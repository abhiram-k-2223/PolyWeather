"""SQLite-based trade storage for the trading engine.

Persists orders, signals, fills, and engine state using the same
sqlite3 patterns as the existing DBManager — but wrapped for use
from async code via ``asyncio.to_thread``.

Tables created:
  - ``trade_orders``: Full order lifecycle history.
  - ``trade_signals``: Ingestion log of all generated signals.
  - ``trade_fills``: Individual fill records from the CLOB.
  - ``engine_state``: Key-value store for engine checkpoint state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default path relative to the project data dir
_DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
)


class TradeStore:
    """Persistent store for trade history and engine state.

    Thread-safe for concurrent access from async code (writes go
    through a single connection). Uses WAL mode for read concurrency.

    Usage:
        store = TradeStore()
        await store.save_order(order_data)
        orders = await store.get_orders(status="OPEN")
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self._db_path = db_path
        else:
            os.makedirs(_DEFAULT_DIR, exist_ok=True)
            self._db_path = os.path.join(_DEFAULT_DIR, "trading.db")

        self._init_db()

    # ------------------------------------------------------------------
    # Schema initialization
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trade_orders (
                    local_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    condition_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'PENDING',
                    filled_size REAL DEFAULT 0,
                    avg_fill_price REAL,
                    error TEXT,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trade_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    target_price REAL NOT NULL,
                    source TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    timestamp TEXT NOT NULL,
                    acted_upon INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS trade_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    local_id TEXT,
                    condition_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    fee REAL DEFAULT 0,
                    transaction_hash TEXT,
                    filled_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS engine_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_orders_state ON trade_orders(state);
                CREATE INDEX IF NOT EXISTS idx_orders_condition ON trade_orders(condition_id);
                CREATE INDEX IF NOT EXISTS idx_signals_condition ON trade_signals(condition_id);
                CREATE INDEX IF NOT EXISTS idx_signals_source ON trade_signals(source);
                CREATE INDEX IF NOT EXISTS idx_fills_order ON trade_fills(order_id);
            """)
            conn.commit()
        finally:
            conn.close()
        logger.debug("TradeStore initialized at %s", self._db_path)

    # ------------------------------------------------------------------
    # Connection (same pattern as DBManager)
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    async def _run(self, fn, *args, **kwargs):
        """Run a synchronous DB function in a thread."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ------------------------------------------------------------------
    # Order persistence
    # ------------------------------------------------------------------

    async def save_order(self, order: Any) -> None:
        """Insert or update a tracked order.

        Accepts either a ``TrackedOrder`` dataclass or a dict.
        """
        if hasattr(order, "local_id"):  # dataclass
            d = {
                "local_id": order.local_id,
                "order_id": order.order_id,
                "condition_id": order.condition_id,
                "token_id": order.token_id,
                "side": order.side,
                "price": order.price,
                "size": order.size,
                "state": order.state.value
                if hasattr(order.state, "value")
                else order.state,
                "filled_size": order.filled_size,
                "avg_fill_price": order.avg_fill_price,
                "error": order.error,
                "metadata_json": json.dumps(order.metadata),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            d = dict(order)
            d.setdefault("updated_at", datetime.now(timezone.utc).isoformat())

        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO trade_orders (
                    local_id, order_id, condition_id, token_id,
                    side, price, size, state, filled_size,
                    avg_fill_price, error, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_id) DO UPDATE SET
                    order_id = excluded.order_id,
                    state = excluded.state,
                    filled_size = excluded.filled_size,
                    avg_fill_price = excluded.avg_fill_price,
                    error = excluded.error,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    d["local_id"],
                    d.get("order_id"),
                    d["condition_id"],
                    d["token_id"],
                    d["side"],
                    d["price"],
                    d["size"],
                    d["state"],
                    d.get("filled_size", 0),
                    d.get("avg_fill_price"),
                    d.get("error"),
                    d.get("metadata_json", "{}"),
                    d.get("created_at", now),
                    d["updated_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_orders(
        self,
        *,
        status: Optional[str] = None,
        condition_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Retrieve orders, optionally filtered."""
        where = []
        params: list = []

        if status:
            where.append("state = ?")
            params.append(status)
        if condition_id:
            where.append("condition_id = ?")
            params.append(condition_id)

        sql = "SELECT * FROM trade_orders"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def get_order_stats(self) -> dict[str, Any]:
        """Return aggregate order statistics."""
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM trade_orders").fetchone()[0]
            by_state = conn.execute(
                "SELECT state, COUNT(*) as cnt FROM trade_orders GROUP BY state"
            ).fetchall()
            return {
                "total_orders": total,
                "by_state": {r["state"]: r["cnt"] for r in by_state},
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Signal persistence
    # ------------------------------------------------------------------

    async def save_signal(self, signal: dict) -> int:
        """Persist a trade signal. Returns the row id."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO trade_signals (
                    condition_id, token_id, direction, confidence,
                    target_price, source, metadata_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["condition_id"],
                    signal.get("token_id", ""),
                    signal.get("direction", ""),
                    signal.get("confidence", 0),
                    signal.get("target_price", 0),
                    signal.get("source", ""),
                    json.dumps(signal.get("metadata", {})),
                    signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
                ),
            )
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()

    async def get_signals(
        self,
        *,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Retrieve signals, optionally filtered by source."""
        where = []
        params: list = []
        if source:
            where.append("source = ?")
            params.append(source)

        sql = "SELECT * FROM trade_signals"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Fill persistence
    # ------------------------------------------------------------------

    async def save_fill(self, fill: dict) -> int:
        """Persist a fill record. Returns the row id."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO trade_fills (
                    order_id, local_id, condition_id, token_id,
                    side, price, size, fee, transaction_hash, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.get("order_id"),
                    fill.get("local_id"),
                    fill["condition_id"],
                    fill["token_id"],
                    fill["side"],
                    fill["price"],
                    fill["size"],
                    fill.get("fee", 0),
                    fill.get("transaction_hash"),
                    fill.get("filled_at", datetime.now(timezone.utc).isoformat()),
                ),
            )
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Engine state (key-value checkpoint)
    # ------------------------------------------------------------------

    async def set_state(self, key: str, value: Any) -> None:
        """Persist an engine state value."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO engine_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), now),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_state(self, key: str) -> Optional[Any]:
        """Retrieve a persisted engine state value."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value_json FROM engine_state WHERE key = ?", (key,)
            ).fetchone()
            if row:
                return json.loads(row["value_json"])
            return None
        finally:
            conn.close()

    async def get_all_state(self) -> dict[str, Any]:
        """Retrieve all engine state as a dict."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT key, value_json FROM engine_state").fetchall()
            return {r["key"]: json.loads(r["value_json"]) for r in rows}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def vacuum(self) -> None:
        """Run VACUUM to reclaim space."""
        conn = self._connect()
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    async def delete_old_records(self, older_than_days: int = 90) -> dict[str, int]:
        """Delete records older than the specified number of days.

        Returns counts of deleted rows.
        """
        cutoff = (
            datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        )
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

        conn = self._connect()
        try:
            deleted_signals = conn.execute(
                "DELETE FROM trade_signals WHERE timestamp < ?", (cutoff_iso,)
            ).rowcount
            deleted_orders = conn.execute(
                "DELETE FROM trade_orders WHERE created_at < ?", (cutoff_iso,)
            ).rowcount
            conn.commit()
            return {
                "signals": deleted_signals,
                "orders": deleted_orders,
            }
        finally:
            conn.close()
