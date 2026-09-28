"""Open-Meteo ensemble probability provider for the signal feed.

Computes P(daily high crosses the market strike) as a Gaussian tail:
the Open-Meteo 51-member ensemble gives today's high median plus a
p10/p90 spread, sigma = (p90 - p10) / 2.563 (the Gaussian 10–90
interpercentile range), and the strike comes from parsing the market
question ("above 75°F" / "below 20°C", °C converted to °F).

Anything unparseable or missing returns None so the feed skips the
city — a missing probability never becomes a trade.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Feed-city coordinates (station approx). Unknown ICAOs skip cleanly.
FEED_CITY_COORDS: dict[str, tuple[float, float]] = {
    "KLGA": (40.78, -73.87),  # New York / LaGuardia
    "KLAX": (33.94, -118.41),  # Los Angeles
    "KORD": (41.97, -87.91),  # Chicago / O'Hare
}

_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
_MIN_MEMBERS = 3
_MIN_SIGMA_F = 1.0
# Gaussian 10th–90th percentile range in units of sigma.
_P90_P10_RANGE_SIGMA = 2.563

_ABOVE_WORDS = r"above|over|exceeds?|exceeding|higher than|at least|reaches?|reaching|\u003e=?"
_BELOW_WORDS = r"below|under|lower than|at most|\u003c=?"
_STRIKE_RE = re.compile(
    rf"(?P<dir>{_ABOVE_WORDS}|{_BELOW_WORDS})\s+"
    r"(?P<val>\d+(?:\.\d+)?)\s?(?:°|degrees?)?\s?(?P<unit>[CF])?",
    re.IGNORECASE,
)


def parse_temp_strike(question: str) -> Optional[tuple[float, str]]:
    """Parse (strike_°F, direction) from a market question.

    Direction is "above" or "below". Returns None when the question
    carries no recognizable temperature strike.
    """
    if not question:
        return None
    match = _STRIKE_RE.search(question)
    if not match:
        return None
    value = float(match.group("val"))
    unit = (match.group("unit") or "F").upper()
    if unit == "C":
        value = value * 9.0 / 5.0 + 32.0
    word = match.group("dir").lower()
    direction = "above" if re.fullmatch(_ABOVE_WORDS, word, re.IGNORECASE) else "below"
    return (value, direction)


def spread_sigma(p10: float, p90: float) -> float:
    """Gaussian sigma from the ensemble 10–90 spread, floored."""
    return max(_MIN_SIGMA_F, (p90 - p10) / _P90_P10_RANGE_SIGMA)


def gauss_exceed_prob(median: float, sigma: float, strike: float) -> float:
    """P(X > strike) for X ~ Normal(median, sigma)."""
    if sigma <= 0:
        return 1.0 if median > strike else 0.0
    z = (strike - median) / (sigma * math.sqrt(2.0))
    return 0.5 * math.erfc(z)


async def fetch_ensemble_daily_max(
    lat: float,
    lon: float,
    *,
    http_get: Callable[..., Any] | None = None,
) -> Optional[dict[str, Any]]:
    """Today's high median/p10/p90 across ensemble members.

    ``http_get`` is injectable for tests (async, takes url + params,
    returns a response with .raise_for_status()/.json()). Defaults to
    the shared async HTTP client.
    """
    if http_get is None:
        from ...async_infra.http_client import get_shared_client

        shared = get_shared_client()

        async def http_get(url: str, params: dict[str, Any]) -> Any:  # type: ignore[no-redef]
            return await shared.get(url, params=params)

    resp = await http_get(
        _ENSEMBLE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "timezone": "auto",
            "forecast_days": 1,
            "temperature_unit": "fahrenheit",
        },
    )
    resp.raise_for_status()
    daily = (resp.json() or {}).get("daily", {})
    highs: list[float] = []
    for key, values in daily.items():
        if key.startswith("temperature_2m_max") and key != "temperature_2m_max":
            if values and values[0] is not None:
                highs.append(float(values[0]))
    if not highs:
        raw = daily.get("temperature_2m_max", [])
        if raw and isinstance(raw, list):
            if isinstance(raw[0], list):
                highs = [float(m[0]) for m in raw if m and m[0] is not None]
            elif raw[0] is not None:
                highs = [float(raw[0])]
    if len(highs) < _MIN_MEMBERS:
        logger.debug("Ensemble data insufficient: %d members", len(highs))
        return None
    highs.sort()
    n = len(highs)
    return {
        "median": highs[n // 2],
        "p10": highs[max(0, int(n * 0.1))],
        "p90": highs[min(n - 1, int(n * 0.9))],
        "members": n,
    }


async def openmeteo_probability(
    icao: str,
    city: str,
    condition_id: str,
    token_id: str,
    *,
    gamma_client: Any | None = None,
    ensemble: Callable[..., Any] | None = None,
) -> Optional[float]:
    """Model P(payout) for a feed city, or None to skip.

    Matches the ``probability_provider(icao, city, condition_id,
    token_id)`` feed signature (sync or awaitable). ``gamma_client``
    and ``ensemble`` are injectable for tests; production defaults
    construct a GammaClient and hit the Open-Meteo ensemble API.
    """
    del city, token_id  # coords come from ICAO; payout prob is strike-based.
    coords = FEED_CITY_COORDS.get(icao)
    if coords is None:
        return None
    try:
        if gamma_client is None:
            from ..polymarket.gamma_client import GammaClient

            gamma_client = GammaClient()
        market = None
        get_markets = getattr(gamma_client, "get_markets", None)
        if callable(get_markets):
            # Condition-ID lookup via /markets?condition_ids= — the
            # /markets/{condition_id} path form 422s live (Gamma expects a
            # market slug there).
            found = await get_markets(condition_ids=[condition_id])
            market = found[0] if found else None
        else:
            market = await gamma_client.get_market(condition_id)
        if market is None or not getattr(market, "question", ""):
            return None
        parsed = parse_temp_strike(market.question)
        if parsed is None:
            logger.debug("Feed skip %s: unparseable strike %r", icao, market.question)
            return None
        strike_f, direction = parsed
        lat, lon = coords
        if ensemble is None:
            spread = await fetch_ensemble_daily_max(lat, lon)
        else:
            spread = ensemble(lat, lon)
            if hasattr(spread, "__await__"):
                spread = await spread
        if not spread:
            return None
        if int(spread.get("members", _MIN_MEMBERS)) < _MIN_MEMBERS:
            logger.debug("Feed skip %s: thin ensemble spread", icao)
            return None
        sigma = spread_sigma(float(spread["p10"]), float(spread["p90"]))
        exceed = gauss_exceed_prob(float(spread["median"]), sigma, strike_f)
        prob = exceed if direction == "above" else 1.0 - exceed
        if not 0.0 < prob < 1.0 or prob != prob:  # NaN guard
            return None
        return prob
    except Exception as exc:
        logger.warning("Open-Meteo probability failed for %s: %s", icao, exc)
        return None
