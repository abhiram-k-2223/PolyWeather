"""Trade storage package.

Provides SQLite-based persistence for trade history, signals,
and engine state — building on the project's existing DB patterns.
"""

from .trade_store import TradeStore

__all__ = ["TradeStore"]
