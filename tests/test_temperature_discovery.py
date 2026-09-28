"""Global temperature-market discovery (TDD RED).

The feed must consider every city Polymarket lists temperature markets
for — including Asian cities — not just 3 hardcoded US ICAOs. Discovery
searches Gamma for temperature markets, keeps only active markets with a
parseable strike in a known-coords city, and picks one tradeable market
per city (highest volume, earliest expiry on ties).
"""

import asyncio

from src.trading.polymarket.gamma_client import GammaEvent, GammaMarket
from src.trading.signals.temperature_discovery import (
    CITY_COORDS,
    discover_temperature_markets,
    extract_city_key,
    pick_tradeable_markets,
    search_temperature_markets,
)


def _market(question, condition_id="c", volume=100.0, active=True, closed=False,
            end="2026-09-29T00:00:00Z", tokens=("t1", "t2")):
    return GammaMarket(
        condition_id=condition_id,
        clob_token_ids=list(tokens),
        question=question,
        description="",
        volume=volume,
        liquidity=50.0,
        active=active,
        closed=closed,
        end_date_iso=end,
        neg_risk=False,
    )


def _event(title, *markets):
    return GammaEvent(event_slug="s", title=title, markets=list(markets))


def test_asian_cities_have_coords():
    for city in ("tokyo", "singapore", "hong-kong", "seoul", "osaka"):
        assert city in CITY_COORDS, city
        lat, lon = CITY_COORDS[city][1], CITY_COORDS[city][2]
        assert -10.0 < lat < 45.0 and 70.0 < lon < 155.0, (city, lat, lon)


def test_extract_city_key_from_live_style_questions():
    assert extract_city_key(
        "Will the highest temperature in Tokyo be 19°C on September 29?", ""
    ) == "tokyo"
    assert extract_city_key(
        "Will the highest temperature in Singapore be 30°C on September 28?", ""
    ) == "singapore"
    assert extract_city_key("Will NYC cases rise?", "") in (None, "new-york")
    assert extract_city_key("Will West Nile cases in NYC rise?", "") is None


def test_discover_keeps_only_tradeable_temperature_markets():
    events = [
        _event(
            "Highest temperature in Tokyo on September 29?",
            _market("Will the highest temperature in Tokyo be 19°C on September 29?",
                    condition_id="tok-c1", volume=500.0),
            _market("Will the highest temperature in Tokyo be 20°C on September 29?",
                    condition_id="tok-c2", volume=900.0),
        ),
        _event(
            "West Nile in NYC?",
            _market("Will West Nile cases in NYC rise?", condition_id="dis-c1",
                    volume=99999.0),
        ),
        _event(
            "Old Tokyo market",
            _market("Will the highest temperature in Tokyo be 19°C on June 1?",
                    condition_id="tok-old", volume=5.0, closed=True),
        ),
    ]
    found = discover_temperature_markets(events)
    ids = {m.condition_id for m in found}
    assert ids == {"tok-c1", "tok-c2"}
    assert all(m.city_key == "tokyo" for m in found)


def test_pick_one_per_city_highest_volume_earliest_expiry_tie():
    events = [
        _event(
            "Tokyo Sep 29",
            _market("Will the highest temperature in Tokyo be 19°C on September 29?",
                    condition_id="a", volume=500.0, end="2026-09-29T00:00:00Z"),
            _market("Will the highest temperature in Tokyo be 20°C on September 29?",
                    condition_id="b", volume=900.0, end="2026-09-29T00:00:00Z"),
        ),
        _event(
            "Singapore Sep 28",
            _market("Will the highest temperature in Singapore be 30°C on September 28?",
                    condition_id="c", volume=100.0, end="2026-09-28T00:00:00Z"),
            _market("Will the highest temperature in Singapore be 30°C on September 28?",
                    condition_id="d", volume=100.0, end="2026-09-30T00:00:00Z"),
        ),
    ]
    picked = pick_tradeable_markets(discover_temperature_markets(events))
    assert set(picked) == {"tokyo", "singapore"}
    assert picked["tokyo"].condition_id == "b"
    assert picked["singapore"].condition_id == "c"


def test_search_wrapper_skips_failed_queries():
    class _Client:
        async def search_events(self, query, limit=25, page=1):
            if "lowest" in query:
                raise RuntimeError("boom")
            return [_event("t", _market("q"))]

    events = asyncio.run(
        search_temperature_markets(_Client(), queries=("highest x", "lowest x"))
    )
    assert len(events) == 1
