"""Async Data API client for Polymarket.

Provides access to Polymarket's Data API for historical market data,
price series, volume, and market metadata — useful for signal
validation before trading.
"""

from __future__ import annotations

import logging
from typing import Optional

from ...async_infra.http_client import get_shared_client
from ...async_infra.rate_limiter import RateLimiter
from .wallet import WalletManager

logger = logging.getLogger(__name__)


class DataAPIClient:
    """Async client for Polymarket's Data API (data-api.polymarket.com).

    This API provides public read-only market data without authentication.
    """

    def __init__(self, wallet: WalletManager) -> None:
        self._base_url = wallet.data_api_url.rstrip("/")
        self._shared = get_shared_client()
        self._limiter = RateLimiter(default_rate=10, default_capacity=20)

    # ------------------------------------------------------------------
    # Market queries
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        *,
        tag: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        closed: bool = False,
    ) -> dict:
        """List markets, optionally filtered by tag (e.g. 'weather')."""
        params = {
            "limit": limit,
            "offset": offset,
            "closed": str(closed).lower(),
        }
        if tag:
            params["tag"] = tag
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/markets",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> dict:
        """Get detailed market data by condition ID."""
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/markets/{condition_id}",
        )
        resp.raise_for_status()
        return resp.json()

    async def get_market_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1h",
        limit: int = 200,
    ) -> dict:
        """Get historical price data for a token.

        Args:
            token_id: CLOB token ID for the outcome.
            interval: '1m', '5m', '15m', '1h', '1d'.
            limit: Number of data points.
        """
        params = {
            "token_id": token_id,
            "interval": interval,
            "limit": limit,
        }
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/price-history",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_market_snapshots(
        self,
        condition_id: str,
        *,
        limit: int = 100,
    ) -> dict:
        """Get liquidity snapshots for a condition."""
        params = {"limit": limit}
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/snapshots/{condition_id}",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def search_markets(
        self, query: str, *, limit: int = 20
    ) -> dict:
        """Search markets by keyword."""
        params = {"q": query, "limit": limit}
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/markets/search",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Token / outcome queries
    # ------------------------------------------------------------------

    async def get_token_prices(self, token_ids: list[str]) -> dict:
        """Get latest prices for one or more tokens.

        Args:
            token_ids: List of CLOB token IDs.
        """
        await self._limiter.wait("data-api")
        resp = await self._shared.post(
            f"{self._base_url}/prices",
            json={"token_ids": token_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_outcome(self, token_id: str) -> dict:
        """Get outcome details by token ID."""
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/outcomes/{token_id}",
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # account data (authenticated — requires wallet)
    # ------------------------------------------------------------------

    async def get_account_trades(
        self,
        account: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Get trades for a given account address."""
        params = {"limit": limit, "offset": offset}
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/accounts/{account}/trades",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_account_positions(
        self,
        account: str,
    ) -> dict:
        """Get positions for a given account address."""
        await self._limiter.wait("data-api")
        resp = await self._shared.get(
            f"{self._base_url}/accounts/{account}/positions",
        )
        resp.raise_for_status()
        return resp.json()
