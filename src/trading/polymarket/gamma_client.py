"""Async Gamma API client for Polymarket market discovery.

The Gamma API is Polymarket's public REST API for discovering markets,
querying prices, volumes, liquidity, and market metadata. It requires
no authentication and is completely free.

Base URL: https://gamma-api.polymarket.com
Docs: https://docs.polymarket.com/api-reference/introduction
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...async_infra.http_client import get_shared_client
from ...async_infra.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class GammaMarket:
    condition_id: str
    clob_token_ids: list[str]
    question: str
    description: str
    volume: float
    liquidity: float
    active: bool
    closed: bool
    end_date_iso: str
    neg_risk: bool
    rewards: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GammaEvent:
    event_slug: str
    title: str
    markets: list[GammaMarket]
    raw: dict[str, Any] = field(default_factory=dict)


class GammaClient:
    """Async client for Polymarket's Gamma API.

    Provides market discovery — finding active weather markets,
    querying prices, and resolving condition IDs for cities.
    All endpoints are public and require no authentication.

    Usage:
        client = GammaClient()
        events = await client.get_events(tag="weather")
        markets = await client.get_markets(condition_ids=[...])
        price = await client.get_best_price(condition_id, token_id)
    """

    def __init__(self) -> None:
        self._base_url = "https://gamma-api.polymarket.com"
        self._shared = get_shared_client()
        self._limiter = RateLimiter(default_rate=10, default_capacity=20)

    # ------------------------------------------------------------------
    # Event discovery
    # ------------------------------------------------------------------

    async def get_events(
        self,
        *,
        tag: str | None = "weather",
        active: bool | None = True,
        closed: bool | None = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GammaEvent]:
        """Discover events, optionally filtered by tag and status.

        Args:
            tag: Filter by tag (e.g. ``"weather"``). ``None`` for all.
            active: Only active (trading) events.
            closed: Include closed events.
            limit: Max events per page.
            offset: Pagination offset.

        Returns:
            List of ``GammaEvent`` objects with nested ``GammaMarket``\\s.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if tag is not None:
            params["tag"] = tag
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()

        await self._limiter.wait("gamma-api")
        resp = await self._shared.get(
            f"{self._base_url}/events",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            data = data.get("data", data) if isinstance(data, dict) else []
        return [_parse_event(raw) for raw in data]

    async def get_event_by_slug(self, slug: str) -> GammaEvent | None:
        """Get a single event by its slug.

        Args:
            slug: Event slug (e.g. ``\"high-temperature-seoul-2026-07-14\"``).
        """
        await self._limiter.wait("gamma-api")
        resp = await self._shared.get(
            f"{self._base_url}/events/{slug}",
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_event(resp.json())

    # ------------------------------------------------------------------
    # Market queries
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        condition_ids: list[str] | None = None,
        *,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
        closed: bool = False,
    ) -> list[GammaMarket]:
        """Query markets, optionally filtered by condition IDs or tag.

        Args:
            condition_ids: Filter by specific condition IDs.
            tag: Filter by tag (e.g. ``\"weather\"``).
            limit: Max markets per page.
            offset: Pagination offset.
            closed: Include closed markets.

        Returns:
            List of ``GammaMarket`` objects.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "closed": str(closed).lower(),
        }
        if tag is not None:
            params["tag"] = tag
        if condition_ids:
            params["condition_ids"] = ",".join(condition_ids)

        await self._limiter.wait("gamma-api")
        resp = await self._shared.get(
            f"{self._base_url}/markets",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            data = data.get("data", data) if isinstance(data, dict) else []
        return [_parse_market(raw) for raw in data]

    async def get_market(self, condition_id: str) -> GammaMarket | None:
        """Get a single market by condition ID."""
        await self._limiter.wait("gamma-api")
        resp = await self._shared.get(
            f"{self._base_url}/markets/{condition_id}",
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_market(resp.json())

    # ------------------------------------------------------------------
    # Price queries
    # ------------------------------------------------------------------

    async def get_best_price(
        self, condition_id: str, token_id: str, side: str = "BUY"
    ) -> float | None:
        """Get the best available price for a token from the order book.

        Args:
            condition_id: Polymarket condition ID.
            token_id: CLOB token ID for the outcome.
            side: ``\"BUY\"`` (best offer) or ``\"SELL\"`` (best bid).

        Returns:
            Best price (0–1) or ``None`` if the order book is empty.
        """
        params = {
            "condition_id": condition_id,
            "token_id": token_id,
            "side": side.upper(),
        }
        await self._limiter.wait("gamma-api")
        resp = await self._shared.get(
            f"{self._base_url}/price",
            params=params,
        )
        if resp.status_code == 404 or resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return float(data.get("price", 0.0))
        return float(data) if data is not None else None

    async def get_midpoint_price(
        self, condition_id: str, token_id: str
    ) -> float | None:
        """Get the midpoint price (average of best bid and best ask).

        Args:
            condition_id: Polymarket condition ID.
            token_id: CLOB token ID for the outcome.

        Returns:
            Midpoint price (0–1) or ``None`` if no liquidity.
        """
        bid = await self.get_best_price(condition_id, token_id, side="SELL")
        ask = await self.get_best_price(condition_id, token_id, side="BUY")
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        return bid or ask

    # ------------------------------------------------------------------
    # City → market resolution
    # ------------------------------------------------------------------

    async def resolve_city_markets(
        self,
        city: str,
        *,
        tag: str = "weather",
        active_only: bool = True,
    ) -> list[GammaMarket]:
        """Find active temperature markets for a city.

        Searches weather events containing the city name and returns
        any active markets. This is the primary method for mapping
        city-level analysis to Polymarket markets.

        Args:
            city: City name (e.g. ``\"Seoul\"``, ``\"New York\"``).
            tag: Event tag to filter by.
            active_only: Only return actively trading markets.

        Returns:
            List of matching ``GammaMarket`` objects.
        """
        events = await self.get_events(tag=tag, active=True)
        city_lower = city.lower()
        results: list[GammaMarket] = []
        for event in events:
            if city_lower not in event.title.lower():
                continue
            for market in event.markets:
                if active_only and not market.active:
                    continue
                if market.closed:
                    continue
                results.append(market)
        return results


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _parse_event(raw: dict[str, Any]) -> GammaEvent:
    markets_raw = raw.get("markets", [])
    if isinstance(markets_raw, dict):
        markets_raw = markets_raw.get("data", [])
    return GammaEvent(
        event_slug=raw.get("slug", ""),
        title=raw.get("title", ""),
        markets=[_parse_market(m) for m in markets_raw] if isinstance(markets_raw, list) else [],
        raw=raw,
    )


def _parse_market(raw: dict[str, Any]) -> GammaMarket:
    token_ids = raw.get("clobTokenIds", "") or raw.get("clob_token_ids", "")
    if isinstance(token_ids, str):
        token_ids = [t.strip() for t in token_ids.split(",") if t.strip()]

    return GammaMarket(
        condition_id=str(raw.get("conditionId", raw.get("condition_id", ""))),
        clob_token_ids=token_ids,
        question=raw.get("question", ""),
        description=raw.get("description", ""),
        volume=float(raw.get("volume", raw.get("volumeNum", 0) or 0)),
        liquidity=float(raw.get("liquidity", raw.get("liquidityNum", 0) or 0)),
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        end_date_iso=raw.get("endDate", raw.get("end_date_iso", "")),
        neg_risk=bool(raw.get("negRisk", raw.get("neg_risk", False))),
        rewards=raw.get("rewards"),
        raw=raw,
    )
