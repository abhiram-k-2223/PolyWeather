"""Async CLOB (Central Limit Order Book) API client for Polymarket.

Provides order placement, cancellation, fills query, and account
position retrieval via the Polymarket CLOB REST API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from ...async_infra.http_client import get_shared_client
from ...async_infra.rate_limiter import RateLimiter
from ...async_infra.retry import RetryConfig, retry_async
from .wallet import WalletManager

logger = logging.getLogger(__name__)

_RETRY = RetryConfig(max_attempts=3, base_delay=0.5, jitter=True)


# ------------------------------------------------------------------
# data classes
# ------------------------------------------------------------------


@dataclass
class CLOBAuthConfig:
    """Authentication configuration for the CLOB API.

    The API uses Polygon wallet signing (EIP-712) for auth.
    See https://docs.polymarket.com/api/rest/authentication
    """

    wallet: WalletManager
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


class CLOBClient:
    """Async client for the Polymarket CLOB REST API.

    Handles auth headers, request signing (if applicable), and
    rate limiting. All methods are async.

    Usage:
        wallet = WalletManager(config)
        client = CLOBClient(wallet)
        orders = await client.get_orders()
    """

    def __init__(
        self,
        wallet: WalletManager,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._wallet = wallet
        self._base_url = wallet.clob_api_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._shared = get_shared_client()
        self._limiter = RateLimiter(default_rate=5, default_capacity=10)

    # ------------------------------------------------------------------
    # Auth headers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build the full set of request headers for the CLOB API.

        Includes wallet-based authentication headers.
        """
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        hdrs.update(self._wallet.get_clob_auth_headers())
        if self._api_key:
            hdrs["POLY_API_KEY"] = self._api_key
        return hdrs

    def _sign_request(self, method: str, path: str, body: str = "") -> str:
        """HMAC-SHA256 signing of requests (optional, used when api_secret provided).

        Polymarket CLOB API v3+ uses request signing for order placement.
        """
        if not self._api_secret:
            return ""
        timestamp = str(int(time.time() * 1000))
        msg = f"{timestamp}{method}{path}{body}"
        sig = hmac.new(
            self._api_secret.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()
        return f"{timestamp}.{sig}"

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_markets(self, *, limit: int = 100, next_cursor: str = "") -> dict:
        """Fetch tradable markets."""
        params = {"limit": limit}
        if next_cursor:
            params["next_cursor"] = next_cursor
        await self._limiter.wait("clob-public")
        resp = await self._shared.get(
            f"{self._base_url}/markets",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> dict:
        """Get a single market by condition ID."""
        await self._limiter.wait("clob-public")
        resp = await self._shared.get(
            f"{self._base_url}/markets/{condition_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_book(self, token_id: str, *, side: str = "") -> dict:
        """Get the order book for a token (market/outcome pair).

        Args:
            token_id: The CLOB token ID for the outcome.
            side: Filter by "BUY" or "SELL".
        """
        params: dict = {"token_id": token_id}
        if side:
            params["side"] = side
        await self._limiter.wait("clob-public")
        resp = await self._shared.get(
            f"{self._base_url}/book",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_midpoint(self, token_id: str) -> dict:
        """Get the midpoint price for a token pair."""
        await self._limiter.wait("clob-public")
        resp = await self._shared.get(
            f"{self._base_url}/midpoint",
            params={"token_id": token_id},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_price(self, token_id: str, side: str, amount: str) -> dict:
        """Get an indicative price for a given order size.

        Args:
            token_id: CLOB token ID.
            side: "BUY" or "SELL".
            amount: USDC amount (as string to preserve precision).
        """
        await self._limiter.wait("clob-public")
        resp = await self._shared.get(
            f"{self._base_url}/price",
            params={"token_id": token_id, "side": side, "amount": amount},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Order endpoints (authenticated)
    # ------------------------------------------------------------------

    @retry_async(_RETRY)
    async def place_order(self, order: dict) -> dict:
        """Place an order on the CLOB.

        The order dict must conform to the Polymarket CLOB order schema:
        https://docs.polymarket.com/api/rest/orders/post-order

        Minimal example:
            order = {
                "token_id": "...",
                "price": "0.55",
                "size": "100.0",
                "side": "BUY",
                "fee_rate_bps": 0,
                "signature_type": 2,  # EIP-712
                "neg_risk": True,
            }
        """
        await self._limiter.wait("clob-order")
        body = json.dumps(order)
        sig = self._sign_request("POST", "/order", body)
        hdrs = self._headers()
        if sig:
            hdrs["POLY_SIGNATURE"] = sig
        resp = await self._shared.post(
            f"{self._base_url}/order",
            content=body,
            headers=hdrs,
        )
        if resp.status_code in (429,):
            self._limiter._bucket("clob-order").report_throttle("clob-order")
        resp.raise_for_status()
        return resp.json()

    @retry_async(_RETRY)
    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by its order ID."""
        await self._limiter.wait("clob-order")
        body = json.dumps({"order_id": order_id})
        hdrs = self._headers()
        resp = await self._shared.delete(
            f"{self._base_url}/order",
            content=body,
            headers=hdrs,
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_all_orders(self) -> dict:
        """Cancel all open orders."""
        await self._limiter.wait("clob-order")
        resp = await self._shared.delete(
            f"{self._base_url}/orders",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_orders(
        self,
        *,
        status: str = "OPEN",
        limit: int = 100,
        next_cursor: str = "",
    ) -> dict:
        """Get orders for the authenticated account.

        Args:
            status: "OPEN", "MATCHED", "CANCELLED", or "ALL".
        """
        params = {"status": status, "limit": limit}
        if next_cursor:
            params["next_cursor"] = next_cursor
        await self._limiter.wait("clob-order")
        resp = await self._shared.get(
            f"{self._base_url}/orders",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Fills and positions
    # ------------------------------------------------------------------

    async def get_fills(
        self,
        *,
        limit: int = 100,
        next_cursor: str = "",
    ) -> dict:
        """Get fills (matched trades) for the authenticated account."""
        params = {"limit": limit}
        if next_cursor:
            params["next_cursor"] = next_cursor
        await self._limiter.wait("clob-order")
        resp = await self._shared.get(
            f"{self._base_url}/fills",
            params=params,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_positions(self, *, limit: int = 100) -> dict:
        """Get current positions for the authenticated account."""
        await self._limiter.wait("clob-order")
        resp = await self._shared.get(
            f"{self._base_url}/positions",
            params={"limit": limit},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_balance(self) -> dict:
        """Get the trading wallet's USDC balance on the CLOB."""
        await self._limiter.wait("clob-order")
        resp = await self._shared.get(
            f"{self._base_url}/balance",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Neg-risk specific endpoints
    # ------------------------------------------------------------------

    async def get_neg_risk(self, condition_id: str) -> dict:
        """Get neg-risk settings for a condition."""
        await self._limiter.wait("clob-public")
        resp = await self._shared.get(
            f"{self._base_url}/neg-risk/{condition_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def post_neg_risk(
        self, condition_id: str, signed_data: dict
    ) -> dict:
        """Post a signed neg-risk order (for neg-risk markets)."""
        await self._limiter.wait("clob-order")
        resp = await self._shared.post(
            f"{self._base_url}/neg-risk/{condition_id}",
            json=signed_data,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()
