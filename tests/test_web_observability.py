
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from starlette.requests import Request

import web.core as web_core
from web.app import app
import web.routes as routes
import web.services.ops_api as ops_api
import web.scan_terminal_cache as scan_terminal_cache
import web.scan_terminal_service as scan_terminal_service
import web.services.city_api as city_api
import web.services.city_runtime as city_runtime
from web.services.observation_freshness import build_observation_freshness
from web.scan_terminal_cache import scan_terminal_cache_key
from src.database.runtime_state import TruthRecordRepository


client = TestClient(app)


def test_healthz_returns_ok_shape():
    response = client.get('/healthz')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] in {'ok', 'degraded'}
    assert 'db' in payload
    assert 'state_storage_mode' in payload
    assert 'cities_count' in payload


def test_healthz_keeps_liveness_200_when_db_health_is_degraded(monkeypatch):
    from web.services import system_api

    monkeypatch.setattr(
        system_api,
        "build_health_payload",
        lambda: {
            "status": "degraded",
            "time_utc": "2026-05-30T00:00:00+00:00",
            "db": {"ok": False, "error": "database is locked"},
            "state_storage_mode": "sqlite",
            "cities_count": 50,
        },
    )

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_system_status_requires_ops_admin():
    response = client.get('/api/system/status')
    assert response.status_code in {401, 403, 503}


def test_system_status_returns_summary_shape_for_ops_admin(monkeypatch):
    monkeypatch.setattr(
        routes,
        "_require_ops_admin",
        lambda request: {"user_id": "admin-user", "email": "admin@example.com"},
    )

    response = client.get('/api/system/status')
    assert response.status_code == 200
    payload = response.json()
    assert 'db' in payload
    assert 'state_storage_mode' in payload
    assert 'features' in payload
    assert 'integrations' in payload
    assert 'cache' in payload
    assert 'analysis' in payload['cache']
    assert 'probability' in payload
    assert payload['probability']['engine_mode'] == 'legacy'
    assert 'training_data' in payload
    assert 'station_networks' in payload
    assert 'realtime' in payload
    assert payload['realtime']['store'] in {'sqlite', 'redis', 'degraded_sqlite'}
    assert 'latest_revision' in payload['realtime']
    assert 'sse_connections' in payload['realtime']
    assert 'truth_records' in payload['training_data']
    assert 'training_features' in payload['training_data']
    assert 'city_coverage' in payload['training_data']
    assert 'model_city_coverage' in payload['training_data']
    assert 'metar_entries' in payload['cache']
    assert 'cities_count' in payload


def test_observation_freshness_accepts_epoch_seconds():
    now = datetime.fromtimestamp(1780169100, tz=timezone.utc)

    payload = build_observation_freshness(
        source_code="mgm",
        observed_at=1780168800,
        now_utc=now,
    )

    assert payload["freshness_status"] == "fresh"
    assert payload["freshness_reason"] == "within_native_fresh_window"
    assert payload["age_sec"] == 300
    assert payload["observed_at"].startswith("2026-")


def test_metrics_endpoint_requires_ops_admin():
    response = client.get('/metrics')
    assert response.status_code in {401, 403, 503}


def test_metrics_endpoint_returns_prometheus_payload_for_ops_admin(monkeypatch):
    monkeypatch.setattr(
        routes,
        "_require_ops_admin",
        lambda request: {"user_id": "admin-user", "email": "admin@example.com"},
    )

    response = client.get('/metrics')
    assert response.status_code == 200
    assert 'polyweather_http_requests_total' in response.text


def test_system_cache_status_requires_ops_admin(monkeypatch):
    monkeypatch.setattr(routes, "_assert_entitlement", lambda request: None)

    response = client.get("/api/system/cache-status?cities=shanghai")

    assert response.status_code in {401, 403, 503}


def test_standard_growth_funnel_events_are_trackable():
    assert {
        "landing_view",
        "enter_terminal",
        "login_start",
        "signup_success",
        "trial_created",
        "payment_start",
        "payment_success",
        "degraded_auth_profile",
    }.issubset(city_runtime.TRACKABLE_ANALYTICS_EVENTS)


def test_standard_growth_funnel_summary_order(monkeypatch):
    from src.database.db_manager import DBManager

    rows = [
        {"id": 1, "event_type": "landing_view", "user_id": "", "client_id": "c1", "session_id": "s1"},
        {"id": 2, "event_type": "enter_terminal", "user_id": "", "client_id": "c1", "session_id": "s1"},
        {"id": 3, "event_type": "login_start", "user_id": "", "client_id": "c1", "session_id": "s1"},
        {"id": 4, "event_type": "signup_success", "user_id": "u1", "client_id": "c1", "session_id": "s1"},
        {"id": 5, "event_type": "trial_created", "user_id": "u1", "client_id": "c1", "session_id": "s1"},
        {"id": 6, "event_type": "payment_start", "user_id": "u1", "client_id": "c1", "session_id": "s1"},
        {"id": 7, "event_type": "payment_success", "user_id": "u1", "client_id": "c1", "session_id": "s1"},
        {"id": 8, "event_type": "degraded_auth_profile", "user_id": "", "client_id": "auth:u1", "session_id": "", "payload": {"reason": "backend_500"}},
    ]
    monkeypatch.setattr(
        DBManager,
        "list_app_analytics_events",
        lambda self, limit=5000, since_iso=None: rows,
    )

    summary = DBManager().get_app_analytics_funnel_summary(days=7)
    assert list(summary["events"].keys()) == [
        "landing_view",
        "enter_terminal",
        "login_start",
        "signup_success",
        "trial_created",
        "payment_start",
        "payment_success",
    ]
    assert summary["rates"]["payment_success_rate"] == 1.0
    assert summary["diagnostics"]["degraded_auth_profile"]["total"] == 1
    assert summary["diagnostics"]["degraded_auth_profile"]["by_reason"][0] == {
        "name": "backend_500",
        "count": 1,
    }


def test_growth_funnel_summarizes_traffic_sources(monkeypatch):
    from src.database.db_manager import DBManager

    rows = [
        {
            "id": 1,
            "event_type": "landing_view",
            "user_id": "",
            "client_id": "c1",
            "session_id": "s1",
            "payload": {
                "referrer": "https://x.com/polyweather",
                "cf_country": "us",
                "device_type": "mobile",
                "path": "/",
            },
        },
        {
            "id": 2,
            "event_type": "landing_view",
            "user_id": "",
            "client_id": "c2",
            "session_id": "s2",
            "payload": {
                "referrer": "",
                "cf_country": "hk",
                "device_type": "desktop",
                "path": "/?ref=abc",
            },
        },
    ]
    monkeypatch.setattr(
        DBManager,
        "list_app_analytics_events",
        lambda self, limit=20000, since_iso=None: rows,
    )

    summary = DBManager().get_app_analytics_funnel_summary(days=7)

    assert summary["traffic"]["referrers"][0] == {"name": "x.com", "count": 1}
    assert {"name": "(direct)", "count": 1} in summary["traffic"]["referrers"]
    assert {"name": "US", "count": 1} in summary["traffic"]["countries"]
    assert {"name": "mobile", "count": 1} in summary["traffic"]["devices"]


