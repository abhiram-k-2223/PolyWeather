"""Rate limiters for async code.

Provides a token-bucket rate limiter and an adaptive rate limiter
that backs off on HTTP 429 / rate-limit responses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """Simple token-bucket rate limiter.

    Thread-safe when used from a single event loop (no locking needed
    in asyncio).
    """

    def __init__(self, rate: float, capacity: int) -> None:
        """
        Args:
            rate: Tokens added per second.
            capacity: Maximum token count (burst size).
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # Sleep until at least one token is replenished
            wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)


class RateLimiter:
    """High-level per-key rate limiter backed by token buckets.

    Usage:
        limiter = RateLimiter(default_rate=5, default_capacity=10)
        await limiter.wait("polymarket-clob")
        # … make API call …
    """

    def __init__(
        self, default_rate: float = 10, default_capacity: int = 20
    ) -> None:
        self._default_rate = default_rate
        self._default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                self._default_rate, self._default_capacity
            )
        return self._buckets[key]

    async def wait(self, key: str = "default") -> None:
        await self._bucket(key).acquire()

    def configure(self, key: str, rate: float, capacity: int) -> None:
        self._buckets[key] = TokenBucket(rate, capacity)


class AdaptiveRateLimiter:
    """Rate limiter that slows down on 429 responses.

    Maintains a per-key multiplier that is applied on top of a base
    rate. The multiplier decays slowly when requests succeed.
    """

    def __init__(
        self,
        base_rate: float = 10,
        base_capacity: int = 20,
        backoff_factor: float = 0.5,
        recovery_rate: float = 0.05,
        max_multiplier: float = 10.0,
    ) -> None:
        self._base_rate = base_rate
        self._base_capacity = base_capacity
        self._backoff = backoff_factor
        self._recovery = recovery_rate
        self._max_mult = max_multiplier
        self._multipliers: dict[str, float] = {}
        self._buckets: dict[str, TokenBucket] = {}

    def _multiplier(self, key: str) -> float:
        return self._multipliers.get(key, 1.0)

    def _effective_rate(self, key: str) -> float:
        return self._base_rate / self._multiplier(key)

    def _effective_capacity(self, key: str) -> int:
        return max(1, int(self._base_capacity / self._multiplier(key)))

    def _bucket(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                self._effective_rate(key), self._effective_capacity(key)
            )
        return self._buckets[key]

    async def wait(self, key: str = "default") -> None:
        await self._bucket(key).acquire()

    def report_success(self, key: str) -> None:
        """Decay the backoff multiplier slightly on success."""
        if key in self._multipliers:
            self._multipliers[key] = max(
                1.0, self._multipliers[key] - self._recovery
            )

    def report_throttle(self, key: str, retry_after: Optional[float] = None) -> None:
        """Increase the backoff multiplier on a 429."""
        current = self._multipliers.get(key, 1.0)
        self._multipliers[key] = min(self._max_mult, current / self._backoff)
        delay = retry_after or (1.0 * self._multipliers[key])
        logger.warning(
            "Rate limited on '%s' — multiplier now %.2f, backing off %.1fs",
            key,
            self._multipliers[key],
            delay,
        )
