"""Async Redis client wrapper.

Provides a singleton async Redis client (``redis.asyncio``) for use
by the trading engine and any future async services.
"""

from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)


class AsyncRedisClient:
    """Wraps ``redis.asyncio.Redis`` with connection pooling.

    Usage:
        client = get_async_redis()
        await client.set("key", "value")
        val = await client.get("key")
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        socket_connect_timeout: float = 3.0,
        socket_timeout: float = 5.0,
        **kwargs,
    ) -> None:
        self._redis = AsyncRedis(
            host=host,
            port=port,
            db=db,
            password=password or None,
            socket_connect_timeout=socket_connect_timeout,
            socket_timeout=socket_timeout,
            decode_responses=True,
            **kwargs,
        )

    @property
    def client(self) -> AsyncRedis:
        return self._redis

    async def close(self) -> None:
        await self._redis.aclose()

    # -- convenience accessors -------------------------------------------

    async def ping(self) -> bool:
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def get(self, key: str) -> Optional[str]:
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        await self._redis.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def xadd(
        self, stream: str, fields: dict, maxlen: Optional[int] = None
    ) -> str:
        return await self._redis.xadd(stream, fields, maxlen=maxlen)

    async def xread(
        self, streams: dict, block: int = 5000, count: int = 10
    ) -> list:
        return await self._redis.xread(streams, block=block, count=count)

    async def publish(self, channel: str, message: str) -> int:
        return await self._redis.publish(channel, message)


# ------------------------------------------------------------------
# singleton
# ------------------------------------------------------------------

_ASYNC_REDIS: Optional[AsyncRedisClient] = None


def get_async_redis(
    host: str = "127.0.0.1",
    port: int = 6379,
    db: int = 0,
    password: Optional[str] = None,
) -> AsyncRedisClient:
    global _ASYNC_REDIS
    if _ASYNC_REDIS is None:
        _ASYNC_REDIS = AsyncRedisClient(
            host=host, port=port, db=db, password=password
        )
    return _ASYNC_REDIS