def test_ops_source_health_flags_expected_official_sources(monkeypatch):
    class FakeCache:
        def get_city_cache(self, kind, city):
            if kind != "full":
                return None
            payloads = {
                "ankara": {
                    "airport_primary": {
                        "source_code": "mgm",
                        "source_label": "MGM",
                        "obs_age_min": 80,
                        "temp": 17,
                    }
                },
                "amsterdam": {
                    "airport_primary": {
                        "source_code": "knmi",
                        "source_label": "KNMI",
                        "obs_age_min": 5,
                        "temp": 19,
                    }
                },
                "tel aviv": {
                    "airport_current": {
                        "source_code": "metar",
                        "source_label": "METAR",
                        "obs_age_min": 5,
                        "temp": 25,
                    }
                },
            }
            payload = payloads.get(city)
            if not payload:
                return None
            return {
                "payload": payload,
                "updated_at": "2026-05-31T10:00:00Z",
                "updated_at_ts": 1,
            }

    monkeypatch.setattr(ops_api.legacy_routes, "_require_ops_admin", lambda request: {"email": "ops@example.com"})
    monkeypatch.setattr(ops_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(
        ops_api.legacy_routes,
        "CITIES",
        {"ankara": {}, "amsterdam": {}, "tel aviv": {}},
        raising=False,
    )

    payload = ops_api.get_ops_source_health(None, limit=10)
    by_city = {row["city"]: row for row in payload["cities"]}

    assert by_city["ankara"]["worst_status"] == "stale"
    assert any(source["source_code"] == "mgm" for source in by_city["ankara"]["sources"])
    assert by_city["amsterdam"]["worst_status"] == "fresh"
    assert any(
        source["source_code"] == "ims" and source["status"] == "missing"
        for source in by_city["tel aviv"]["sources"]
    )









def test_cities_endpoint_uses_denver_display_name_for_aurora_market():
    response = client.get("/api/cities")
    assert response.status_code == 200
    payload = response.json()
    denver = next(item for item in payload["cities"] if item["name"] == "denver")
    assert denver["display_name"] == "Denver"
    assert denver["network_provider"] == "global_metar"
    assert denver["deb_recent_tier"] in {"high", "medium", "low", "other"}
    assert "deb_recent_sample_count" in denver


def test_cities_endpoint_includes_new_wunderground_cities():
    response = client.get("/api/cities")
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["cities"]}
    assert {
        "busan",
        "qingdao",
        "panama city",
        "kuala lumpur",
        "jakarta",
        "helsinki",
        "amsterdam",
    }.issubset(names)


def test_cities_endpoint_does_not_block_on_recent_deb_index(monkeypatch):
    monkeypatch.setattr(city_api, "_RECENT_DEB_CACHE", None, raising=False)
    monkeypatch.setattr(city_api, "_RECENT_DEB_CACHE_TS", 0.0, raising=False)
    monkeypatch.setattr(city_api, "_RECENT_DEB_REFRESHING", False, raising=False)
    monkeypatch.setattr(city_api, "_get_recent_deb_cache", lambda: None, raising=False)
    monkeypatch.setattr(city_api, "_start_recent_deb_refresh", lambda: None, raising=False)

    def fail_recent_index():
        raise AssertionError("recent DEB stats must not run in the default city-list request")

    monkeypatch.setattr(
        city_api.legacy_routes,
        "_build_recent_deb_performance_index",
        fail_recent_index,
    )

    response = client.get("/api/cities")

    assert response.status_code == 200
    denver = next(item for item in response.json()["cities"] if item["name"] == "denver")
    assert denver["deb_recent_tier"] == "other"
    assert denver["deb_recent_sample_count"] == 0




def test_city_detail_batch_endpoint_builds_multiple_cached_details(monkeypatch):
    calls = []

    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_city_cache_is_fresh",
        lambda entry, ttl: True,
    )
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_overlay_latest_wunderground_current",
        lambda city, payload: {**payload, "overlay_city": city},
    )

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return {
                "payload": {
                    "city": city,
                    "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
                }
            }

    def build_detail(data, market_slug, target_date, resolution):
        calls.append((data["city"], resolution))
        return {
            "city": data["city"],
            "hourly": data["hourly"],
            "resolution": resolution,
            "overlay_city": data["overlay_city"],
        }

    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    response = client.get("/api/cities/detail-batch?cities=Shanghai,Paris&resolution=10m")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cities"] == ["shanghai", "paris"]
    assert sorted(payload["details"]) == ["paris", "shanghai"]
    assert payload["details"]["shanghai"]["resolution"] == "10m"
    assert payload["details"]["paris"]["overlay_city"] == "paris"
    assert sorted(calls) == [("paris", "10m"), ("shanghai", "10m")]


def test_city_detail_batch_chart_scope_returns_only_chart_fields(monkeypatch):
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_city_cache_is_fresh",
        lambda entry, ttl: True,
    )
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_overlay_latest_wunderground_current",
        lambda city, payload: payload,
    )

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return {
                "payload": {
                    "name": city,
                    "display_name": city.title(),
                    "local_date": "2026-05-30",
                    "local_time": "15:20",
                    "temp_symbol": "°C",
                    "current": {
                        "temp": 20.0,
                        "settlement_source": "metar",
                        "settlement_source_label": "METAR",
                    },
                    "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
                    "forecast": {
                        "today_high": 22.0,
                        "daily": [{"date": "2026-05-30", "max_temp": 22.0}],
                    },
                    "multi_model": {
                        "hourly_times": ["15:00"],
                        "hourly_forecasts": {"ECMWF": [21.0]},
                    },
                    "deb": {"prediction": 21.5, "hourly_path": {"times": ["15:00"], "temps": [21.5]}},
                    "probabilities": {"mu": 21.4, "distribution": [{"value": 21, "probability": 0.4}]},
                    "runway_plate_history": {"01/19": [{"time": "2026-05-30T15:20:00Z", "temp": 20.1}]},
                    "airport_current": {"temp": 20.0},
                    "airport_primary": {"temp": 20.0},
                    "airport_primary_today_obs": [["15:20", 20.0]],
                    "wunderground_current": {"max_so_far": 20.5},
                    "settlement_station": {"settlement_station_label": "Station"},
                    "amos": {"runway_obs": {"point_temperatures": []}},
                    "metar_today_obs": [{"time": "15:20", "temp": 20.0}],
                    "settlement_today_obs": [],
                    "dynamic_commentary": {"summary": "large text"},
                    "official_nearby": [{"name": "unused"}],
                    "taf": {"raw": "unused"},
                    "ai_analysis": "unused",
                }
            }

    def build_detail(_data, _market_slug, _target_date, _resolution):
        raise AssertionError("chart scope must not build the full city detail payload")

    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    response = client.get("/api/cities/detail-batch?cities=Paris&resolution=10m&scope=chart")

    assert response.status_code == 200
    detail = response.json()["details"]["paris"]
    assert detail["timeseries"]["hourly"]["temps"] == [20.0]
    assert detail["models_hourly"]["curves"]["ECMWF"] == [21.0]
    assert detail["deb"]["hourly_path"]["temps"] == [21.5]
    assert detail["airport_primary_today_obs"] == [["15:20", 20.0]]
    assert "dynamic_commentary" not in detail
    assert "official_nearby" not in detail
    assert "taf" not in detail
    assert "ai_analysis" not in detail


