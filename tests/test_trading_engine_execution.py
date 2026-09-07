"""Tests for completed execution paths (problems.md #5)."""

import pytest

from src.trading.engine.order_manager import OrderState, TrackedOrder
from src.trading.engine.signal_ingestion import (
    SignalDirection,
    SignalIngestor,
    SignalSource,
    TradeSignal,
)
from src.trading.engine.trading_engine import EngineConfig, TradingEngine


def _engine():
    eng = TradingEngine.__new__(TradingEngine)
    from src.trading.engine.order_manager import OrderManager
    from src.trading.engine.position_tracker import PositionTracker
    from src.trading.engine.risk_engine import RiskEngine

    eng._config = EngineConfig(
        city_to_market_map={"KJFK": ("cond-1", "tok-1")},
    )
    eng._clob = None  # type: ignore[assignment]
    eng._data_api = None  # type: ignore[assignment]
    eng._order_manager = OrderManager.__new__(OrderManager)
    eng._order_manager._orders = {}
    eng._order_manager._next_id = 0
    eng._order_manager._storage = None
    eng._order_manager._clob = None  # type: ignore[assignment]
    eng._order_manager.on_order_closed = None
    eng._position_tracker = PositionTracker()
    eng._risk_engine = RiskEngine(eng._config.risk)
    eng._signal_ingestor = SignalIngestor()
    eng._signal_callback = None
    eng._running = False
    eng._loop_task = None
    eng._last_reconcile = 0.0
    eng._cached_cash = None
    eng._stats = {
        "signals_processed": 0,
        "orders_placed": 0,
        "orders_failed": 0,
        "trades_executed": 0,
        "started_at": None,
    }
    for icao, (c, t) in eng._config.city_to_market_map.items():
        eng._signal_ingestor.register_market(icao, c, t)
    eng._order_manager.on_order_closed = eng._on_order_closed
    return eng


def _signal(
    condition_id: str = "cond-1",
    token_id: str = "",
    direction: SignalDirection = SignalDirection.BUY,
    confidence: float = 0.8,
    target_price: float = 0.4,
    metadata: dict | None = None,
) -> TradeSignal:
    return TradeSignal(
        condition_id=condition_id,
        token_id=token_id,
        direction=direction,
        confidence=confidence,
        target_price=target_price,
        source=SignalSource.COMPOSITE,
        metadata={"model_probability": 0.7} if metadata is None else metadata,
    )


def test_token_resolution_uses_registered_map():
    eng = _engine()
    assert eng._resolve_token_id("cond-1") == "tok-1"
    assert eng._resolve_token_id("unknown") == ""


def test_signal_ingestor_token_registry():
    ing = SignalIngestor()
    ing.register_market("KJFK", "c1", "t1")
    assert ing.get_token_id("c1") == "t1"
    assert ing.get_token_id("missing") == ""


def test_empty_token_signal_rejected_without_order():
    import asyncio

    eng = _engine()
    sig = _signal(condition_id="unmapped", token_id="")
    assert asyncio.run(eng.process_signal(sig)) is None
    assert eng._stats["orders_failed"] == 1


def test_kelly_sizing_positive_edge_and_zero_without():
    eng = _engine()
    assert eng._compute_position_size(_signal()) > 0
    # No edge: model prob == price -> Kelly 0.
    sig = _signal(target_price=0.7, metadata={"model_probability": 0.7})
    assert eng._compute_position_size(sig) == 0.0


def test_portfolio_value_uses_paper_base_then_cached_cash():
    eng = _engine()
    assert eng._estimate_portfolio_value() == 10000.0
    eng._cached_cash = 5000.0
    assert eng._estimate_portfolio_value() == 5000.0


def test_settle_matched_position_records_real_pnl_and_cooldown():
    eng = _engine()
    order = TrackedOrder(
        local_id="l1",
        order_id="o1",
        condition_id="cond-1",
        token_id="tok-1",
        side="BUY",
        price=0.4,
        size=100.0,
        state=OrderState.MATCHED,
        filled_size=100.0,
        avg_fill_price=0.4,
    )
    eng._on_order_closed(order)
    assert eng._position_tracker.get_position("tok-1") is not None
    realized = eng.settle_matched_position("tok-1", payout_per_share=0.0)
    assert realized == pytest.approx(-40.0)
    # Loss cooldown now engages.
    assessment = eng._risk_engine.assess(
        signal_confidence=0.9,
        position_size=10.0,
        open_orders=[],
        total_portfolio_value=10000.0,
    )
    assert assessment.allowed is False
    assert "cooldown" in assessment.reason
