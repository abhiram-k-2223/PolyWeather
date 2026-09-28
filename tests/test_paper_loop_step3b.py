"""Step 3b: async Gamma refresh + in-process paper maintenance loop (TDD RED).

- refresh_market_map_from_gamma must be async and await the real
  GammaClient.resolve_city_markets (sync impl returns an un-awaited
  coroutine and crashes on list()).
- run_paper_maintenance_once(): refresh map + settle closed markets.
- start/stop_paper_loop(): gated on POLY_PAPER_LOOP_ENABLED, daemon thread.
"""

import asyncio
import inspect
import time

from src.trading.engine.signal_ingestion import (
    SignalDirection,
    SignalSource,
    TradeSignal,
)
from src.trading.engine.trading_engine import EngineConfig, TradingEngine
from src.trading.polymarket.gamma_client import GammaMarket


def _market(cond, tokens, *, closed=False, outcomes=None) -> GammaMarket:
    raw = {"outcomePrices": outcomes} if outcomes is not None else {}
    return GammaMarket(
        condition_id=cond,
        clob_token_ids=list(tokens),
        # Models live data: Gamma always sends a question, and the
        # resolver only resolves temperature markets.
        question="High above 70°F today?",
        description="d",
        volume=10.0,
        liquidity=5.0,
        active=not closed,
        closed=closed,
        end_date_iso="",
        neg_risk=True,
        raw=raw,
    )


class _AsyncGamma:
    """Stub matching the real GammaClient async interface."""

    def __init__(self, by_city=None, by_cond=None, fail_resolve=(), fail_market=()):
        self.by_city = by_city or {}
        self.by_cond = by_cond or {}
        self.fail_resolve = set(fail_resolve)
        self.fail_market = set(fail_market)
        self.calls = []

    async def resolve_city_markets(self, city, tag="weather", active_only=True):
        self.calls.append(city)
        if city in self.fail_resolve:
            raise RuntimeError("gamma resolve down")
        return self.by_city.get(city, [])

    async def get_market(self, condition_id):
        if condition_id in self.fail_market:
            raise RuntimeError("gamma market down")
        return self.by_cond.get(condition_id)


def _engine() -> TradingEngine:
    return TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )


def _signal(cond="cond-m", token="tok-m") -> TradeSignal:
    return TradeSignal(
        condition_id=cond,
        token_id=token,
        direction=SignalDirection.BUY,
        confidence=0.8,
        target_price=0.4,
        source=SignalSource.COMPOSITE,
        metadata={"model_probability": 0.7},
    )


def test_refresh_awaits_async_gamma_and_wires_engine(monkeypatch):
    import web.services.trading_api as tapi

    assert inspect.iscoroutinefunction(tapi.refresh_market_map_from_gamma)
    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    eng = _engine()
    client = _AsyncGamma(by_city={"new york": [_market("c-ny", ["t-ny"], closed=False)]})
    got = asyncio.run(
        tapi.refresh_market_map_from_gamma(client=client, engine=eng)
    )
    assert got == {"KLGA": ("c-ny", "t-ny")}
    assert eng._signal_ingestor.get_condition_id("KLGA") == "c-ny"
    assert client.calls == ["new york"]


def test_refresh_skips_city_on_gamma_failure():
    import web.services.trading_api as tapi

    eng = _engine()
    client = _AsyncGamma(by_city={}, fail_resolve={"new york", "los angeles", "chicago"})
    got = asyncio.run(
        tapi.refresh_market_map_from_gamma(client=client, engine=eng)
    )
    assert got == {}


def test_maintenance_once_refreshes_and_settles(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA")
    eng = _engine()
    asyncio.run(eng.process_signal(_signal()))
    client = _AsyncGamma(
        by_city={"new york": [_market("cond-m", ["tok-m", "tok-x"], closed=False)]},
        by_cond={"cond-m": _market("cond-m", ["tok-m", "tok-x"], closed=True, outcomes=["1", "0"])},
    )
    out = asyncio.run(tapi.run_paper_maintenance_once(engine=eng, client=client))
    assert out["market_map"] == {"KLGA": ("cond-m", "tok-m")}
    assert out["settled_tokens"] == ["tok-m"]
    paper = eng.get_status()["paper"]
    assert paper["settled_trades"] == 1
    assert paper["open_positions"] == 0
    assert paper["total_pnl_usdc"] > 0


def test_maintenance_once_survives_gamma_failure():
    import web.services.trading_api as tapi

    eng = _engine()
    asyncio.run(eng.process_signal(_signal()))
    client = _AsyncGamma(fail_market={"cond-m"})
    out = asyncio.run(tapi.run_paper_maintenance_once(engine=eng, client=client))
    assert out["settled_tokens"] == []
    assert len(eng._paper_store.get_open_positions()) == 1


def test_paper_loop_enabled_defaults_true(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.delenv("POLY_PAPER_LOOP_ENABLED", raising=False)
    assert tapi._paper_loop_enabled() is True
    monkeypatch.setenv("POLY_PAPER_LOOP_ENABLED", "0")
    assert tapi._paper_loop_enabled() is False


def test_start_loop_disabled_does_nothing(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_PAPER_LOOP_ENABLED", "0")
    assert tapi.start_paper_loop(tick=lambda: None) is False
    assert tapi._PAPER_LOOP_THREAD is None


def test_start_and_stop_loop_runs_tick():
    import threading

    import web.services.trading_api as tapi

    fired = threading.Event()
    try:
        assert tapi.start_paper_loop(
            tick=fired.set, interval_sec=0.01
        ) is True
        assert fired.wait(timeout=5.0) is True
        assert tapi._PAPER_LOOP_THREAD is not None
    finally:
        tapi.stop_paper_loop()
    time.sleep(0.05)
    assert tapi._PAPER_LOOP_THREAD is None


def test_production_work_reuses_single_open_loop(monkeypatch):
    """Two production ticks must share one open event loop.

    Regression: production_tick() used asyncio.run per tick, so the
    get_shared_client() httpx singleton bound to tick N's loop and
    every later tick failed with "Event loop is closed".
    """
    import web.services.trading_api as tapi

    loops = []

    async def _fake_maintenance(**kwargs):
        loops.append(asyncio.get_running_loop())
        return {"market_map": {}, "feed": {}, "settled_tokens": []}

    monkeypatch.setattr(tapi, "run_paper_maintenance_once", _fake_maintenance)
    try:
        previous = asyncio.get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        tapi._production_work()
        tapi._production_work()
    finally:
        asyncio.set_event_loop(previous)
    assert len(loops) == 2
    assert loops[0] is loops[1]
    assert not loops[0].is_closed()
    loop.close()


def test_refresh_merges_keyword_discovery():
    import web.services.trading_api as tapi
    from src.trading.polymarket.gamma_client import GammaEvent

    eng = _engine()
    tok = _market("tok-c", ["tok-t"], closed=False)
    tok.question = "Will the highest temperature in Tokyo be 19°C on September 29?"
    tok.volume = 500.0
    ev = GammaEvent(
        event_slug="s",
        title="Highest temperature in Tokyo on September 29?",
        markets=[tok],
    )

    class _C(_AsyncGamma):
        async def search_events(self, query, limit=25, page=1):
            return [ev]

    got = asyncio.run(tapi.refresh_market_map_from_gamma(client=_C(), engine=eng))
    assert got.get("tokyo") == ("tok-c", "tok-t")
    assert eng._signal_ingestor.get_condition_id("tokyo") == "tok-c"