def test_chart_scope_overlays_collector_runway_history_from_db(monkeypatch):
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_city_cache_is_fresh",
        lambda entry, ttl: True,
    )
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_overlay_latest_wunderground_current",
        lambda city, payload: payload,
    )

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return {
                "payload": {
                    "name": city,
                    "display_name": city.title(),
                    "local_date": "2026-06-06",
                    "local_time": "13:28",
                    "temp_symbol": "°C",
                    "risk": {"icao": "ZSPD"},
                    "current": {
                        "temp": 25.0,
                        "settlement_source": "metar",
                        "settlement_source_label": "METAR",
                    },
                    "hourly": {"times": ["13:00"], "temps": [25.0]},
                    "forecast": {"today_high": 26.0, "daily": []},
                    "multi_model": {},
                    "deb": {"prediction": 26.0},
                    "probabilities": {"mu": 26.0, "distribution": []},
                    "runway_plate_history": {
                        "35R/17L": [{"time": "2026-06-06T05:21:00+00:00", "temp": 24.2}]
                    },
                }
            }

        def get_runway_obs_recent(self, icao, minutes=60):
            assert icao == "ZSPD"
            assert minutes == 36 * 60
            return [
                {
                    "runway": "35R/17L",
                    "tdz_temp": 24.2,
                    "mid_temp": None,
                    "end_temp": 24.0,
                    "target_runway_max": 24.2,
                    "otime_utc": "2026-06-06T05:21:00+00:00",
                },
                {
                    "runway": "35R/17L",
                    "tdz_temp": 24.8,
                    "mid_temp": None,
                    "end_temp": 24.6,
                    "target_runway_max": 24.8,
                    "otime_utc": "2026-06-06T05:28:00+00:00",
                },
            ]

    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())

    response = client.get("/api/cities/detail-batch?cities=Shanghai&resolution=1m&scope=chart")

    assert response.status_code == 200
    history = response.json()["details"]["shanghai"]["runway_plate_history"]["35R/17L"]
    assert history[-1] == {"time": "2026-06-06T05:28:00+00:00", "temp": 24.8}


def test_chart_data_cache_hit_starts_full_stale_refresh(monkeypatch):
    import asyncio

    refresh_calls = []

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return {
                "payload": {
                    "name": city,
                    "display_name": city.title(),
                    "hourly": {"times": ["13:00"], "temps": [25.0]},
                },
            }

        def get_runway_obs_recent(self, icao, minutes=60):
            return []

    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_city_cache_is_fresh",
        lambda entry, ttl: False,
    )
    monkeypatch.setattr(
        city_api,
        "_start_city_full_stale_refresh",
        refresh_calls.append,
    )
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_overlay_latest_wunderground_current",
        lambda city, payload: payload,
    )

    payload = asyncio.run(city_api._get_city_chart_data("paris", force_refresh=False))

    assert payload["hourly"]["temps"] == [25.0]
    assert refresh_calls == ["paris"]


def test_chart_data_returns_cached_payload_when_optional_overlay_times_out(monkeypatch):
    import asyncio

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return {
                "payload": {
                    "name": city,
                    "display_name": city.title(),
                    "risk": {"icao": "ZSPD"},
                    "hourly": {"times": ["13:00"], "temps": [25.0]},
                    "runway_plate_history": {
                        "35R/17L": [{"time": "2026-06-06T05:21:00+00:00", "temp": 24.2}]
                    },
                },
            }

        def get_runway_obs_recent(self, icao, minutes=60):
            return [
                {
                    "runway": "35R/17L",
                    "target_runway_max": 24.8,
                    "otime_utc": "2026-06-06T05:28:00+00:00",
                }
            ]

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api._overlay_cached_runway_history_from_db:
            await asyncio.sleep(0.05)
        return fn(*args, **kwargs)

    monkeypatch.setenv("POLYWEATHER_CITY_CHART_OPTIONAL_OVERLAY_TIMEOUT_MS", "1")
    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(
        city_api.legacy_routes,
        "_overlay_latest_wunderground_current",
        lambda city, payload: payload,
    )

    payload = asyncio.run(city_api._get_city_chart_data("shanghai", force_refresh=False))

    assert payload["runway_plate_history"]["35R/17L"] == [
        {"time": "2026-06-06T05:21:00+00:00", "temp": 24.2}
    ]


def test_chart_detail_payload_uses_threadpool_and_reuses_short_cache(monkeypatch):
    import asyncio

    build_calls = 0
    threadpool_calls = 0

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        nonlocal threadpool_calls
        threadpool_calls += 1
        await asyncio.sleep(0)
        return fn(*args, **kwargs)

    def build_chart_detail(data, resolution):
        nonlocal build_calls
        build_calls += 1
        return {
            "city": data["city"],
            "resolution": resolution,
            "hourly": data["hourly"],
        }

    city_api._CITY_CHART_DETAIL_PAYLOAD_CACHE.clear()
    city_api._CITY_CHART_DETAIL_PAYLOAD_CACHE_TS.clear()
    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_PAYLOAD_CACHE_TTL_SEC", "20")
    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_chart_detail_payload", build_chart_detail)

    data = {
        "city": "paris",
        "updated_at": "2026-05-30T15:00:00Z",
        "hourly": {"times": ["2026-05-30T15:00:00Z"], "temps": [20.0]},
    }

    first = asyncio.run(city_api._build_city_chart_detail_payload(data, "10m"))
    second = asyncio.run(city_api._build_city_chart_detail_payload(data, "10m"))

    assert first == second
    assert first["resolution"] == "10m"
    assert build_calls == 1
    assert threadpool_calls == 1


