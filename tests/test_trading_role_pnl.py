"""Lean trading role + public paper PnL summary (TDD RED).

- POLYWEATHER_SERVICE_ROLE=trading registers only system/trading/paper
  routes (no city/scan/analytics/auth/feedback/sse/ops/legacy).
- GET /api/paper/summary exposes paper P&L with no ops-admin auth.
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


def _route_tags(app):
    # FastAPI >=0.139 keeps include_router() entries as lazy
    # _IncludedRouter wrappers; app.routes carries no tags until
    # resolution. openapi() resolves them — assert on served tags.
    tags = set()
    for item in app.openapi()["paths"].values():
        for op in ("get", "post", "put", "delete", "patch"):
            if op in item:
                tags.update(item[op].get("tags", []))
    return tags


def test_trading_role_registers_only_lean_routes():
    from fastapi import FastAPI
    from web import app_factory

    lean = FastAPI()
    app_factory._register_lean_routers(lean)
    tags = _route_tags(lean)
    assert "trading" in tags
    assert "paper" in tags
    assert "system" in tags
    for heavy in ("city", "scan", "analytics", "auth", "feedback", "events"):
        assert heavy not in tags

    full = FastAPI()
    app_factory._register_full_routers(full)
    full_tags = _route_tags(full)
    assert "trading" in full_tags
    assert "city" in full_tags


def test_paper_summary_shape_uninitialized():
    from web.routers.paper import paper_summary

    out = asyncio.run(paper_summary())
    assert out["initialized"] is False
    assert "paper" in out
    assert out["paper"]["total_pnl_usdc"] == 0.0


def test_paper_summary_reports_live_pnl(monkeypatch):
    import web.services.trading_api as tapi
    from web.routers.paper import paper_summary

    eng = TradingEngine.__new__(TradingEngine)
    from src.trading.engine.position_tracker import PositionTracker
    from src.trading.engine.risk_engine import RiskEngine
    from src.trading.engine.signal_ingestion import SignalIngestor

    eng._config = EngineConfig(paper_mode=True, enabled=True)
    eng._clob = AsyncMock()
    eng._data_api = None
    eng._order_manager = OrderManager(eng._clob, paper_mode=True)
    eng._position_tracker = PositionTracker()
    eng._risk_engine = RiskEngine(eng._config.risk)
    eng._signal_ingestor = SignalIngestor()
    eng._signal_callback = None
    eng._running = True
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
    monkeypatch.setattr(tapi, "_ENGINE", eng)

    sig = TradeSignal(
        condition_id="cond-pnl",
        token_id="tok-pnl",
        direction=SignalDirection.BUY,
        confidence=0.8,
        target_price=0.4,
        source=SignalSource.COMPOSITE,
        metadata={"model_probability": 0.7},
    )
    asyncio.run(eng.process_signal(sig))
    eng.settle_due_paper_positions({"tok-pnl": True})

    out = asyncio.run(paper_summary())
    assert out["initialized"] is True
    assert out["paper_mode"] is True
    assert out["paper"]["settled_trades"] == 1
    assert out["paper"]["total_pnl_usdc"] > 0
