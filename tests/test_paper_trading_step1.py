"""Step 1 paper-mode execution branch (TDD RED).

Covers PAPER_TRADING_READINESS.md Step 1 acceptance:
- EngineConfig.paper_mode defaults False (backwards compat)
- OrderManager paper branch never touches CLOB, returns OPEN with paper id
- TradingEngine paper path logs to PaperTradeStore, settles with correct P&L,
  routes cancel, exposes paper stats, counts MATCHED/OPEN as placed
- web _build_engine_config defaults paper True, init allows no-key paper
"""

import asyncio
from unittest.mock import AsyncMock

from src.trading.engine.order_manager import OrderManager, OrderState
from src.trading.engine.paper_trade_store import PaperTradeStore
from src.trading.engine.signal_ingestion import (
    SignalDirection,
    SignalSource,
    TradeSignal,
)
from src.trading.engine.trading_engine import EngineConfig, TradingEngine


def _signal(**kw) -> TradeSignal:
    base = dict(
        condition_id="cond-paper-1",
        token_id="tok-paper-1",
        direction=SignalDirection.BUY,
        confidence=0.8,
        target_price=0.4,
        source=SignalSource.COMPOSITE,
        metadata={"model_probability": 0.7},
    )
    base.update(kw)
    return TradeSignal(**base)


def test_engine_config_paper_mode_defaults_off():
    assert EngineConfig().paper_mode is False


def test_order_manager_paper_branch_skips_clob():
    clob = AsyncMock()
    clob.place_order = AsyncMock()
    mgr = OrderManager(clob, paper_mode=True)
    order = asyncio.run(
        mgr.place_order(
            condition_id="c1", token_id="t1", side="BUY", price=0.4, size=50.0
        )
    )
    assert order.state == OrderState.OPEN
    assert order.order_id is not None and order.order_id.startswith("paper_")
    clob.place_order.assert_not_called()


def test_order_manager_paper_cancel_and_reconcile_no_clob():
    clob = AsyncMock()
    clob.cancel_order = AsyncMock()
    clob.get_orders = AsyncMock()
    mgr = OrderManager(clob, paper_mode=True)
    order = asyncio.run(
        mgr.place_order(
            condition_id="c1", token_id="t1", side="BUY", price=0.4, size=10.0
        )
    )
    assert asyncio.run(mgr.cancel_order(order.local_id)) is True
    assert mgr.get_order(order.local_id).state == OrderState.CANCELLED
    clob.cancel_order.assert_not_called()
    assert asyncio.run(mgr.reconcile()) == 0
    clob.get_orders.assert_not_called()


def test_engine_paper_signal_end_to_end_no_clob_writes():
    clob = AsyncMock()
    clob.place_order = AsyncMock(side_effect=AssertionError("live CLOB touched"))
    store = PaperTradeStore()
    eng = TradingEngine.__new__(TradingEngine)
    # Minimal ctor-equivalent wiring with paper_mode on.
    from src.trading.engine.position_tracker import PositionTracker
    from src.trading.engine.risk_engine import RiskEngine
    from src.trading.engine.signal_ingestion import SignalIngestor

    eng._config = EngineConfig(paper_mode=True)
    eng._clob = clob
    eng._data_api = None
    eng._order_manager = OrderManager(clob, paper_mode=True)
    eng._position_tracker = PositionTracker()
    eng._risk_engine = RiskEngine(eng._config.risk)
    eng._signal_ingestor = SignalIngestor()
    eng._signal_callback = None
    eng._running = False
    eng._loop_task = None
    eng._last_reconcile = 0.0
    eng._cached_cash = None
    eng._paper_store = store
    eng._stats = {
        "signals_processed": 0,
        "orders_placed": 0,
        "orders_failed": 0,
        "trades_executed": 0,
        "started_at": None,
    }
    eng._order_manager.on_order_closed = eng._on_order_closed

    order = asyncio.run(eng.process_signal(_signal()))
    assert order is not None
    assert order.state in (OrderState.OPEN, OrderState.MATCHED)
    clob.place_order.assert_not_called()
    assert eng._stats["orders_placed"] == 1
    assert len(store.get_open_positions()) == 1

    # Settlement math: BUY 0.4, size from Kelly>0; win pays (1-0.4)*size.
    rec = store.get_open_positions()[0]
    settled = eng.settle_paper_position(rec.token_id, won=True)
    assert settled is not None
    assert settled.status == "SETTLED_WON"
    assert settled.simulated_pnl == (1.0 - rec.price) * rec.size
    stats = eng.get_status()["paper"]
    assert stats["settled_trades"] == 1
    assert stats["total_pnl_usdc"] == round(settled.simulated_pnl, 2)


def test_engine_paper_settlement_loss_and_cancel():
    store = PaperTradeStore()
    eng = TradingEngine.__new__(TradingEngine)
    from src.trading.engine.order_manager import OrderManager as OM
    from src.trading.engine.position_tracker import PositionTracker
    from src.trading.engine.risk_engine import RiskEngine
    from src.trading.engine.signal_ingestion import SignalIngestor

    eng._config = EngineConfig(paper_mode=True)
    eng._clob = AsyncMock()
    eng._data_api = None
    eng._order_manager = OM(eng._clob, paper_mode=True)
    eng._position_tracker = PositionTracker()
    eng._risk_engine = RiskEngine(eng._config.risk)
    eng._signal_ingestor = SignalIngestor()
    eng._signal_callback = None
    eng._running = False
    eng._loop_task = None
    eng._last_reconcile = 0.0
    eng._cached_cash = None
    eng._paper_store = store
    eng._stats = {
        "signals_processed": 0,
        "orders_placed": 0,
        "orders_failed": 0,
        "trades_executed": 0,
        "started_at": None,
    }
    eng._order_manager.on_order_closed = eng._on_order_closed

    order = asyncio.run(eng.process_signal(_signal(token_id="tok-loss")))
    # Cancel path frees both OrderManager and paper store.
    assert asyncio.run(eng.cancel_paper_order(order.local_id)) is True
    assert store.get_open_positions() == []
    # Re-place then lose.
    asyncio.run(eng.process_signal(_signal(token_id="tok-loss2")))
    rec2 = store.get_open_positions()[0]
    lost = eng.settle_paper_position(rec2.token_id, won=False)
    assert lost.status == "SETTLED_LOST"
    assert lost.simulated_pnl == -rec2.price * rec2.size


def test_web_paper_mode_defaults_true_and_status_surface(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.delenv("POLY_PAPER_MODE", raising=False)
    assert tapi._is_paper_mode() is True
    monkeypatch.setenv("POLY_PAPER_MODE", "0")
    assert tapi._is_paper_mode() is False
    monkeypatch.setenv("POLY_PAPER_MODE", "1")
    assert tapi._is_paper_mode() is True

    # _build_engine_config passes paper through.
    cfg = tapi._build_engine_config()
    assert cfg.paper_mode is True