def test_city_detail_batch_partial_timeout_default_stays_below_proxy_budget(monkeypatch):
    monkeypatch.delenv("POLYWEATHER_CITY_DETAIL_BATCH_PARTIAL_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("POLYWEATHER_CITY_DETAIL_BATCH_CONCURRENCY", raising=False)
    monkeypatch.delenv("POLYWEATHER_CITY_DETAIL_BATCH_GLOBAL_CONCURRENCY", raising=False)
    monkeypatch.delenv("POLYWEATHER_CITY_DETAIL_BATCH_QUEUE_WAIT_MS", raising=False)

    assert city_api._city_detail_batch_concurrency() == 1
    assert city_api._city_detail_batch_global_concurrency() == 1
    assert city_api._city_detail_batch_queue_wait_seconds() == 3.0
    assert city_api._city_detail_batch_partial_timeout_seconds() == 8.0


def test_city_detail_batch_waits_briefly_for_global_builder_slot(monkeypatch):
    import asyncio
    import threading

    build_calls = 0

    async def build_batch_item(city, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return city, {"city": city}

    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_GLOBAL_CONCURRENCY", "1")
    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_QUEUE_WAIT_MS", "200")
    monkeypatch.setattr(city_api, "_CITY_DETAIL_BATCH_BUILD_SEMAPHORE", None)
    monkeypatch.setattr(city_api, "_CITY_DETAIL_BATCH_BUILD_SEMAPHORE_SIZE", 0)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api, "_build_city_detail_batch_item_async", build_batch_item)

    semaphore = city_api._city_detail_batch_build_semaphore()
    assert semaphore.acquire(blocking=False) is True
    release_timer = threading.Timer(0.02, semaphore.release)
    release_timer.start()
    try:
        payload = asyncio.run(
            city_api.get_city_detail_batch_payload(
                object(),
                cities="Wait-Paris,Wait-Shanghai",
                resolution="10m",
                limit=2,
            )
        )
    finally:
        release_timer.join(timeout=1)

    assert payload["partial"] is False
    assert payload.get("busy") is not True
    assert sorted(payload["details"]) == ["wait-paris", "wait-shanghai"]
    assert payload["missing"] == []
    assert payload["diagnostics"]["response_source"] == "fresh_build"
    assert build_calls == 2


def test_city_detail_batch_returns_busy_when_global_builder_slot_is_full(monkeypatch):
    import asyncio

    build_calls = 0

    async def build_batch_item(city, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return city, {"city": city}

    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_GLOBAL_CONCURRENCY", "1")
    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_QUEUE_WAIT_MS", "10")
    monkeypatch.setattr(city_api, "_CITY_DETAIL_BATCH_BUILD_SEMAPHORE", None)
    monkeypatch.setattr(city_api, "_CITY_DETAIL_BATCH_BUILD_SEMAPHORE_SIZE", 0)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api, "_build_city_detail_batch_item_async", build_batch_item)

    semaphore = city_api._city_detail_batch_build_semaphore()
    assert semaphore.acquire(blocking=False) is True
    try:
        payload = asyncio.run(
            city_api.get_city_detail_batch_payload(
                object(),
                cities="Busy-Paris,Busy-Shanghai",
                resolution="10m",
                limit=2,
            )
        )
    finally:
        semaphore.release()

    assert payload["partial"] is True
    assert payload["busy"] is True
    assert payload["details"] == {}
    assert payload["missing"] == ["busy-paris", "busy-shanghai"]
    assert payload["diagnostics"]["partial_reason"] == "busy"
    assert payload["diagnostics"]["response_source"] == "busy"
    assert payload["diagnostics"]["requested_count"] == 2
    assert payload["diagnostics"]["completed_count"] == 0
    assert payload["diagnostics"]["missing_count"] == 2
    assert payload["diagnostics"]["city_status"]["busy-paris"]["status"] == "busy"
    assert payload["diagnostics"]["city_status"]["busy-shanghai"]["status"] == "busy"
    assert build_calls == 0


def test_city_detail_batch_endpoint_limits_backend_concurrency(monkeypatch):
    import asyncio

    active = 0
    max_active = 0

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.01)
            return fn(*args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_CONCURRENCY", "2")
    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_city_cache_is_fresh", lambda entry, ttl: False)

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return None

    def refresh_full(city, force_refresh):
        return {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
        }

    def build_detail(data, market_slug, target_date, resolution):
        return {
            "city": data["city"],
            "hourly": data["hourly"],
            "resolution": resolution,
        }

    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_full_cache", refresh_full)
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    response = client.get("/api/cities/detail-batch?cities=a,b,c,d,e&resolution=10m&limit=5")

    assert response.status_code == 200
    assert response.json()["cities"] == ["a", "b", "c", "d", "e"]
    assert max_active <= 2


def test_city_detail_batch_returns_completed_details_when_one_city_is_slow(monkeypatch):
    import asyncio

    completed = []

    async def build_batch_item(city, **kwargs):
        if city == "slow":
            await asyncio.sleep(0.08)
        completed.append(city)
        return city, {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
            "resolution": kwargs.get("resolution"),
        }

    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_PARTIAL_TIMEOUT_MS", "20")
    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_CONCURRENCY", "2")
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api, "_build_city_detail_batch_item_async", build_batch_item)

    payload = asyncio.run(
        city_api.get_city_detail_batch_payload(
            object(),
            cities="fast,slow,other",
            resolution="10m",
            limit=3,
        )
    )

    assert payload["cities"] == ["fast", "slow", "other"]
    assert sorted(payload["details"]) == ["fast", "other"]
    assert payload["details"]["fast"]["resolution"] == "10m"
    assert payload["partial"] is True
    assert payload["missing"] == ["slow"]
    assert payload["errors"] == {}
    assert payload["diagnostics"]["partial_reason"] == "timeout"
    assert payload["diagnostics"]["requested_count"] == 3
    assert payload["diagnostics"]["completed_count"] == 2
    assert payload["diagnostics"]["missing_count"] == 1
    assert payload["diagnostics"]["error_count"] == 0
    assert payload["diagnostics"]["batch_concurrency"] == 2
    assert payload["diagnostics"]["partial_timeout_ms"] == 20
    assert payload["diagnostics"]["city_status"]["fast"]["status"] == "ok"
    assert payload["diagnostics"]["city_status"]["other"]["status"] == "ok"
    assert payload["diagnostics"]["city_status"]["slow"]["status"] == "timeout"
    assert isinstance(payload["diagnostics"]["city_status"]["fast"]["duration_ms"], (int, float))
    assert "slow" not in completed


