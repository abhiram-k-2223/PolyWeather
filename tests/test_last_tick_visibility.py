"""Last-tick visibility in /api/paper/summary (TDD RED)."""

import asyncio

import web.services.trading_api as tapi


def test_last_tick_starts_empty():
    # Module-global tick state: reset for isolation since other suite
    # files also run maintenance ticks in-process.
    tapi._LAST_TICK = None
    assert tapi.get_last_tick() is None


def test_maintenance_tick_records_last_tick():
    from src.trading.engine.trading_engine import EngineConfig, TradingEngine

    tapi._LAST_TICK = None
    eng = TradingEngine(
        wallet=None, config=EngineConfig(enabled=True, paper_mode=True)
    )
    out = asyncio.run(
        tapi.run_paper_maintenance_once(
            engine=eng,
            client=object(),
            probability_provider=None,
        )
    )
    last = tapi.get_last_tick()
    assert last is not None
    assert last["feed"] == out["feed"]
    assert "at" in last
    tapi._LAST_TICK = None


def test_summary_includes_last_tick():
    from fastapi.testclient import TestClient

    from web.app_factory import create_app

    tapi._LAST_TICK = {
        "at": "2026-09-30T00:00:00+00:00",
        "feed": {"signals": 0, "orders": 0, "skipped": ["x"]},
    }
    try:
        client = TestClient(create_app())
        body = client.get("/api/paper/summary").json()
        assert body["last_tick"]["feed"]["skipped"] == ["x"]
    finally:
        tapi._LAST_TICK = None
