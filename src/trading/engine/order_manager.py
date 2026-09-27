"""Order management — handles the full lifecycle of Polymarket orders.

Responsible for order placement, cancellation, tracking, and
reconciliation with the CLOB API. Works closely with the
CLOBClient and the trade storage layer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from src.trading.polymarket.clob_client import CLOBClient

logger = logging.getLogger(__name__)


class OrderState(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    # Order vanished from the remote OPEN list but fills could not be
    # verified. Excluded from exposure and P&L until confirmed.
    CLOSED_UNVERIFIED = "CLOSED_UNVERIFIED"


@dataclass
class TrackedOrder:
    """An order being tracked by the engine.

    Tracks both the local intent and the CLOB API state.
    """

    local_id: str
    order_id: Optional[str]  # CLOB-assigned order ID (None until placed)
    condition_id: str
    token_id: str
    side: str  # BUY or SELL
    price: float
    size: float
    state: OrderState
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    matched_at: Optional[datetime] = None
    filled_size: float = 0.0
    avg_fill_price: Optional[float] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class OrderManager:
    """Manages order lifecycle — create, cancel, track, and reconcile.

    This is the single point of control for all CLOB orders.
    It maintains a local in-memory order book (synced to storage)
    and drives the CLOBClient for execution.

    Usage:
        mgr = OrderManager(clob_client)
        order = await mgr.place_order(
            condition_id="...",
            token_id="...",
            side="BUY",
            price=0.55,
            size=100.0,
        )
        await mgr.cancel_order(order.local_id)
    """

    def __init__(
        self, clob_client: Optional[CLOBClient], storage: Optional[Any] = None,
        *, paper_mode: bool = False,
    ) -> None:
        self._clob = clob_client
        self._storage = storage  # optional TradeStore for persistence
        self._paper_mode = paper_mode
        self._orders: dict[str, TrackedOrder] = {}  # local_id -> order
        self._next_id: int = 0
        # Optional hook invoked when an order reaches a terminal state with
        # verified outcome (MATCHED or CANCELLED). The TradingEngine wires
        # this to risk accounting so daily limits/cooldowns actually fire.
        self.on_order_closed: Optional[Any] = None

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    async def place_order(
        self,
        condition_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        *,
        neg_risk: bool = True,
        metadata: Optional[dict] = None,
    ) -> TrackedOrder:
        """Place an order on the CLOB and track it locally.

        Returns the TrackedOrder with state=PENDING. After a successful
        API response, state transitions to OPEN.
        """
        local_id = self._next_local_id()
        order = TrackedOrder(
            local_id=local_id,
            order_id=None,
            condition_id=condition_id,
            token_id=token_id,
            side=side.upper(),
            price=price,
            size=size,
            state=OrderState.PENDING,
            metadata=metadata or {},
        )
        self._orders[local_id] = order

        # Paper mode: never touch the CLOB. Simulate an immediately
        # resting OPEN order with a local paper id — no network writes.
        if self._paper_mode:
            order.order_id = f"paper_{local_id}"
            order.state = OrderState.OPEN
            order.metadata = {**(order.metadata or {}), "paper": True}
            logger.info(
                "Paper order %s: %s %s %.4f @ %.4f (no CLOB write)",
                local_id, side, token_id[:10], size, price,
            )
            if self._storage:
                await self._storage.save_order(order)
            return order

        try:
            clob_order = self._build_clob_order(order, neg_risk=neg_risk)
            result = await self._clob.place_order(clob_order)
            order.order_id = result.get("order_id") or result.get("id")
            order.state = OrderState.OPEN
            logger.info(
                "Order %s placed: %s %s %.4f @ %.2f (CLOB ID: %s)",
                local_id,
                side,
                token_id[:10],
                size,
                price,
                order.order_id,
            )
        except Exception as exc:
            order.state = OrderState.FAILED
            order.error = str(exc)
            logger.error("Failed to place order %s: %s", local_id, exc)

        if self._storage:
            await self._storage.save_order(order)

        return order

    async def cancel_order(self, local_id: str) -> bool:
        """Cancel a tracked order by its local ID."""
        order = self._orders.get(local_id)
        if not order:
            logger.warning("Order %s not found", local_id)
            return False
        if self._paper_mode:
            order.state = OrderState.CANCELLED
            logger.info("Paper order %s cancelled (no CLOB write)", local_id)
            if self._storage:
                await self._storage.save_order(order)
            return True
        if not order.order_id:
            order.state = OrderState.CANCELLED
            return True

        try:
            await self._clob.cancel_order(order.order_id)
            order.state = OrderState.CANCELLED
            logger.info("Order %s cancelled", local_id)
            if self._storage:
                await self._storage.save_order(order)
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", local_id, exc)
            return False

    async def cancel_all(self) -> int:
        """Cancel all tracked open orders. Returns the count cancelled."""
        open_orders = [
            o for o in self._orders.values()
            if o.state in (OrderState.PENDING, OrderState.OPEN)
        ]
        for o in open_orders:
            await self.cancel_order(o.local_id)
        return len(open_orders)

    # ------------------------------------------------------------------
    # Reconciliation (sync with CLOB)
    # ------------------------------------------------------------------

    async def reconcile(self) -> int:
        """Fetch open orders from the CLOB and update local state.

        An order missing from the remote OPEN list may have been filled,
        cancelled, or expired. Verify via fills before claiming MATCHED;
        if fills cannot be fetched, mark CLOSED_UNVERIFIED instead of
        fabricating a win.

        Returns the number of mismatches found and corrected.
        """
        # Paper mode has no remote book — nothing to reconcile, no reads.
        if self._paper_mode:
            return 0
        try:
            remote = await self._clob.get_orders(status="OPEN")
        except Exception as exc:
            logger.error("Failed to reconcile: %s", exc)
            return 0

        mismatches = 0
        remote_orders = remote.get("data", [])
        remote_ids = {o.get("id") for o in remote_orders if o.get("id")}

        vanished = [
            local_order
            for local_order in self._orders.values()
            if local_order.order_id
            and local_order.order_id not in remote_ids
            and local_order.state == OrderState.OPEN
        ]
        if not vanished:
            return 0

        fills_by_token_side: dict[tuple[str, str], list[dict[str, Any]]] = {}
        fills_fetch_failed = False
        try:
            fills_response = await self._clob.get_fills(limit=100)
            for fill in fills_response.get("data", []):
                key = (
                    str(fill.get("asset_id") or fill.get("token_id") or ""),
                    str(fill.get("side") or "").upper(),
                )
                fills_by_token_side.setdefault(key, []).append(fill)
        except Exception as exc:
            logger.warning(
                "Reconciliation could not fetch fills (%s); "
                "vanished orders will be marked CLOSED_UNVERIFIED",
                exc,
            )
            fills_fetch_failed = True

        for local_order in vanished:
            now = datetime.now(timezone.utc)
            if fills_fetch_failed:
                local_order.state = OrderState.CLOSED_UNVERIFIED
                local_order.error = "removed from CLOB; fill verification unavailable"
                logger.warning(
                    "Order %s no longer open — marked CLOSED_UNVERIFIED",
                    local_order.local_id,
                )
            else:
                matched_fills = fills_by_token_side.get(
                    (local_order.token_id, local_order.side), []
                )
                if matched_fills:
                    total_size = sum(
                        float(f.get("size") or 0.0) for f in matched_fills
                    )
                    notional = sum(
                        float(f.get("size") or 0.0) * float(f.get("price") or 0.0)
                        for f in matched_fills
                    )
                    local_order.state = OrderState.MATCHED
                    local_order.matched_at = now
                    local_order.filled_size = total_size
                    local_order.avg_fill_price = (
                        notional / total_size if total_size > 0 else None
                    )
                    logger.info(
                        "Order %s verified MATCHED: %.4f @ %s",
                        local_order.local_id,
                        total_size,
                        local_order.avg_fill_price,
                    )
                else:
                    # Gone from OPEN with zero fills — cancelled or expired.
                    local_order.state = OrderState.CANCELLED
                    logger.info(
                        "Order %s no longer open with no fills — marked CANCELLED",
                        local_order.local_id,
                    )
                if self.on_order_closed:
                    try:
                        self.on_order_closed(local_order)
                    except Exception as exc:
                        logger.error("on_order_closed hook failed: %s", exc)
            mismatches += 1
            if self._storage:
                try:
                    await self._storage.save_order(local_order)
                except Exception as exc:
                    logger.error(
                        "Failed to persist reconciled order %s: %s",
                        local_order.local_id,
                        exc,
                    )

        return mismatches

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_order(self, local_id: str) -> Optional[TrackedOrder]:
        return self._orders.get(local_id)

    def get_orders_by_condition(self, condition_id: str) -> list[TrackedOrder]:
        return [
            o for o in self._orders.values()
            if o.condition_id == condition_id
        ]

    def get_open_orders(self) -> list[TrackedOrder]:
        return [
            o for o in self._orders.values()
            if o.state in (OrderState.PENDING, OrderState.OPEN)
        ]

    def get_total_exposure(self) -> float:
        """Total USDC committed in open BUY orders."""
        return sum(
            o.price * o.size
            for o in self._orders.values()
            if o.state in (OrderState.PENDING, OrderState.OPEN)
            and o.side == "BUY"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_local_id(self) -> str:
        self._next_id += 1
        return f"ord_{int(time.time() * 1000)}_{self._next_id}"

    @staticmethod
    def _build_clob_order(
        order: TrackedOrder, *, neg_risk: bool = True
    ) -> dict[str, Any]:
        """Convert a TrackedOrder into the CLOB API order format."""
        return {
            "token_id": order.token_id,
            "price": str(order.price),
            "size": str(order.size),
            "side": order.side,
            "signature_type": 2,  # EIP-712
            "neg_risk": neg_risk,
        }
