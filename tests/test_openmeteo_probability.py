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


def _ensemble(median=26.7, p10=25.6, p90=27.8, members=21):
    async def _fetch(lat, lon):
        assert isinstance(lat, float) and isinstance(lon, float)
        return {"median": median, "p10": p10, "p90": p90, "members": members}

    return _fetch


class _QueryGamma:
    """Mirrors the real GammaClient query interface: /markets?condition_ids=.

    The path-param /markets/{condition_id} lookup 422s live, so the
    provider must use this query form.
    """

    def __init__(self, question):
        self.question = question
        self.seen_condition_ids = None

    async def get_markets(self, condition_ids=None):
        from src.trading.polymarket.gamma_client import GammaMarket

        self.seen_condition_ids = list(condition_ids or [])
        return [
            GammaMarket(
                condition_id=(condition_ids or [""])[0],
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
        ]


def test_provider_uses_condition_ids_query_not_path_lookup():
    stub = _QueryGamma("NYC high above 75°F today?")
    p = asyncio.run(
        openmeteo_probability(
            "KLGA",
            "new york",
            "c1",
            "t1",
            gamma_client=stub,
            ensemble=_ensemble(median=26.7, p10=25.6, p90=27.8),
        )
    )
    assert stub.seen_condition_ids == ["c1"]
    assert p is not None and p > 0.9


def test_provider_above_strike_below_median_high_prob():
    p = asyncio.run(
        openmeteo_probability(
            "KLGA",
            "new york",
            "c1",
            "t1",
            gamma_client=_StubGamma("NYC high above 75°F today?"),
            ensemble=_ensemble(median=26.7, p10=25.6, p90=27.8),
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
            ensemble=_ensemble(median=26.7, p10=25.6, p90=27.8),
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
                    "temperature_2m_max_member01": [26.7],
                    "temperature_2m_max_member02": [25.6],
                    "temperature_2m_max_member03": [27.8],
                }
            }

    async def _get(url, params):
        seen["url"] = url
        assert "temperature_unit" not in params
        return _Resp()

    out = asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, http_get=_get))
    assert seen["url"].startswith("https://ensemble-api.open-meteo.com")
    assert out == {"median": 26.7, "p10": 25.6, "p90": 27.8, "members": 3}


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


def test_parse_live_gamma_question_forms():
    from src.trading.signals.openmeteo_probability import parse_temp_strike

    # Live form: direction implied by Highest/Lowest in question/title.
    strike, direction = parse_temp_strike(
        "Will the highest temperature in Tokyo be 19°C on September 29?",
        "Highest temperature in Tokyo on September 29?",
    )
    assert direction == "above" and strike == round(19 * 9 / 5 + 32, 6)
    strike, direction = parse_temp_strike(
        "Will the lowest temperature in Tokyo be 15°C or below on September 29?",
        "Lowest temperature in Tokyo on September 29?",
    )
    assert direction == "below"
    # Trailing direction word wins over title inference.
    _, direction = parse_temp_strike(
        "Will the highest temperature in Tokyo be 18°C or below on September 28?",
        "Highest temperature in Tokyo on September 28?",
    )
    assert direction == "below"
    # No temperature clause at all -> None.
    assert parse_temp_strike("Will West Nile cases in NYC rise?", "") is None
    # Legacy explicit forms still parse.
    assert parse_temp_strike("Will it go above 75°F?", "") == (75.0, "above")


def test_provider_resolves_coords_by_city_slug():
    from src.trading.signals.openmeteo_probability import openmeteo_probability

    q = "Will the highest temperature in Tokyo be 19°C on September 29?"
    p = asyncio.run(
        openmeteo_probability(
            "tokyo",
            "Tokyo",
            "c1",
            "t1",
            gamma_client=_QueryGamma(q),
            ensemble=_ensemble(median=26.7, p10=25.6, p90=27.8),
        )
    )
    assert p is not None and p > 0.9
    # Unknown key stays unresolved even with a known display name:
    # unconfigured keys must never trade.
    p2 = asyncio.run(
        openmeteo_probability(
            "XXXX",
            "Tokyo",
            "c1",
            "t1",
            gamma_client=_QueryGamma(q),
            ensemble=_ensemble(median=26.7, p10=25.6, p90=27.8),
        )
    )
    assert p2 is None


