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


def test_risk_slippage_and_depth_checks_opt_in():
    from src.trading.engine.risk_engine import RiskConfig, RiskEngine

    # Legacy default: unconfigured slippage model never rejects.
    eng = RiskEngine(RiskConfig())
    ok = eng.assess(
        signal_confidence=0.9, position_size=400.0, open_orders=[],
        total_portfolio_value=10000.0,
    )
    assert ok.allowed is True
    # Configured linear model rejects an order past slippage tolerance
    # (400 USDC stays under the $500 position cap — only slippage blocks).
    hot = RiskEngine(RiskConfig(slippage_bps_per_100usd=20.0, max_slippage_bps=50))
    assert hot.estimate_slippage_bps(400.0) == 80.0
    blocked = hot.assess(
        signal_confidence=0.9, position_size=400.0, open_orders=[],
        total_portfolio_value=10000.0,
    )
    assert blocked.allowed is False and "slippage" in blocked.reason
    # Thin-book guard rejects only when live depth is supplied.
    thin = RiskEngine(RiskConfig(min_orderbook_depth_usd=100.0))
    assert thin.assess(
        signal_confidence=0.9, position_size=10.0, open_orders=[],
        total_portfolio_value=10000.0, orderbook_depth_usd=15.0,
    ).allowed is False
    assert thin.assess(
        signal_confidence=0.9, position_size=10.0, open_orders=[],
        total_portfolio_value=10000.0, orderbook_depth_usd=500.0,
    ).allowed is True


def test_backtester_slippage_worsens_fills_and_defaults_off():
    from scripts.backtester.base import BacktestConfig, BacktestRecord
    from scripts.backtester.engine import (
        apply_slippage_to_price, compute_slippage_fraction, run_backtest,
    )
    from scripts.backtester.strategies.forecast_gap import ForecastGapStrategy

    assert compute_slippage_fraction(200.0, 0) == 0.0
    # 10bps per $100 on a $200 order = 20bps = 0.002 price fraction.
    assert compute_slippage_fraction(200.0, 10) == 0.002
    assert apply_slippage_to_price(0.5, 200.0, 0) == 0.5
    assert apply_slippage_to_price(0.5, 200.0, 10) > 0.5
    # Square-root impact charges less than linear for small vs depth.
    lin = compute_slippage_fraction(50.0, 10)
    sqrt = compute_slippage_fraction(50.0, 10, orderbook_depth_usd=10_000.0)
    assert 0 < sqrt < lin

    recs = [
        BacktestRecord(city="new york", target_date=f"2026-01-0{d}",
                       model_probability=0.9, market_price=0.05,
                       actual_outcome=1.0)
        for d in range(1, 4)
    ]
    plain = run_backtest(
        list(recs), ForecastGapStrategy(BacktestConfig()), BacktestConfig(),
    )
    slipped = run_backtest(
        list(recs),
        ForecastGapStrategy(BacktestConfig(slippage_bps=100)),
        BacktestConfig(slippage_bps=100),
    )
    assert plain.trades and slipped.trades
    # Default config keeps legacy point-price fills.
    assert plain.trades[0].entry_price == 0.05
    assert plain.trades[0].metadata.get("slippage_bps_charged") == 0.0
    # Enabled slippage pays up and earns fewer tokens per USDC.
    assert slipped.trades[0].entry_price > 0.05
    assert slipped.trades[0].size_tokens < plain.trades[0].size_tokens


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