def test_city_detail_batch_response_cache_keeps_entitlement_check(monkeypatch):
    import asyncio

    entitlement_calls = 0
    build_calls = 0

    async def build_batch_item(city, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return city, {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
            "resolution": kwargs.get("resolution"),
        }

    def assert_entitlement(request):
        nonlocal entitlement_calls
        entitlement_calls += 1

    city_api._CITY_DETAIL_BATCH_RESPONSE_CACHE.clear()
    city_api._CITY_DETAIL_BATCH_RESPONSE_CACHE_TS.clear()
    city_api._CITY_DETAIL_BATCH_RESPONSE_INFLIGHT.clear()

    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_RESPONSE_CACHE_TTL_SEC", "20")
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", assert_entitlement)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api, "_build_city_detail_batch_item_async", build_batch_item)

    first = asyncio.run(
        city_api.get_city_detail_batch_payload(
            object(),
            cities="Paris",
            resolution="10m",
            limit=12,
        )
    )
    second = asyncio.run(
        city_api.get_city_detail_batch_payload(
            object(),
            cities="Paris",
            resolution="10m",
            limit=12,
        )
    )

    assert first == second
    assert first["details"]["paris"]["resolution"] == "10m"
    assert entitlement_calls == 2
    assert build_calls == 1


def test_concurrent_city_detail_batch_requests_share_inflight_response(monkeypatch):
    import asyncio

    entitlement_calls = 0
    build_calls = 0

    async def build_batch_item(city, **kwargs):
        nonlocal build_calls
        build_calls += 1
        await asyncio.sleep(0.02)
        return city, {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
            "resolution": kwargs.get("resolution"),
        }

    def assert_entitlement(request):
        nonlocal entitlement_calls
        entitlement_calls += 1

    city_api._CITY_DETAIL_BATCH_RESPONSE_CACHE.clear()
    city_api._CITY_DETAIL_BATCH_RESPONSE_CACHE_TS.clear()
    city_api._CITY_DETAIL_BATCH_RESPONSE_INFLIGHT.clear()

    monkeypatch.setenv("POLYWEATHER_CITY_DETAIL_BATCH_RESPONSE_CACHE_TTL_SEC", "20")
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", assert_entitlement)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api, "_build_city_detail_batch_item_async", build_batch_item)

    async def run_requests():
        return await asyncio.gather(
            city_api.get_city_detail_batch_payload(
                object(),
                cities="Paris",
                resolution="10m",
                limit=12,
            ),
            city_api.get_city_detail_batch_payload(
                object(),
                cities="Paris",
                resolution="10m",
                limit=12,
            ),
        )

    first, second = asyncio.run(run_requests())

    assert first == second
    assert entitlement_calls == 2
    assert build_calls == 1


def test_concurrent_city_detail_requests_share_same_full_cache_refresh(monkeypatch):
    import asyncio

    refresh_calls = 0
    build_calls = 0

    class FakeCache:
        payload = None

        def get_city_cache(self, kind, city):
            assert kind == "full"
            if self.payload is None:
                return None
            return {"payload": self.payload}

    fake_cache = FakeCache()

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api.legacy_routes._refresh_city_full_cache:
            await asyncio.sleep(0.02)
        return fn(*args, **kwargs)

    def refresh_full(city, force_refresh):
        nonlocal refresh_calls
        refresh_calls += 1
        fake_cache.payload = {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
        }
        return fake_cache.payload

    def build_detail(data, market_slug, target_date, resolution):
        nonlocal build_calls
        build_calls += 1
        return {
            "city": data["city"],
            "hourly": data["hourly"],
            "resolution": resolution,
        }

    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", fake_cache)
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_full_cache", refresh_full)
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    async def run_two_requests():
        return await asyncio.gather(
            city_api.get_city_detail_aggregate_payload(object(), "Paris", resolution="10m"),
            city_api.get_city_detail_aggregate_payload(object(), "Paris", resolution="10m"),
        )

    results = asyncio.run(run_two_requests())

    assert [item["city"] for item in results] == ["paris", "paris"]
    assert refresh_calls == 1
    assert build_calls == 1


def test_stale_city_detail_uses_cached_full_payload_while_refreshing(monkeypatch):
    import asyncio

    refresh_calls = 0
    build_inputs = []

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            assert city == "paris"
            return {
                "payload": {
                    "city": "paris",
                    "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
                },
            }

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api.legacy_routes._refresh_city_full_cache:
            await asyncio.sleep(0.01)
        return fn(*args, **kwargs)

    def refresh_full(city, force_refresh):
        nonlocal refresh_calls
        refresh_calls += 1
        return {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [21.0]},
        }

    def build_detail(data, market_slug, target_date, resolution):
        build_inputs.append(data["hourly"]["temps"][0])
        return {
            "city": data["city"],
            "live_temp": data["hourly"]["temps"][0],
            "resolution": resolution,
        }

    city_api._CITY_FULL_REFRESH_INFLIGHT.clear()
    city_api._CITY_DETAIL_PAYLOAD_CACHE.clear()
    city_api._CITY_DETAIL_PAYLOAD_CACHE_TS.clear()
    city_api._CITY_DETAIL_PAYLOAD_INFLIGHT.clear()

    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_city_cache_is_fresh", lambda entry, ttl: False)
    monkeypatch.setattr(city_api.legacy_routes, "_overlay_latest_wunderground_current", lambda city, payload: payload)
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_full_cache", refresh_full)
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    async def run_request():
        payload = await city_api.get_city_detail_aggregate_payload(object(), "Paris", resolution="10m")
        await asyncio.sleep(0.03)
        return payload

    result = asyncio.run(run_request())

    assert result["live_temp"] == 20.0
    assert build_inputs == [20.0]
    assert refresh_calls == 1


def test_force_refresh_panel_returns_cached_payload_when_refresh_is_slow(monkeypatch):
    import asyncio

    refresh_calls = 0

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "panel"
            assert city == "paris"
            return {
                "payload": {
                    "name": "paris",
                    "deb": {"prediction": 20.0},
                    "from_cache": True,
                },
            }

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api.legacy_routes._refresh_city_panel_cache:
            await asyncio.sleep(0.05)
        return fn(*args, **kwargs)

    def refresh_panel(city, force_refresh):
        nonlocal refresh_calls
        refresh_calls += 1
        return {"name": city, "deb": {"prediction": 21.0}, "from_cache": False}

    monkeypatch.setenv("POLYWEATHER_CITY_FORCE_REFRESH_TIMEOUT_SEC", "0.01")
    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_overlay_latest_wunderground_current", lambda city, payload: payload)
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_panel_cache", refresh_panel)

    async def run_request():
        payload = await city_api.get_city_detail_payload(
            object(),
            "Paris",
            force_refresh=True,
            depth="panel",
        )
        await asyncio.sleep(0.06)
        return payload

    result = asyncio.run(run_request())

    assert result["from_cache"] is True
    assert result["deb"]["prediction"] == 20.0
    assert refresh_calls == 1