def test_ensemble_request_uses_native_celsius():
    """Ensemble API 400s on temperature_unit=fahrenheit (forecast-only
    param) — the request must not send it; spread comes back in °C."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "daily": {
                    "temperature_2m_max_member01": [26.7],
                    "temperature_2m_max_member02": [25.6],
                    "temperature_2m_max_member03": [27.8],
                }
            }

    seen = {}

    async def _get(url, params):
        seen.update(params)
        return _Resp()

    out = asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, http_get=_get))
    assert "temperature_unit" not in seen
    assert out == {"median": 26.7, "p10": 25.6, "p90": 27.8, "members": 3}


def test_ensemble_request_selects_explicit_model():
    """Ensemble API 400s on the default best_match model — the request
    must name a specific ensemble model (icon_seamless, global)."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"daily": {"temperature_2m_max_member01": [26.7]}}

    seen = {}

    async def _get(url, params):
        seen.update(params)
        return _Resp()

    asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, http_get=_get))
    assert seen.get("models") == "icon_seamless"


def test_fahrenheit_strike_vs_celsius_ensemble_like_for_like():
    """°F strike (75°F = 23.9°C) vs °C ensemble spread must compare in
    the same units: median 26.7°C well above the strike -> high prob."""
    p = asyncio.run(
        openmeteo_probability(
            "KLGA",
            "new york",
            "c1",
            "t1",
            gamma_client=_QueryGamma("NYC high above 75°F today?"),
            ensemble=_ensemble(median=26.7, p10=25.6, p90=27.8),
        )
    )
    assert p is not None and p > 0.9


def test_ensemble_min_variable_requested():
    """stat='min' must request temperature_2m_min (lowest markets need
    the daily LOW ensemble, not the high)."""
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "daily": {
                    "time": ["2026-09-30"],
                    "temperature_2m_min_member01": [4.0],
                    "temperature_2m_min_member02": [3.0],
                    "temperature_2m_min_member03": [5.0],
                }
            }

    async def _get(url, params):
        seen["daily"] = params.get("daily")
        return _Resp()

    out = asyncio.run(fetch_ensemble_daily_max(40.78, -73.87, stat="min", http_get=_get))
    assert seen["daily"] == "temperature_2m_min"
    assert out == {"median": 4.0, "p10": 3.0, "p90": 5.0, "members": 3}


def test_below_market_uses_min_ensemble_above_uses_max(monkeypatch):
    """Provider must route lowest-markets to the min ensemble and
    highest-markets to the max ensemble (like-for-like variable)."""
    import importlib

    omp = importlib.import_module("src.trading.signals.openmeteo_probability")

    calls = []

    async def _fake_fetch(lat, lon, **kw):
        calls.append(kw.get("stat", "max"))
        return {"median": 4.0, "p10": 3.0, "p90": 5.0, "members": 21}

    monkeypatch.setattr(omp, "fetch_ensemble_daily_max", _fake_fetch)
    low = asyncio.run(
        openmeteo_probability(
            "tokyo",
            "Tokyo",
            "c-low",
            "t-low",
            gamma_client=_QueryGamma(
                "Will the lowest temperature in Tokyo be 5°C on September 30?"
            ),
            ensemble=None,
        )
    )
    high = asyncio.run(
        openmeteo_probability(
            "tokyo",
            "Tokyo",
            "c-high",
            "t-high",
            gamma_client=_QueryGamma(
                "Will the highest temperature in Tokyo be 25°C on September 30?"
            ),
            ensemble=None,
        )
    )
    assert calls == ["min", "max"]
    assert low is not None and high is not None
