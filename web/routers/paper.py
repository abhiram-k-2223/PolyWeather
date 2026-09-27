"""Public paper-trading summary — read-only PnL with no ops-admin auth.

Unlike ``/api/trading/*`` (order control + position detail, gated),
this endpoint exposes only aggregate paper stats: mode, engine state,
counters, win rate, and simulated P&L. Safe to query directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/paper", tags=["paper"])

_ZERO_PAPER = {
    "total_trades": 0,
    "open_positions": 0,
    "settled_trades": 0,
    "wins": 0,
    "losses": 0,
    "win_rate": 0.0,
    "total_pnl_usdc": 0.0,
    "avg_pnl_per_trade": 0.0,
    "total_volume_usdc": 0.0,
}


@router.get("/summary")
async def paper_summary() -> dict[str, Any]:
    """Return paper mode, engine state, and aggregate paper P&L."""
    from web.services.trading_api import _is_paper_mode, get_engine

    engine = get_engine()
    if engine is None:
        return {
            "initialized": False,
            "paper_mode": _is_paper_mode(),
            "running": False,
            "enabled": False,
            "stats": {},
            "paper": dict(_ZERO_PAPER),
        }
    status = engine.get_status()
    return {
        "initialized": True,
        "paper_mode": bool(status.get("paper_mode", False)),
        "running": bool(status.get("running", False)),
        "enabled": bool(status.get("enabled", False)),
        "stats": dict(status.get("stats", {})),
        "paper": dict(status.get("paper", _ZERO_PAPER)),
    }
