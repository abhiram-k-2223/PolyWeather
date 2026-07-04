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
        self, clob_client: CLOBClient, storage: Optional[Any] = None
    ) -> None:
        self._clob = clob_client
        self._storage = storage  # optional TradeStore for persistence
        self._orders: dict[str, TrackedOrder] = {}  # local_id -> order
        self._next_id: int = 0

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

        Returns the number of mismatches found and corrected.
        """
        try:
            remote = await self._clob.get_orders(status="OPEN")
        except Exception as exc:
            logger.error("Failed to reconcile: %s", exc)
            return 0

        mismatches = 0
        remote_orders = remote.get("data", [])
        remote_ids = {o.get("id") for o in remote_orders if o.get("id")}

        for local_order in self._orders.values():
            if local_order.order_id and local_order.order_id not in remote_ids:
                if local_order.state == OrderState.OPEN:
                    # Order is no longer open on the CLOB — it may have been
                    # filled, cancelled by another session, or expired.
                    # Check fills to determine.
                    local_order.state = OrderState.MATCHED
                    mismatches += 1
                    logger.info(
                        "Order %s no longer open — marked MATCHED",
                        local_order.local_id,
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
