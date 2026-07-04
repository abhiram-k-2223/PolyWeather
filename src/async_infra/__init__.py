"""Async infrastructure layer for PolyWeather.

Provides foundational async components used across all services:
event loop management, connection-pooled HTTP clients,
async Redis access, retry/backoff utilities, and rate limiters.
"""

from .event_loop import AsyncManager, get_async_manager
from .http_client import SharedClient, create_shared_client, get_shared_client
from .rate_limiter import AdaptiveRateLimiter, RateLimiter, TokenBucket
from .redis_client import AsyncRedisClient, get_async_redis
from .retry import RetryConfig, retry_async

__all__ = [
    "AsyncManager",
    "get_async_manager",
    "SharedClient",
    "create_shared_client",
    "get_shared_client",
    "AdaptiveRateLimiter",
    "RateLimiter",
    "TokenBucket",
    "AsyncRedisClient",
    "get_async_redis",
    "RetryConfig",
    "retry_async",
]
