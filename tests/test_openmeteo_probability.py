"""Option (a): Open-Meteo Gaussian probability vs market strike (TDD RED).

- parse_temp_strike: pure strike parsing from market questions.
- gauss_exceed_prob / spread_sigma: pure Gaussian tail math.
- openmeteo_probability: (icao, city, cond, tok) provider with injectable
  gamma client + ensemble fetcher; None (skip) on any failure.
- production_tick: paper-loop production path wires the default provider.
"""

import asyncio

from src.trading.signals.openmeteo_probability import (
    FEED_CITY_COORDS,
    fetch_ensemble_daily_max,
    gauss_exceed_prob,
    openmeteo_probability,
    parse_temp_strike,
    spread_sigma,
)


def test_parse_above_fahrenheit():
    assert parse_temp_strike(
        "Will the high temperature in New York be above 75°F on Sep 28?"
    ) == (75.0, "above")


def test_parse_below_celsius_converts():
    assert parse_temp_strike("Chicago high below 20°C tomorrow?") == (68.0, "below")


def test_parse_unparseable_returns_none():
    assert parse_temp_strike("Will it rain in New York tomorrow?") is None
    assert parse_temp_strike("") is None


def test_gauss_at_strike_is_half():
    assert gauss_exceed_prob(75.0, 3.0, 75.0) == 0.5


def test_gauss_tail_direction():
    assert gauss_exceed_prob(80.0, 2.0, 75.0) > 0.9
    assert gauss_exceed_prob(70.0, 2.0, 75.0) < 0.1
    # Symmetry: P(X>s | med=m) == P(X<m' ...) sanity via complement.
    p = gauss_exceed_prob(77.0, 2.0, 75.0)
    assert abs(p - (1.0 - gauss_exceed_prob(73.0, 2.0, 75.0))) < 1e-9


def test_spread_sigma_floor():
    assert spread_sigma(70.0, 80.0) == (80.0 - 70.0) / 2.563
    assert spread_sigma(75.0, 75.0) == 1.0


def test_feed_city_coords_cover_defaults():
    for icao in ("KLGA", "KLAX", "KORD"):
        lat, lon = FEED_CITY_COORDS[icao]
        assert isinstance(lat, float) and isinstance(lon, float)


class _StubGamma:
    def __init__(self, question):
        self.question = question

    async def get_market(self, condition_id):
        from src.trading.polymarket.gamma_client import GammaMarket

        return GammaMarket(
            condition_id=condition_id,
            clob_token_ids=["t1", "t2"],
            question=self.question,
            description="",
            volume=100.0,
            liquidity=50.0,
            active=True,
            closed=False,
            end_date_iso="",
            neg_risk=False,
        )


def _ensemble(median=80.0, p10=77.0, p90=83.0, members=21):
    async def _fetch(lat, lon):
        assert isinstance(lat, float) and isinstance(lon, float)
        return {"median": median, "p10": p10, "p90": p90, "members": members}

    return _fetch


def test_provider_above_strike_below_median_high_prob():
    p = asyncio.run(
        openmeteo_probability(
            "KLGA",
            "new york",
            "c1",
            "t1",
            gamma_client=_StubGamma("NYC high above 75°F today?"),
            ensemble=_ensemble(median=80.0, p10=77.0, p90=83.0),
        )
    )
    assert p is not None and 0.0 < p < 1.0
    assert p > 0.9


def test_provider_below_direction_uses_cdf():
    p = asyncio.run(
        openmeteo_probability(
            "KLGA",
            "new york",
            "c1",
            "t1",
            gamma_client=_StubGamma("NYC high below 75°F today?"),
            ensemble=_ensemble(median=80.0, p10=77.0, p90=83.0),
        )
    )
    assert p is not None and 0.0 < p < 1.0
    assert p < 0.1


def test_provider_skips_cleanly():
    kw = dict(city="new york", condition_id="c1", token_id="t1")
    assert (
        asyncio.run(
            openmeteo_probability(
                "XXXX", gamma_client=_StubGamma("above 75°F?"), ensemble=_ensemble(), **kw
            )
        )
        is None
    )
    assert (
        asyncio.run(
            openmeteo_probability(
                "KLGA", gamma_client=_StubGamma("Will it rain?"), ensemble=_ensemble(), **kw
            )
        )
        is None
    )
    assert (
        asyncio.run(
            openmeteo_probability(
                "KLGA",
                gamma_client=_StubGamma("above 75°F?"),
                ensemble=_ensemble(members=2),
                **kw,
            )
        )
        is None
    )


def test_fetch_ensemble_member_format():
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "daily": {
                    "time": ["2026-09-28"],
                    "temperature_2m_max_member01": [80.0],
                    "temperature_2m_max_member02": [77.0],
                    "temperature_2m_max_member03": [83.0],
                }
            }

    async def _get(url, params):
        seen["url"] = url
        assert params["temperature_unit"] == "fahrenheit"
        return _Resp()

    out = asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, http_get=_get))
    assert seen["url"].startswith("https://ensemble-api.open-meteo.com")
    assert out == {"median": 80.0, "p10": 77.0, "p90": 83.0, "members": 3}


def test_fetch_ensemble_nested_and_thin():
    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    async def _nested(url, params):
        return _Resp({"daily": {"temperature_2m_max": [[81.0], [79.0], [82.0]]}})

    out = asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, http_get=_nested))
    assert out is not None and out["members"] == 3 and out["median"] == 81.0

    async def _thin(url, params):
        return _Resp({"daily": {"temperature_2m_max_member01": [80.0]}})

    assert (
        asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, http_get=_thin)) is None
    )


def test_production_tick_wires_default_provider(monkeypatch):
    import web.services.trading_api as tapi

    seen = {}

    async def _fake_maintenance(**kwargs):
        seen.update(kwargs)
        return {"market_map": {}, "feed": {}, "settled_tokens": []}

    monkeypatch.setattr(tapi, "run_paper_maintenance_once", _fake_maintenance)
    tapi.production_tick()
    provider = seen.get("probability_provider")
    assert provider is not None
    assert getattr(provider, "__name__", "") == "openmeteo_probability"
    assert provider.__module__.endswith("openmeteo_probability")
