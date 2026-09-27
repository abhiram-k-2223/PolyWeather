"""Step 2: market map + collector hook (TDD RED).

- Unmapped ICAO yields no signals; mapped ICAO flows to process_signal.
- Resolver picks today's market per city (active, has tokens, best volume).
- Engine.update_market_map() refreshes routing without restart.
- trading_api feed-city allowlist + Gamma refresh helper.
"""

import asyncio
from types import SimpleNamespace

from src.trading.engine.signal_ingestion import WeatherObservationSnapshot
from src.trading.engine.trading_engine import EngineConfig, TradingEngine


def _market(cond, tokens, volume, active=True, closed=False, question=""):
    return SimpleNamespace(
        condition_id=cond,
        clob_token_ids=list(tokens),
        volume=volume,
        active=active,
        closed=closed,
        question=question,
    )


def _snapshot(icao="KLGA", temp=45.0):
    return WeatherObservationSnapshot(
        city="New York",
        icao=icao,
        temperature_c=temp,
        dew_point_c=None,
        humidity_pct=None,
        wind_speed_kmh=None,
        wind_gust_kmh=None,
        pressure_hpa=None,
        condition_text="clear",
    )


def test_pick_best_market_skips_dead_and_picks_volume():
    from src.trading.polymarket.market_resolver import pick_best_market_for_city

    assert pick_best_market_for_city("new york", []) is None
    markets = [
        _market("c-closed", ["t0"], 999.0, active=True, closed=True),
        _market("c-inactive", ["t0"], 999.0, active=False, closed=False),
        _market("c-notokens", [], 999.0),
        _market("c-low", ["t-low"], 10.0),
        _market("c-high", ["t-high"], 250.0),
    ]
    assert pick_best_market_for_city("new york", markets) == ("c-high", "t-high")


def test_pick_best_market_tie_break_deterministic():
    from src.trading.polymarket.market_resolver import pick_best_market_for_city

    markets = [
        _market("c-b", ["tb"], 100.0),
        _market("c-a", ["ta"], 100.0),
    ]
    assert pick_best_market_for_city("new york", markets) == ("c-a", "ta")


def test_build_market_map_only_resolved():
    from src.trading.polymarket.market_resolver import build_market_map

    got = build_market_map({
        "KLGA": ("new york", [_market("c-ny", ["t-ny"], 50.0)]),
        "XXXX": ("nowhere", []),
    })
    assert got == {"KLGA": ("c-ny", "t-ny")}


def test_unmapped_icao_yields_no_signals():
    eng = TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )
    orders = asyncio.run(eng.process_observation(_snapshot(icao="XXXX")))
    assert orders == []
    assert eng._stats["signals_processed"] == 0
    assert eng._stats["orders_failed"] == 0


def test_update_market_map_routes_observation_to_order():
    eng = TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )
    assert eng.update_market_map({"KLGA": ("c-ny", "t-ny")}) == 1
    orders = asyncio.run(eng.process_observation(_snapshot(icao="KLGA")))
    assert len(orders) == 1
    assert eng._stats["signals_processed"] == 1
    assert eng._stats["orders_placed"] == 1
    assert eng._stats["orders_failed"] == 0


class _FakeGamma:
    def __init__(self, by_city):
        self.by_city = by_city
        self.calls = []

    def resolve_city_markets(self, city, tag="weather", active_only=True):
        self.calls.append(city)
        return self.by_city.get(city, [])


def test_refresh_market_map_from_gamma_wires_engine(monkeypatch):
    import web.services.trading_api as tapi

    monkeypatch.setenv("POLY_TRADING_FEED_CITIES", "KLGA,KLAX")
    eng = TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )
    client = _FakeGamma({
        "new york": [_market("c-ny", ["t-ny"], 5.0)],
        "los angeles": [_market("c-la", ["t-la"], 7.0)],
    })
    got = tapi.refresh_market_map_from_gamma(
        client=client, engine=eng, feed_cities=None
    )
    assert got == {"KLGA": ("c-ny", "t-ny"), "KLAX": ("c-la", "t-la")}
    assert eng._signal_ingestor.get_condition_id("KLGA") == "c-ny"
    orders = asyncio.run(eng.process_observation(_snapshot(icao="KLAX")))
    assert len(orders) == 1
    assert eng._stats["orders_failed"] == 0


def test_feed_cities_defaults_to_three():
    import web.services.trading_api as tapi

    assert tapi._feed_cities({}) == {"KLGA": "new york", "KLAX": "los angeles", "KORD": "chicago"}
