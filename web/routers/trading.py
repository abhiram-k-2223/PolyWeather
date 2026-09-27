"""Trading API routes — engine status, order history, and control endpoints.

These routes expose the Polymarket trading engine's state and history
for monitoring and manual intervention. They are read-heavy and
lightweight, but they disclose P&L, positions, and order flow, so every
endpoint requires ops-admin authentication.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from web.services.trading_api import get_store, trading_status

router = APIRouter(prefix="/api/trading", tags=["trading"])


async def _ops_admin(request: Request) -> None:
    # Imported lazily so the lean trading role (app_factory) can serve
    # this router without pulling the legacy web.routes import chain.
    from web.routes import _require_ops_admin

    await run_in_threadpool(_require_ops_admin, request)


@router.get("/status")
async def status(request: Request):
    """Return trading engine health, P&L, and risk status."""
    await _ops_admin(request)
    return trading_status()


@router.get("/orders")
async def orders(request: Request, status: str = "", limit: int = 50):
    """Return trade order history, optionally filtered by status."""
    await _ops_admin(request)
    store = get_store()
    params = {"limit": limit}
    if status:
        params["status"] = status.upper()
    rows = await store.get_orders(**params)
    return {"orders": rows, "count": len(rows)}


@router.get("/signals")
async def signals(request: Request, source: str = "", limit: int = 50):
    """Return signal history, optionally filtered by source."""
    await _ops_admin(request)
    store = get_store()
    params = {"limit": limit}
    if source:
        params["source"] = source
    rows = await store.get_signals(**params)
    return {"signals": rows, "count": len(rows)}


@router.get("/fills")
async def fills(request: Request, limit: int = 50):
    """Return recent fill records."""
    await _ops_admin(request)
    store = get_store()
    # The fills endpoint reuses order reads with matched state for now
    rows = await store.get_orders(status="MATCHED", limit=limit)
    return {"fills": rows, "count": len(rows)}
