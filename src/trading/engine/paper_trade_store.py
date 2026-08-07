from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PaperTradeRecord:
    local_id: str
    condition_id: str
    token_id: str
    side: str
    price: float
    size: float
    direction: str
    confidence: float
    source: str
    model_probability: float
    market_price: Optional[float]
    gap: float
    status: str  # OPEN, FILLED, CANCELLED, SETTLED_WON, SETTLED_LOST
    simulated_pnl: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class PaperTradeStore:
    def __init__(self) -> None:
        self._trades: list[PaperTradeRecord] = []
        self._positions: dict[str, PaperTradeRecord] = {}

    def log_trade(
        self,
        condition_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        direction: str,
        confidence: float,
        source: str,
        model_probability: float,
        market_price: Optional[float],
        gap: float,
        metadata: Optional[dict] = None,
    ) -> PaperTradeRecord:
        local_id = f"paper_{uuid.uuid4().hex[:12]}"
        record = PaperTradeRecord(
            local_id=local_id,
            condition_id=condition_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            direction=direction,
            confidence=confidence,
            source=source,
            model_probability=model_probability,
            market_price=market_price,
            gap=gap,
            status="OPEN",
            metadata=metadata or {},
        )
        self._trades.append(record)
        self._positions[token_id] = record
        logger.info(
            "Paper trade logged: %s %s %.4f @ %.4f (gap=%.2f%%)",
            local_id, side, size, price, gap * 100,
        )
        return record

    def record_settlement(self, token_id: str, won: bool) -> Optional[PaperTradeRecord]:
        record = self._positions.pop(token_id, None)
        if not record:
            return None
        if won:
            record.status = "SETTLED_WON"
            record.simulated_pnl = (1.0 - record.price) * record.size
        else:
            record.status = "SETTLED_LOST"
            record.simulated_pnl = -record.price * record.size
        logger.info(
            "Paper settlement: %s %s (pnl=%.2f)",
            record.local_id, "WON" if won else "LOST", record.simulated_pnl,
        )
        return record

    def cancel_trade(self, local_id: str) -> bool:
        for t in self._trades:
            if t.local_id == local_id:
                t.status = "CANCELLED"
                self._positions.pop(t.token_id, None)
                return True
        return False

    def get_trades(self, limit: int = 100) -> list[PaperTradeRecord]:
        return sorted(self._trades, key=lambda t: t.created_at, reverse=True)[:limit]

    def get_trades_by_condition(self, condition_id: str) -> list[PaperTradeRecord]:
        return [t for t in self._trades if t.condition_id == condition_id]

    def get_open_positions(self) -> list[PaperTradeRecord]:
        return list(self._positions.values())

    def get_stats(self) -> dict[str, Any]:
        total = len(self._trades)
        settled = [t for t in self._trades if t.status in ("SETTLED_WON", "SETTLED_LOST")]
        won = sum(1 for t in settled if t.status == "SETTLED_WON")
        total_pnl = sum(t.simulated_pnl for t in settled)
        open_count = len(self._positions)
        return {
            "total_trades": total,
            "open_positions": open_count,
            "settled_trades": len(settled),
            "wins": won,
            "losses": len(settled) - won,
            "win_rate": won / len(settled) if settled else 0.0,
            "total_pnl_usdc": round(total_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / len(settled), 2) if settled else 0.0,
            "total_volume_usdc": round(sum(t.price * t.size for t in self._trades), 2),
        }

    def clear(self) -> None:
        self._trades.clear()
        self._positions.clear()

    def to_dict(self, trades: Optional[list[PaperTradeRecord]] = None) -> list[dict]:
        return [
            {
                "local_id": t.local_id,
                "condition_id": t.condition_id,
                "token_id": t.token_id,
                "side": t.side,
                "price": t.price,
                "size": t.size,
                "direction": t.direction,
                "confidence": t.confidence,
                "source": t.source,
                "model_probability": t.model_probability,
                "market_price": t.market_price,
                "gap": t.gap,
                "status": t.status,
                "simulated_pnl": t.simulated_pnl,
                "created_at": t.created_at.isoformat(),
            }
            for t in (trades or self._trades)
        ]
