"""Position tracking — monitor open positions and compute P&L.

Tracks positions across all Polymarket markets the engine is
active in. Provides P&L, exposure, and close-out estimates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """An open position in a Polymarket market.

    Attributes:
        condition_id: Polymarket condition ID.
        token_id: Outcome token ID.
        side: "YES" or "NO" position.
        size: Number of outcome tokens held.
        avg_entry_price: Average price paid per token.
        current_price: Latest known market price.
        unrealized_pnl: Current unrealized P&L in USDC.
        opened_at: When the position was opened.
        last_updated: When position data was last refreshed.
        metadata: Arbitrary extra data.
    """

    condition_id: str
    token_id: str
    side: str
    size: float
    avg_entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class PositionTracker:
    """Tracks positions and computes P&L.

    Can be seeded from the CLOB API or from local storage.
    """

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}  # token_id -> Position

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def open_position(
        self,
        condition_id: str,
        token_id: str,
        side: str,
        size: float,
        entry_price: float,
        metadata: Optional[dict] = None,
    ) -> Position:
        """Record a new position."""
        pos = Position(
            condition_id=condition_id,
            token_id=token_id,
            side=side.upper(),
            size=size,
            avg_entry_price=entry_price,
            metadata=metadata or {},
        )
        self._positions[token_id] = pos
        logger.info(
            "Position opened: %s %s @ %.4f (size=%.2f)",
            token_id[:10],
            side,
            entry_price,
            size,
        )
        return pos

    def close_position(self, token_id: str) -> Optional[Position]:
        """Remove and return a closed position."""
        pos = self._positions.pop(token_id, None)
        if pos:
            logger.info(
                "Position closed: %s (entry=%.4f, size=%.2f)",
                token_id[:10],
                pos.avg_entry_price,
                pos.size,
            )
        return pos

    def update_price(self, token_id: str, price: float) -> None:
        """Update the current market price for a position and recompute P&L."""
        pos = self._positions.get(token_id)
        if not pos:
            return
        pos.current_price = price
        pos.last_updated = datetime.now(timezone.utc)
        if pos.side == "YES":
            pos.unrealized_pnl = (price - pos.avg_entry_price) * pos.size
        else:
            # NO position: P&L = (entry - current) * size
            pos.unrealized_pnl = (pos.avg_entry_price - price) * pos.size

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, token_id: str) -> Optional[Position]:
        return self._positions.get(token_id)

    def get_positions_by_condition(self, condition_id: str) -> list[Position]:
        return [
            p for p in self._positions.values()
            if p.condition_id == condition_id
        ]

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    def get_total_exposure(self) -> float:
        """Total cost basis of all open positions."""
        return sum(
            p.avg_entry_price * p.size for p in self._positions.values()
        )

    def position_count(self) -> int:
        return len(self._positions)

    # ------------------------------------------------------------------
    # Bulk sync from CLOB or storage
    # ------------------------------------------------------------------

    def sync_from_clob_response(self, positions_data: list[dict]) -> int:
        """Sync positions from a CLOB API /positions response.

        Returns the number of positions synced.
        """
        count = 0
        for item in positions_data:
            token_id = item.get("token_id")
            if not token_id:
                continue
            pos = Position(
                condition_id=item.get("condition_id", ""),
                token_id=token_id,
                side=item.get("side", "YES"),
                size=float(item.get("size", 0)),
                avg_entry_price=float(item.get("avg_price", 0)),
                current_price=float(item.get("current_price", 0)),
            )
            self._positions[token_id] = pos
            count += 1
        logger.info("Synced %d positions from CLOB", count)
        return count