def test_force_refresh_panel_returns_cached_payload_when_refresh_already_running(monkeypatch):
    import asyncio

    refresh_calls = 0

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "panel"
            assert city == "paris"
            return {
                "payload": {
                    "name": "paris",
                    "deb": {"prediction": 20.0},
                    "from_cache": True,
                },
            }

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api.legacy_routes._refresh_city_panel_cache:
            await asyncio.sleep(0.08)
        return fn(*args, **kwargs)

    def refresh_panel(city, force_refresh):
        nonlocal refresh_calls
        refresh_calls += 1
        return {"name": city, "deb": {"prediction": 21.0}, "from_cache": False}

    city_api._CITY_FORCE_REFRESH_INFLIGHT.clear()

    monkeypatch.setenv("POLYWEATHER_CITY_FORCE_REFRESH_TIMEOUT_SEC", "0.5")
    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_overlay_latest_wunderground_current", lambda city, payload: payload)
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_panel_cache", refresh_panel)

    async def run_requests():
        first_task = asyncio.create_task(
            city_api.get_city_detail_payload(
                object(),
                "Paris",
                force_refresh=True,
                depth="panel",
            )
        )
        await asyncio.sleep(0.01)
        second = await city_api.get_city_detail_payload(
            object(),
            "Paris",
            force_refresh=True,
            depth="panel",
        )
        first = await first_task
        return first, second

    first_result, second_result = asyncio.run(run_requests())

    assert first_result["from_cache"] is False
    assert second_result["from_cache"] is True
    assert second_result["deb"]["prediction"] == 20.0
    assert refresh_calls == 1


def test_stale_panel_returns_cached_payload_while_refreshing(monkeypatch):
    import asyncio

    refresh_calls = 0

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "panel"
            assert city == "paris"
            return {
                "payload": {
                    "name": "paris",
                    "deb": {"prediction": 20.0},
                    "from_cache": True,
                },
            }

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api.legacy_routes._refresh_city_panel_cache:
            await asyncio.sleep(0.05)
        return fn(*args, **kwargs)

    def refresh_panel(city, force_refresh):
        nonlocal refresh_calls
        refresh_calls += 1
        return {"name": city, "deb": {"prediction": 21.0}, "from_cache": False}

    city_api._CITY_STALE_REFRESH_TASKS.clear()

    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_city_cache_is_fresh", lambda entry, ttl: False)
    monkeypatch.setattr(city_api.legacy_routes, "_overlay_latest_wunderground_current", lambda city, payload: payload)
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_panel_cache", refresh_panel)

    async def run_request():
        payload = await city_api.get_city_detail_payload(
            object(),
            "Paris",
            force_refresh=False,
            depth="panel",
        )
        await asyncio.sleep(0.06)
        return payload

    result = asyncio.run(run_request())

    assert result["from_cache"] is True
    assert result["deb"]["prediction"] == 20.0
    assert refresh_calls == 1


def test_force_refresh_full_detail_returns_cached_payload_when_refresh_is_slow(monkeypatch):
    import asyncio

    refresh_calls = 0
    build_inputs = []

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            assert city == "paris"
            return {
                "payload": {
                    "city": "paris",
                    "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
                },
            }

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        if fn is city_api.legacy_routes._refresh_city_full_cache:
            await asyncio.sleep(0.05)
        return fn(*args, **kwargs)

    def refresh_full(city, force_refresh):
        nonlocal refresh_calls
        refresh_calls += 1
        return {
            "city": city,
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [21.0]},
        }

    def build_detail(data, market_slug, target_date, resolution):
        build_inputs.append(data["hourly"]["temps"][0])
        return {"city": data["city"], "live_temp": data["hourly"]["temps"][0]}

    city_api._CITY_FULL_REFRESH_INFLIGHT.clear()
    city_api._CITY_DETAIL_PAYLOAD_CACHE.clear()
    city_api._CITY_DETAIL_PAYLOAD_CACHE_TS.clear()
    city_api._CITY_DETAIL_PAYLOAD_INFLIGHT.clear()

    monkeypatch.setenv("POLYWEATHER_CITY_FORCE_REFRESH_TIMEOUT_SEC", "0.01")
    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_overlay_latest_wunderground_current", lambda city, payload: payload)
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_full_cache", refresh_full)
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    async def run_request():
        payload = await city_api.get_city_detail_aggregate_payload(
            object(),
            "Paris",
            force_refresh=True,
            resolution="10m",
        )
        await asyncio.sleep(0.06)
        return payload

    result = asyncio.run(run_request())

    assert result["live_temp"] == 20.0
    assert build_inputs == [20.0]
    assert refresh_calls == 1


def test_force_refresh_invalidates_short_city_detail_payload_cache(monkeypatch):
    import asyncio

    build_calls = 0
    refreshed_payloads = [
        {
            "city": "paris",
            "local_date": "2026-05-30",
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [20.0]},
        },
        {
            "city": "paris",
            "local_date": "2026-05-30",
            "hourly": {"times": ["2026-05-30T00:00:00Z"], "temps": [21.0]},
        },
    ]

    class FakeCache:
        def get_city_cache(self, kind, city):
            assert kind == "full"
            return None

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def refresh_full(city, force_refresh):
        assert city == "paris"
        assert refreshed_payloads
        return refreshed_payloads.pop(0)

    def build_detail(data, market_slug, target_date, resolution):
        nonlocal build_calls
        build_calls += 1
        return {
            "city": data["city"],
            "live_temp": data["hourly"]["temps"][0],
            "resolution": resolution,
        }

    city_api._CITY_DETAIL_PAYLOAD_CACHE.clear()
    city_api._CITY_DETAIL_PAYLOAD_CACHE_TS.clear()
    city_api._CITY_DETAIL_PAYLOAD_INFLIGHT.clear()

    monkeypatch.setattr(city_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(city_api.legacy_routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(city_api.legacy_routes, "_normalize_city_or_404", lambda name: name.strip().lower())
    monkeypatch.setattr(city_api.legacy_routes, "_CACHE_DB", FakeCache())
    monkeypatch.setattr(city_api.legacy_routes, "_refresh_city_full_cache", refresh_full)
    monkeypatch.setattr(city_api.legacy_routes, "_build_city_detail_payload", build_detail)

    first = asyncio.run(city_api.get_city_detail_aggregate_payload(object(), "Paris", resolution="10m"))
    second = asyncio.run(
        city_api.get_city_detail_aggregate_payload(
            object(),
            "Paris",
            resolution="10m",
            force_refresh=True,
        ),
    )

    assert first["live_temp"] == 20.0
    assert second["live_temp"] == 21.0
    assert build_calls == 2




















def test_backend_entitlement_token_binds_forwarded_supabase_identity(monkeypatch):
    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "enabled", True)
    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "anon_key", "anon-key")
    monkeypatch.setattr(web_core, "_SUPABASE_AUTH_REQUIRED", True)
    monkeypatch.setattr(web_core, "_ENTITLEMENT_TOKEN", "backend-token")

    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-polyweather-entitlement", b"backend-token"),
                (b"x-polyweather-auth-user-id", b"user-1"),
                (b"x-polyweather-auth-email", b"user@example.com"),
            ],
        }
    )

    web_core._assert_entitlement(request)

    assert request.state.auth_user_id == "user-1"
    assert request.state.auth_email == "user@example.com"


