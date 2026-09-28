"""Resolve daily Polymarket weather markets to the engine market map.

Daily temperature markets expire, so a hand-maintained
``POLY_MARKET_MAP`` goes stale. Prefer this resolver: for each feed city,
fetch today's candidates via
``GammaClient.resolve_city_markets(city)`` (or
``discover_weather_markets_from_poly()``) and pick one market per ICAO.

Runbook
-------
1. Set ``POLY_TRADING_FEED_CITIES`` (comma-separated ICAOs, default
   ``KLGA,KLAX,KORD``).
2. Call ``web.services.trading_api.refresh_market_map_from_gamma()``
   (daily cron or deploy hook) — it resolves each feed city and pushes
   the result into the engine via ``TradingEngine.update_market_map()``.
3. ``POLY_MARKET_MAP`` env JSON (``{ICAO: [condition_id, token_id]}``)
   remains as a manual override / seed for the initial map; the Gamma
   refresh overwrites entries it resolves and leaves the rest alone.

Selection rule (``pick_best_market_for_city``): skip inactive, closed, or
token-less markets; skip markets whose question carries no parseable
temperature strike (volume alone once elected a disease market and the
feed went silent — no strike ever parses from it); pick the highest
``volume``; break ties by smallest ``condition_id`` so the choice is
deterministic.
"""

from __future__ import annotations

from typing import Any, Optional

from src.trading.signals.openmeteo_probability import parse_temp_strike

#: Default paper-feed cities: ICAO -> city name used for the Gamma search.
DEFAULT_FEED_CITIES: dict[str, str] = {
    "KLGA": "new york",
    "KLAX": "los angeles",
    "KORD": "chicago",
}


def _market_tokens(market: Any) -> list[str]:
    tokens = getattr(market, "clob_token_ids", None)
    if not tokens:
        single = getattr(market, "clob_token_id", "") or ""
        tokens = [single] if single else []
    return [t for t in tokens if t]


def _market_volume(market: Any) -> float:
    try:
        return float(getattr(market, "volume", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def pick_best_market_for_city(
    city: str, markets: list[Any] | None
) -> Optional[tuple[str, str]]:
    """Pick ``(condition_id, token_id)`` for a city, or None.

    Args:
        city: Human-readable city name (used only for context; the
            caller fetches candidates).
        markets: Candidate markets (GammaMarket or duck-typed equivalents
            with ``condition_id`` / ``clob_token_ids`` / ``volume`` /
            ``active`` / ``closed``).
    """
    del city  # selection is volume-based; city scopes the candidates.
    best_cond = ""
    best_token = ""
    best_vol = -1.0
    for market in markets or []:
        if not getattr(market, "active", True):
            continue
        if getattr(market, "closed", False):
            continue
        condition_id = str(getattr(market, "condition_id", "") or "")
        tokens = _market_tokens(market)
        if not condition_id or not tokens:
            continue
        if parse_temp_strike(str(getattr(market, "question", "") or "")) is None:
            continue
        vol = _market_volume(market)
        if vol > best_vol or (vol == best_vol and condition_id < best_cond):
            best_vol, best_cond, best_token = vol, condition_id, tokens[0]
    if best_vol < 0:
        return None
    return (best_cond, best_token)


def build_market_map(
    candidates: dict[str, tuple[str, list[Any]]] | None,
) -> dict[str, tuple[str, str]]:
    """Build ``{ICAO: (condition_id, token_id)}`` from resolved candidates.

    Args:
        candidates: ``{ICAO: (city_name, markets)}``. ICAOs with no
            resolvable market are omitted (caller keeps any prior entry).
    """
    resolved: dict[str, tuple[str, str]] = {}
    for icao, (city, markets) in (candidates or {}).items():
        pick = pick_best_market_for_city(city, markets)
        if pick is not None:
            resolved[icao] = pick
    return resolved
