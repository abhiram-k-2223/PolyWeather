"""Async event loop lifecycle manager.

Provides a singleton `AsyncManager` that owns the asyncio event loop
used by background trading tasks, while coexisting cleanly with
FastAPI's existing loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)


class AsyncManager:
    """Manages the background event loop and task lifecycle.

    Typical usage from a synchronous service (e.g. the collector or
    the bot) that wants to run async trading tasks:

        mgr = get_async_manager()
        mgr.start()
        mgr.schedule(my_coroutine())
        # later …
        mgr.stop()
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._tasks: list[asyncio.Task] = []
        self._stopped = threading.Event()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background event loop on a daemon thread."""
        if self._loop is not None and self._loop.is_running():
            logger.debug("AsyncManager already running")
            return

        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="async-mgr",
            daemon=True,
        )
        self._thread.start()
        logger.info("AsyncManager started")

    def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop the background event loop."""
        if self._loop is None or not self._loop.is_running():
            return

        for t in self._tasks:
            t.cancel()

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._stopped.wait(timeout=timeout)
        logger.info("AsyncManager stopped")

    def schedule(self, coro, *, name: Optional[str] = None) -> asyncio.Task:
        """Schedule a coroutine on the background loop.

        Returns a concurrent.futures-compatible Future that can be
        awaited or synchronously waited on.
        """
        if self._loop is None:
            raise RuntimeError("AsyncManager not started; call .start() first")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future

    def run_coro_sync(self, coro, timeout: float = 30) -> object:
        """Run a coroutine synchronously, blocking the current thread.

        Useful from existing synchronous code paths.
        """
        if self._loop is None:
            raise RuntimeError("AsyncManager not started; call .start() first")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ------------------------------------------------------------------
    # FastAPI lifespan (async context manager)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Use as a FastAPI lifespan context manager.

        Example:
            @asynccontextmanager
            async def lifespan(app):
                async with get_async_manager().lifespan():
                    yield
        """
        self.start()
        try:
            yield
        finally:
            self.stop()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._stopped.set()


# ------------------------------------------------------------------
# module-level singleton
# ------------------------------------------------------------------

_ASYNC_MANAGER: Optional[AsyncManager] = None


def get_async_manager() -> AsyncManager:
    global _ASYNC_MANAGER
    if _ASYNC_MANAGER is None:
        _ASYNC_MANAGER = AsyncManager()
    return _ASYNC_MANAGER
