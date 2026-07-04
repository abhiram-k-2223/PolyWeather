"""Retry / backoff utilities for async operations.

Provides a configurable `@retry_async` decorator and a lightweight
`RetryConfig` class for controlling retry behaviour per-call-site.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from functools import wraps
from typing import (
    Awaitable,
    Callable,
    Optional,
    Set,
    Type,
    TypeVar,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Awaitable])


@dataclass
class RetryConfig:
    """Retry policy configuration.

    Attributes:
        max_attempts: Maximum number of attempts (including the first).
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Maximum delay cap in seconds.
        jitter: Add ±50% random jitter when True.
        retryable_exceptions: Set of exception types that trigger a retry.
            ``None`` means retry on all exceptions.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    retryable_exceptions: Optional[Set[Type[Exception]]] = None

    def next_delay(self, attempt: int) -> float:
        """Compute delay before *attempt* (1-based)."""
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            delay *= 0.5 + random.random()  # 0.5x – 1.5x
        return delay


def retry_async(
    config: Optional[RetryConfig] = None,
) -> Callable[[F], F]:
    """Decorator that retries an async function on failure.

    Usage:
        @retry_async(RetryConfig(max_attempts=5))
        async def fetch_data(url: str) -> dict:
            ...
    """
    cfg = config or RetryConfig()

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, cfg.max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if cfg.retryable_exceptions is not None:
                        if not any(
                            isinstance(exc, e) for e in cfg.retryable_exceptions
                        ):
                            raise  # non-retryable, re-raise immediately
                    if attempt == cfg.max_attempts:
                        logger.error(
                            "All %d attempts failed for %s: %s",
                            cfg.max_attempts,
                            func.__name__,
                            exc,
                        )
                        raise
                    delay = cfg.next_delay(attempt)
                    logger.warning(
                        "Attempt %d/%d failed for %s — retrying in %.1fs: %s",
                        attempt,
                        cfg.max_attempts,
                        func.__name__,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
            # Should not be reachable
            raise RuntimeError("Unexpected retry loop exit")
        return wrapper  # type: ignore
    return decorator
