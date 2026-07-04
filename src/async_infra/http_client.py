"""Shared async HTTP client with connection pooling.

Wraps `httpx.AsyncClient` in a singleton that can be shared across
all async services — connection reuse, DNS caching, and configurable
timeouts.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Sensible defaults for a VPS-hosted weather / trading backend
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0, pool=5.0)
_DEFAULT_LIMITS = httpx.Limits(
    max_keepalive_connections=20,
    max_connections=100,
    keepalive_expiry=30.0,
)


class SharedClient:
    """Thin wrapper that owns an `httpx.AsyncClient` with connection pooling.

    Usage:
        client = get_shared_client()
        resp = await client.get("https://api.example.com/")
    """

    def __init__(
        self,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
        limits: httpx.Limits = _DEFAULT_LIMITS,
        **kwargs,
    ) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, limits=limits, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    # -- passthrough convenience methods ---------------------------------

    async def get(
        self, url: str, *, params: Optional[dict] = None, **kwargs
    ) -> httpx.Response:
        return await self._client.get(url, params=params, **kwargs)

    async def post(
        self, url: str, *, json: Optional[dict] = None, **kwargs
    ) -> httpx.Response:
        return await self._client.post(url, json=json, **kwargs)

    async def put(
        self, url: str, *, json: Optional[dict] = None, **kwargs
    ) -> httpx.Response:
        return await self._client.put(url, json=json, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self._client.delete(url, **kwargs)


# ------------------------------------------------------------------
# singleton helpers
# ------------------------------------------------------------------

_SHARED_CLIENT: Optional[SharedClient] = None


def create_shared_client(**kwargs) -> SharedClient:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is not None:
        logger.warning("Overwriting existing SharedClient singleton")
    _SHARED_CLIENT = SharedClient(**kwargs)
    return _SHARED_CLIENT


def get_shared_client() -> SharedClient:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None:
        _SHARED_CLIENT = SharedClient()
    return _SHARED_CLIENT
