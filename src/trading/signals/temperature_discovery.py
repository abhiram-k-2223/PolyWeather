"""Global temperature-market discovery.

The feed must consider every city Polymarket lists temperature markets
for — including Asian cities — not just a few hardcoded stations.
Discovery searches Gamma for temperature markets, keeps only active,
unclosed markets with a parseable temperature strike in a known-coords
city, and picks one tradeable market per city.

City coordinates are city-center approximations; the Open-Meteo
ensemble grid is ~0.25°, so ~10 km precision is plenty.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.trading.signals.openmeteo_probability import parse_temp_strike

logger = logging.getLogger(__name__)

# slug -> (display name, lat, lon)
CITY_COORDS: dict[str, tuple[str, float, float]] = {
    # Asia-Pacific
    "tokyo": ("Tokyo", 35.68, 139.69),
    "osaka": ("Osaka", 34.69, 135.50),
    "seoul": ("Seoul", 37.57, 126.98),
    "beijing": ("Beijing", 39.90, 116.40),
    "shanghai": ("Shanghai", 31.23, 121.47),
    "taipei": ("Taipei", 25.03, 121.57),
    "hong-kong": ("Hong Kong", 22.32, 114.17),
    "manila": ("Manila", 14.60, 120.98),
    "bangkok": ("Bangkok", 13.76, 100.50),
    "singapore": ("Singapore", 1.35, 103.82),
    "jakarta": ("Jakarta", -6.21, 106.85),
    "mumbai": ("Mumbai", 19.08, 72.88),
    "delhi": ("Delhi", 28.61, 77.21),
    "sydney": ("Sydney", -33.87, 151.21),
    # North America
    "new-york": ("New York", 40.71, -74.01),
    "los-angeles": ("Los Angeles", 34.05, -118.24),
    "chicago": ("Chicago", 41.88, -87.63),
    "san-francisco": ("San Francisco", 37.77, -122.42),
    "seattle": ("Seattle", 47.61, -122.33),
    "denver": ("Denver", 39.74, -104.99),
    "dallas": ("Dallas", 32.78, -96.80),
    "houston": ("Houston", 29.76, -95.37),
    "atlanta": ("Atlanta", 33.75, -84.39),
    "miami": ("Miami", 25.76, -80.19),
    "boston": ("Boston", 42.36, -71.06),
    "phoenix": ("Phoenix", 33.45, -112.07),
    "las-vegas": ("Las Vegas", 36.17, -115.14),
    "toronto": ("Toronto", 43.65, -79.38),
    "vancouver": ("Vancouver", 49.28, -123.12),
    "mexico-city": ("Mexico City", 19.43, -99.13),
    # Europe
    "london": ("London", 51.51, -0.13),
    "paris": ("Paris", 48.86, 2.35),
}

_CITY_ALIASES = {
    "nyc": "new-york",
    "new york": "new-york",
    "la": "los-angeles",
    "sf": "san-francisco",
    "san francisco": "san-francisco",
    "hong kong": "hong-kong",
    "mexico city": "mexico-city",
    "las vegas": "las-vegas",
}

_CITY_RE = re.compile(
    r"[Tt]emperature in ([A-Za-z][A-Za-z .'\-]*?)(?: be | on |\?|$)"
)


def slugify_city(name: str) -> str:
    """Normalize a display name (or key) to a ``CITY_COORDS`` slug."""
    raw = re.sub(r"\s+", " ", (name or "").strip().lower())
    return _CITY_ALIASES.get(raw, raw.replace(" ", "-"))


def extract_city_key(question: str, title: str) -> str | None:
    """City slug from a temperature-market question/event title.

    Returns None when no known city is named (e.g. disease markets that
    merely mention a city without a temperature clause).
    """
    for text in (question or "", title or ""):
        match = _CITY_RE.search(text)
        if not match:
            continue
        key = slugify_city(match.group(1))
        if key in CITY_COORDS:
            return key
    return None


@dataclass
class DiscoveredMarket:
    city_key: str
    city: str
    lat: float
    lon: float
    strike_f: float
    direction: str
    condition_id: str
    token_ids: list[str]
    volume: float
    liquidity: float
    end_date_iso: str
    question: str


def discover_temperature_markets(events: list[Any]) -> list[DiscoveredMarket]:
    """Filter Gamma events down to tradeable temperature markets."""
    found: list[DiscoveredMarket] = []
    for event in events or []:
        for market in getattr(event, "markets", None) or []:
            if not getattr(market, "active", False):
                continue
            if getattr(market, "closed", True):
                continue
            if not getattr(market, "clob_token_ids", None):
                continue
            question = getattr(market, "question", "") or ""
            parsed = parse_temp_strike(question)
            if parsed is None:
                continue
            city_key = extract_city_key(question, getattr(event, "title", ""))
            if city_key is None:
                continue
            display, lat, lon = CITY_COORDS[city_key]
            strike_f, direction = parsed
            found.append(
                DiscoveredMarket(
                    city_key=city_key,
                    city=display,
                    lat=lat,
                    lon=lon,
                    strike_f=strike_f,
                    direction=direction,
                    condition_id=getattr(market, "condition_id", ""),
                    token_ids=list(market.clob_token_ids),
                    volume=float(getattr(market, "volume", 0.0) or 0.0),
                    liquidity=float(getattr(market, "liquidity", 0.0) or 0.0),
                    end_date_iso=getattr(market, "end_date_iso", "") or "",
                    question=question,
                )
            )
    return found


def pick_tradeable_markets(
    discovered: list[DiscoveredMarket],
) -> dict[str, DiscoveredMarket]:
    """One market per city: highest volume, earliest expiry on ties."""
    by_city: dict[str, list[DiscoveredMarket]] = {}
    for market in discovered:
        by_city.setdefault(market.city_key, []).append(market)
    return {
        city: sorted(group, key=lambda m: (-m.volume, m.end_date_iso))[0]
        for city, group in by_city.items()
    }


async def search_temperature_markets(
    client: Any,
    queries: tuple[str, ...] = ("highest temperature", "lowest temperature"),
    limit: int = 25,
) -> list[Any]:
    """Run Gamma full-text searches; skip per-query failures."""
    events: list[Any] = []
    for query in queries:
        try:
            events.extend(await client.search_events(query, limit=limit))
        except Exception as exc:
            logger.warning("Temperature discovery search failed %r: %s", query, exc)
    return events
