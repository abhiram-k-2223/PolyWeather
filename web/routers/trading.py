"""Trading API routes — engine status, order history, and control endpoints.

These routes expose the Polymarket trading engine's state and history
for monitoring and manual intervention. They are read-heavy and
lightweight.
"""

from __future__ import annotations

from fastapi import APIRouter

from web.services.trading_api import get_store, trading_status

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status")
async def status():
    """Return trading engine health, P&L, and risk status."""
    return trading_status()


@router.get("/orders")
async def orders(status: str = "", limit: int = 50):
    """Return trade order history, optionally filtered by status."""
    store = get_store()
    params = {"limit": limit}
    if status:
        params["status"] = status.upper()
    rows = await store.get_orders(**params)
    return {"orders": rows, "count": len(rows)}


@router.get("/signals")
async def signals(source: str = "", limit: int = 50):
    """Return signal history, optionally filtered by source."""
    store = get_store()
    params = {"limit": limit}
    if source:
        params["source"] = source
    rows = await store.get_signals(**params)
    return {"signals": rows, "count": len(rows)}


@router.get("/fills")
async def fills(limit: int = 50):
    """Return recent fill records."""
    store = get_store()
    # The fills endpoint reuses order reads with matched state for now
    rows = await store.get_orders(status="MATCHED", limit=limit)
    return {"fills": rows, "count": len(rows)}