def test_backend_entitlement_token_without_forwarded_identity_validates_bearer(monkeypatch):
    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "enabled", True)
    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "anon_key", "anon-key")
    monkeypatch.setattr(web_core, "_SUPABASE_AUTH_REQUIRED", False)
    monkeypatch.setattr(web_core, "_ENTITLEMENT_TOKEN", "backend-token")

    class _Identity:
        user_id = "user-1"
        email = "user@example.com"
        points = 7
        created_at = "2026-05-01T00:00:00+00:00"

    calls = {"count": 0}

    def _get_identity(token):
        calls["count"] += 1
        assert token == "access-token"
        return _Identity()

    monkeypatch.setattr(web_core.SUPABASE_ENTITLEMENT, "get_identity", _get_identity)

    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-polyweather-entitlement", b"backend-token"),
                (b"authorization", b"Bearer access-token"),
            ],
        }
    )

    web_core._assert_entitlement(request)

    assert calls["count"] == 1
    assert request.state.auth_user_id == "user-1"
    assert request.state.auth_email == "user@example.com"












def test_ops_truth_history_returns_filtered_rows(monkeypatch):
    monkeypatch.setattr(routes, "_assert_entitlement", lambda request: None)
    monkeypatch.setattr(routes, "_require_ops_admin", lambda request: None)

    repo = TruthRecordRepository()
    repo.upsert_truth(
        city="taipei",
        target_date="2026-04-02",
        actual_high=26.0,
        settlement_source="wunderground",
        settlement_station_code="RCSS",
        settlement_station_label="Taipei Songshan Airport Station",
        truth_version="v1",
        updated_by="test",
        source_payload={"sample": True},
        is_final=True,
    )

    response = client.get("/api/ops/truth-history?city=taipei&date_from=2026-04-01&date_to=2026-04-03&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert payload["filters"]["city"] == "taipei"
    assert payload["items"][0]["city"] == "taipei"


def test_scan_terminal_service_returns_stale_payload_after_failed_refresh(monkeypatch):
    filters = {"scan_mode": "tradable", "limit": 5}
    normalized_filters = scan_terminal_service._normalize_scan_terminal_filters(filters)
    scan_terminal_cache._SCAN_TERMINAL_CACHE.clear()

    monkeypatch.setattr(
        scan_terminal_service,
        "_scan_city_terminal_rows",
        lambda *_args, **_kwargs: {
            "city": "taipei",
            "rows": [
                {
                    "id": "row-1",
                    "market_key": "market-1",
                    "edge_percent": 12.4,
                    "final_score": 83.0,
                    "volume": 2000,
                }
            ],
            "candidate_total": 1,
            "primary_scores": [83.0],
        },
    )

    ready = scan_terminal_service.build_scan_terminal_payload(filters, force_refresh=True)
    assert ready["status"] == "ready"
    assert ready["rows"][0]["id"] == "row-1"

    def _explode(*_args, **_kwargs):
        raise RuntimeError("upstream 504")

    monkeypatch.setattr(scan_terminal_service, "_scan_city_terminal_rows", _explode)

    stale = scan_terminal_service.build_scan_terminal_payload(filters, force_refresh=True)

    assert stale["status"] == "stale"
    assert stale["stale"] is True
    assert stale["rows"][0]["id"] == "row-1"
    assert stale["filters"] == normalized_filters
    assert stale["stale_reason"] == "upstream 504"


def test_scan_terminal_timeout_does_not_replace_better_cached_snapshot(monkeypatch):
    import time

    filters = {"scan_mode": "tradable", "limit": 5}
    normalized_filters = scan_terminal_service._normalize_scan_terminal_filters(filters)
    scan_terminal_cache._SCAN_TERMINAL_CACHE.clear()
    previous_payload = {
        "generated_at": "2026-05-31T00:00:00Z",
        "snapshot_id": "scan-existing",
        "filters": normalized_filters,
        "summary": {"candidate_total": 2, "visible_count": 2},
        "top_signal": {"id": "old-1"},
        "rows": [{"id": "old-1"}, {"id": "old-2"}],
        "status": "ready",
        "stale": False,
        "stale_reason": None,
        "last_success_at": None,
        "last_failed_at": None,
    }
    scan_terminal_cache.set_cached_scan_terminal_payload(
        normalized_filters,
        previous_payload,
    )

    monkeypatch.setattr(
        scan_terminal_service,
        "CITIES",
        {"fast": {"tz": 0}, "slow": {"tz": 0}},
    )
    monkeypatch.setattr(scan_terminal_service, "SCAN_TERMINAL_BUILD_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(scan_terminal_service, "SCAN_TERMINAL_MAX_WORKERS", 2)

    def _scan_city(city_name, *_args, **_kwargs):
        if city_name == "slow":
            time.sleep(0.05)
        return {
            "city": city_name,
            "candidate_total": 1,
            "primary_scores": [80.0],
            "rows": [
                {
                    "id": f"{city_name}-row",
                    "market_key": f"{city_name}-market",
                    "edge_percent": 4.0,
                    "final_score": 80.0,
                    "volume": 1000,
                }
            ],
        }

    monkeypatch.setattr(scan_terminal_service, "_scan_city_terminal_rows", _scan_city)

    stale = scan_terminal_service.build_scan_terminal_payload(filters, force_refresh=True)

    assert stale["status"] == "stale"
    assert stale["stale"] is True
    assert [row["id"] for row in stale["rows"]] == ["old-1", "old-2"]
    assert stale["stale_reason"].startswith("scan terminal build timed out")
    cached = scan_terminal_cache.get_cached_scan_terminal_payload(
        normalized_filters,
        ttl_sec=3600,
    )
    assert [row["id"] for row in cached["rows"]] == ["old-1", "old-2"]


def test_scan_terminal_cold_requests_start_background_build_without_blocking(monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    filters = {"scan_mode": "tradable", "limit": 17, "min_edge_pct": 6.75}
    scan_terminal_cache._SCAN_TERMINAL_CACHE.clear()
    scan_terminal_cache._SCAN_TERMINAL_REFRESHING.clear()
    monkeypatch.setenv("POLYWEATHER_SCAN_TERMINAL_REDIS_CACHE_ENABLED", "false")

    calls = 0

    def _fake_uncached(filters_arg, *, force_refresh=False, timeout_sec=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return {
            "generated_at": "2026-06-05T00:00:00Z",
            "filters": dict(filters_arg),
            "summary": {"candidate_total": 1},
            "top_signal": None,
            "rows": [{"id": "shared-row"}],
            "status": "ready",
            "stale": False,
            "stale_reason": None,
            "last_success_at": None,
            "last_failed_at": None,
        }

    monkeypatch.setattr(
        scan_terminal_service,
        "_build_scan_terminal_payload_uncached",
        _fake_uncached,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(scan_terminal_service.build_scan_terminal_payload, filters),
            executor.submit(scan_terminal_service.build_scan_terminal_payload, filters),
        ]
        results = [future.result(timeout=0.02) for future in futures]

    assert calls == 1
    assert [result["status"] for result in results] == ["failed", "failed"]
    assert all(result["rows"] == [] for result in results)
    assert all("初始化" in result["stale_reason"] or "刷新中" in result["stale_reason"] for result in results)


def test_scan_terminal_background_refresh_reuses_cached_city_data(monkeypatch):
    filters = {"scan_mode": "tradable", "limit": 17, "min_edge_pct": 6.75}
    calls = []

    monkeypatch.setattr(
        scan_terminal_service,
        "mark_scan_terminal_refreshing",
        lambda _filters: True,
    )
    monkeypatch.setattr(
        scan_terminal_service,
        "clear_scan_terminal_refreshing",
        lambda _filters: None,
    )
    monkeypatch.setattr(
        scan_terminal_service,
        "_build_scan_terminal_payload_singleflight",
        lambda filters_arg, *, force_refresh=False: calls.append(
            (dict(filters_arg), force_refresh)
        ),
    )

    class _ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(scan_terminal_service.threading, "Thread", _ImmediateThread)

    assert scan_terminal_service._start_scan_terminal_background_refresh(filters) is True
    assert calls == [(filters, False)]


def test_scan_terminal_nonforce_ignores_ancient_success_snapshot(monkeypatch):
    filters = {"scan_mode": "tradable", "limit": 17, "min_edge_pct": 6.75}
    old_success_t = 1780839484.0

    monkeypatch.setattr(
        scan_terminal_service,
        "get_cached_scan_terminal_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scan_terminal_service,
        "get_scan_terminal_cache_entry",
        lambda *_args, **_kwargs: {
            "t": old_success_t,
            "success_t": old_success_t,
            "success_payload": {
                "generated_at": "2026-06-07T13:38:04.694350Z",
                "rows": [{"id": "chengdu:2026-06-07", "city": "chengdu", "current_temp": 21.0}],
                "summary": {"candidate_total": 1},
            },
        },
    )
    monkeypatch.setattr(
        scan_terminal_service.time,
        "time",
        lambda: old_success_t + scan_terminal_service.SCAN_TERMINAL_PAYLOAD_TTL_SEC * 3,
    )
    monkeypatch.setattr(
        scan_terminal_service,
        "_start_scan_terminal_background_refresh",
        lambda *_args, **_kwargs: True,
    )

    payload = scan_terminal_service.build_scan_terminal_payload(filters)

    assert payload["status"] == "failed"
    assert payload["rows"] == []
    assert payload["summary"]["candidate_total"] == 0


def test_scan_terminal_prewarm_builds_default_terminal_payload(monkeypatch):
    calls = []

    def _fake_build(filters, *, force_refresh=False, timeout_sec=None):
        calls.append((dict(filters), force_refresh, timeout_sec))
        return {"rows": []}

    monkeypatch.setattr(
        scan_terminal_service,
        "_build_scan_terminal_payload_uncached",
        _fake_build,
    )

    assert scan_terminal_service._warm_scan_terminal_payloads() == 2
    assert {filters["limit"] for filters, _, _ in calls} == {25, 180}
    filters, force_refresh, timeout_sec = calls[0]
    assert all(force_refresh is False for _, force_refresh, _ in calls)
    assert all(
        timeout_sec == scan_terminal_service.SCAN_TERMINAL_PREWARM_PAYLOAD_TIMEOUT_SEC
        for _, _, timeout_sec in calls
    )
    assert filters["scan_mode"] == "tradable"
    assert filters["min_price"] == 0.05
    assert filters["max_price"] == 0.95
    assert filters["min_edge_pct"] == 2.0
    assert filters["min_liquidity"] == 500.0
    assert filters["market_type"] == "maxtemp"
    assert filters["time_range"] == "today"
    assert filters["limit"] == 25


def test_scan_terminal_service_returns_failed_without_success_snapshot(monkeypatch):
    filters = {"scan_mode": "tradable", "limit": 5}
    scan_terminal_cache._SCAN_TERMINAL_CACHE.clear()

    def _explode(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(scan_terminal_service, "_scan_city_terminal_rows", _explode)

    failed = scan_terminal_service.build_scan_terminal_payload(filters, force_refresh=True)

    assert failed["status"] == "failed"
    assert failed["stale"] is False
    assert failed["rows"] == []
    assert failed["summary"]["candidate_total"] == 0
    assert failed["stale_reason"] == "network down"


def test_scan_terminal_endpoint_forwards_filters(monkeypatch):
    monkeypatch.setattr(routes, "_assert_entitlement", lambda request: None)

    captured = {}

    def _fake_build_scan_terminal_payload(filters, *, force_refresh=False):
        captured["filters"] = dict(filters)
        captured["force_refresh"] = force_refresh
        return {
            "generated_at": "2026-04-23T00:00:00Z",
            "filters": filters,
            "summary": {
                "recommended_count": 1,
                "visible_count": 1,
                "candidate_total": 3,
                "avg_edge_percent": 4.2,
                "avg_primary_confidence": 88.0,
                "tradable_market_count": 1,
                "total_volume": 1500,
                "resolved_market_type": "maxtemp",
            },
            "top_signal": None,
            "rows": [],
        }

    monkeypatch.setattr(routes, "build_scan_terminal_payload", _fake_build_scan_terminal_payload)

    response = client.get(
        "/api/scan/terminal?scan_mode=trend&min_price=0.1&max_price=0.8&min_edge_pct=3"
        "&min_liquidity=700&high_liquidity_only=true&market_type=all&time_range=week&limit=12&force_refresh=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["recommended_count"] == 1
    assert captured["force_refresh"] is True
    assert captured["filters"]["scan_mode"] == "trend"
    assert captured["filters"]["market_type"] == "all"
    assert captured["filters"]["time_range"] == "week"
    assert captured["filters"]["limit"] == 12


def test_scan_terminal_cache_key_includes_filter_dimensions():
    first = scan_terminal_cache_key(
        {
            "scan_mode": "tradable",
            "time_range": "today",
            "limit": 25,
        }
    )
    second = scan_terminal_cache_key(
        {
            "scan_mode": "trend",
            "time_range": "week",
            "limit": 10,
        }
    )

    assert first != second
    assert "trend" in second
    assert "week" in second
