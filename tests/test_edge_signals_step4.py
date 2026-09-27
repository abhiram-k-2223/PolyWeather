"""Step 4: DEB-driven edge signals (TDD RED).

- SignalIngestor.ingest_forecast_gap: edge = model_p - market_price,
  mirrors ForecastGapStrategy (edge>threshold, max_price_for_buy,
  two_sided SELL). Priced at the market, metadata carries
  model_probability/market_price/gap for Kelly + PaperTradeStore.
- trading_api.run_signal_feed_once: per feed city, live midpoint price +
  probability provider -> edge signal -> engine.process_signal.
  Paper-only unless POLY_LIVE_SIGNALS=1.
"""

import asyncio
from unittest.mock import MagicMock

from src.trading.engine.signal_ingestion import SignalDirection, SignalIngestor
from src.trading.engine.trading_engine import EngineConfig, TradingEngine


def _ingestor() -> SignalIngestor:
    ing = SignalIngestor()
    ing.register_market("KLGA", "c-ny", "t-ny")
    return ing


def test_edge_buy_above_threshold_priced_at_market():
    ing = _ingestor()
    sig = ing.ingest_forecast_gap("KLGA", 0.70, 0.05)
    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.target_price == 0.05
    assert sig.metadata["model_probability"] == 0.70
    assert sig.metadata["market_price"] == 0.05
    assert sig.metadata["gap"] == 0.70 - 0.05
    assert sig.confidence >= 0.6


def test_no_edge_returns_none():
    ing = _ingestor()
    assert ing.ingest_forecast_gap("KLGA", 0.50, 0.48) is None


def test_negative_edge_one_sided_returns_none():
    ing = _ingestor()
    assert ing.ingest_forecast_gap("KLGA", 0.30, 0.90) is None


def test_negative_edge_two_sided_sells_at_market():
    ing = _ingestor()
    sig = ing.ingest_forecast_gap("KLGA", 0.30, 0.90, two_sided=True)
    assert sig is not None
    assert sig.direction == SignalDirection.SELL
    assert sig.target_price == 0.90
    assert sig.metadata["gap"] == 0.30 - 0.90


def test_buy_blocked_above_max_price():
    ing = _ingestor()
    assert ing.ingest_forecast_gap("KLGA", 0.90, 0.50) is None


def test_invalid_inputs_return_none():
    ing = _ingestor()
    assert ing.ingest_forecast_gap("KLGA", 0.70, None) is None
    assert ing.ingest_forecast_gap("KLGA", 0.70, 0.0) is None
    assert ing.ingest_forecast_gap("KLGA", 0.70, 1.5) is None
    assert ing.ingest_forecast_gap("KLGA", -0.1, 0.05) is None
    assert ing.ingest_forecast_gap("KLGA", 1.2, 0.05) is None
    assert ing.ingest_forecast_gap("XXXX", 0.70, 0.05) is None


def test_kelly_size_matches_signal_edge():
    from src.trading.engine.kelly_sizing import compute_kelly_position_size

    ing = _ingestor()
    sig = ing.ingest_forecast_gap("KLGA", 0.70, 0.05)
    assert sig is not None
    bankroll, cap = 2000.0, 500.0
    expect = compute_kelly_position_size(0.70, 0.05, bankroll, cap)
    assert expect > 0
    eng = TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )
    eng._cached_cash = bankroll
    assert eng._compute_position_size(sig) == expect


class _FeedGamma:
    """Stub matching GammaClient price interface (async)."""

    def __init__(self, price=None):
        self.price = price
        self.calls = []

    async def get_midpoint_price(self, condition_id, token_id):
        self.calls.append((condition_id, token_id))
        return self.price

    async def get_best_price(self, condition_id, token_id, side="BUY"):
        return self.price


def _paper_engine() -> TradingEngine:
    eng = TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )
    eng.update_market_map({"KLGA": ("c-ny", "t-ny")})
    return eng


def test_feed_places_paper_order_on_edge(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    eng = _paper_engine()
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            client=_FeedGamma(price=0.05),
            probability_provider=lambda icao, city, cond, tok: 0.70,
        )
    )
    assert out["signals"] == 1
    assert out["orders"] == 1
    assert eng._stats["orders_placed"] == 1
    assert len(eng._paper_store.get_open_positions()) == 1


def test_feed_skips_without_provider(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    eng = _paper_engine()
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng, client=_FeedGamma(price=0.05), probability_provider=None
        )
    )
    assert out["signals"] == 0 and out["orders"] == 0
    assert eng._stats["orders_placed"] == 0


def test_feed_skips_without_price(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    eng = _paper_engine()
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            client=_FeedGamma(price=None),
            probability_provider=lambda icao, city, cond, tok: 0.70,
        )
    )
    assert out["signals"] == 0 and out["orders"] == 0


def test_feed_blocked_live_without_opt_in(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    monkeypatch.delenv("POLY_LIVE_SIGNALS", raising=False)
    eng = TradingEngine(
        wallet=MagicMock(), config=EngineConfig(enabled=True, paper_mode=False)
    )
    eng.update_market_map({"KLGA": ("c-ny", "t-ny")})
    seen = []
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            client=_FeedGamma(price=0.05),
            probability_provider=lambda icao, city, cond, tok: seen.append(icao) or 0.70,
        )
    )
    assert out["signals"] == 0 and seen == []


def test_feed_live_opt_in_reaches_provider(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    monkeypatch.setenv("POLY_LIVE_SIGNALS", "1")
    eng = TradingEngine(
        wallet=MagicMock(), config=EngineConfig(enabled=True, paper_mode=False)
    )
    eng.update_market_map({"KLGA": ("c-ny", "t-ny")})
    # No edge at equal price -> provider called, but no order, no network.
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            client=_FeedGamma(price=0.50),
            probability_provider=lambda icao, city, cond, tok: 0.50,
        )
    )
    assert out["signals"] == 0 and out["orders"] == 0


def test_maintenance_tick_includes_feed(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    eng = _paper_engine()
    out = asyncio.run(
        tapi.run_paper_maintenance_once(
            engine=eng,
            client=_FeedGamma(price=0.05),
            probability_provider=lambda icao, city, cond, tok: 0.70,
        )
    )
    assert out["feed"]["orders"] == 1
