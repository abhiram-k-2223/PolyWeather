"""Step 3 settlement loop (TDD RED).

Covers PAPER_TRADING_READINESS.md Step 3 acceptance:
- Closed-market resolution maps to paper win/loss without fabricating wins
- Batch settle settles only resolved positions with correct P&L math
- Settled loss triggers risk cooldown; status payload exposes paper P&L
- Per-market Gamma failure never blocks the rest of the loop
"""

import asyncio
from unittest.mock import AsyncMock

from src.trading.engine.order_manager import OrderManager
from src.trading.engine.paper_trade_store import PaperTradeStore
from src.trading.engine.signal_ingestion import (
    SignalDirection,
    SignalSource,
    TradeSignal,
)
from src.trading.engine.trading_engine import EngineConfig, TradingEngine
from src.trading.polymarket.gamma_client import GammaMarket
from src.trading.polymarket.market_resolution import resolve_token_outcome


def _market(token_ids, *, closed=False, outcomes=None) -> GammaMarket:
    raw: dict = {}
    if outcomes is not None:
        raw["outcomePrices"] = outcomes
    return GammaMarket(
        condition_id="cond-1",
        clob_token_ids=list(token_ids),
        question="q",
        description="d",
        volume=100.0,
        liquidity=50.0,
        active=not closed,
        closed=closed,
        end_date_iso="",
        neg_risk=True,
        raw=raw,
    )


def test_resolve_unresolved_when_market_open():
    m = _market(["tYes", "tNo"], closed=False, outcomes=["1", "0"])
    assert resolve_token_outcome(m, "tYes") is None


def test_resolve_win_and_loss_by_token_index():
    m = _market(["tYes", "tNo"], closed=True, outcomes=["1", "0"])
    assert resolve_token_outcome(m, "tYes") is True
    assert resolve_token_outcome(m, "tNo") is False


def test_resolve_unknown_token_or_ambiguous_is_none():
    m = _market(["tYes", "tNo"], closed=True, outcomes=["1", "0"])
    assert resolve_token_outcome(m, "tMissing") is None
    m2 = _market(["tYes", "tNo"], closed=True, outcomes=None)
    assert resolve_token_outcome(m2, "tYes") is None


def _engine() -> TradingEngine:
    eng = TradingEngine.__new__(TradingEngine)
    from src.trading.engine.position_tracker import PositionTracker
    from src.trading.engine.risk_engine import RiskEngine
    from src.trading.engine.signal_ingestion import SignalIngestor

    eng._config = EngineConfig(paper_mode=True)
    eng._clob = AsyncMock()
    eng._data_api = None
    eng._order_manager = OrderManager(eng._clob, paper_mode=True)
    eng._position_tracker = PositionTracker()
    eng._risk_engine = RiskEngine(eng._config.risk)
    eng._signal_ingestor = SignalIngestor()
    eng._signal_callback = None
    eng._running = False
    eng._loop_task = None
    eng._last_reconcile = 0.0
    eng._cached_cash = None
    eng._paper_store = PaperTradeStore()
    eng._stats = {
        "signals_processed": 0,
        "orders_placed": 0,
        "orders_failed": 0,
        "trades_executed": 0,
        "started_at": None,
    }
    eng._order_manager.on_order_closed = eng._on_order_closed
    return eng


def _signal(token_id: str) -> TradeSignal:
    return TradeSignal(
        condition_id="cond-1",
        token_id=token_id,
        direction=SignalDirection.BUY,
        confidence=0.8,
        target_price=0.4,
        source=SignalSource.COMPOSITE,
        metadata={"model_probability": 0.7},
    )


def test_settle_due_batch_win_loss_and_skip():
    eng = _engine()
    asyncio.run(eng.process_signal(_signal("tokA")))
    asyncio.run(eng.process_signal(_signal("tokB")))
    recs = {r.token_id: r for r in eng._paper_store.get_open_positions()}
    settled = eng.settle_due_paper_positions({"tokA": True, "tokB": False})
    assert {r.token_id for r in settled} == {"tokA", "tokB"}
    assert settled[0].simulated_pnl == (1.0 - recs["tokA"].price) * recs["tokA"].size
    assert settled[1].simulated_pnl == -recs["tokB"].price * recs["tokB"].size
    assert eng._paper_store.get_open_positions() == []
    # Unknown token ids are left untouched, never fabricated.
    assert eng.settle_due_paper_positions({"tokGhost": True}) == []


def test_settled_loss_triggers_risk_cooldown():
    eng = _engine()
    asyncio.run(eng.process_signal(_signal("tokL")))
    eng.settle_due_paper_positions({"tokL": False})
    blocked = eng._risk_engine.assess(
        signal_confidence=0.9,
        position_size=10.0,
        open_orders=[],
        total_portfolio_value=10000.0,
    )
    assert blocked.allowed is False
    assert "cooldown" in blocked.reason


def test_status_paper_payload_shape_after_full_cycle():
    eng = _engine()
    asyncio.run(eng.process_signal(_signal("tokS")))
    eng.settle_due_paper_positions({"tokS": True})
    paper = eng.get_status()["paper"]
    for key in ("open_positions", "settled_trades", "win_rate", "total_pnl_usdc"):
        assert key in paper
    assert paper["open_positions"] == 0
    assert paper["settled_trades"] == 1
    assert paper["win_rate"] == 1.0
    assert paper["total_pnl_usdc"] > 0


def test_check_and_settle_skips_failed_market():
    import web.services.trading_api as tapi

    eng = _engine()
    asyncio.run(eng.process_signal(_signal("tokOK")))
    asyncio.run(eng.process_signal(_signal("tokFail")))
    # Patch store condition ids so the stub client can distinguish them.
    for r in eng._paper_store.get_open_positions():
        r.condition_id = f"cond-{r.token_id}"

    ok_market = _market(["tokOK", "tokOther"], closed=True, outcomes=["1", "0"])

    async def fake_get_markets(condition_ids=None):
        if (condition_ids or []) == ["cond-tokOK"]:
            return [ok_market]
        raise RuntimeError("gamma down")

    client = AsyncMock()
    client.get_markets = fake_get_markets
    settled = asyncio.run(tapi.check_and_settle_closed_markets(eng, client))
    assert [r.token_id for r in settled] == ["tokOK"]
    assert settled[0].status == "SETTLED_WON"
    remaining = [r.token_id for r in eng._paper_store.get_open_positions()]
    assert remaining == ["tokFail"]


def test_check_and_settle_uses_condition_ids_query():
    """Settlement must resolve via /markets?condition_ids= — the
    /markets/{condition_id} path form 422s live (same class as the Step 4
    provider bug). A client exposing only get_markets must still settle."""
    import web.services.trading_api as tapi

    eng = _engine()
    asyncio.run(eng.process_signal(_signal("tokQ")))
    for r in eng._paper_store.get_open_positions():
        r.condition_id = "cond-tokQ"

    won_market = _market(["tokQ", "tokOther"], closed=True, outcomes=["1", "0"])
    won_market.condition_id = "cond-tokQ"

    seen: list = []

    class _QueryOnly:
        async def get_markets(self, condition_ids=None):
            seen.append(list(condition_ids or []))
            assert (condition_ids or []) == ["cond-tokQ"]
            return [won_market]

    settled = asyncio.run(tapi.check_and_settle_closed_markets(eng, _QueryOnly()))
    assert seen == [["cond-tokQ"]]
    assert [r.token_id for r in settled] == ["tokQ"]
    assert settled[0].status == "SETTLED_WON"
