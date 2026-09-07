"""Regression tests for the multi-model history pipeline bug.

433/433 city-days shipped with GFS=ECMWF=ICON identical because
``scripts/fetch_openmeteo_history.py`` sent singular ``model=`` (silently
ignored by Open-Meteo, returning Best Match for every request) and used
the deprecated ``ecmwf_ifs04`` identifier (returns no data with the
correct plural param). These tests pin the fixed behaviour.
"""

from __future__ import annotations

import scripts.fetch_openmeteo_history as fetcher
from scripts.build_openmeteo_backtest_records import build_records
from scripts.fetch_openmeteo_history import history_diversity_stats


def test_models_uses_supported_identifiers():
    assert fetcher.MODELS["ecmwf_ifs025"] == "ECMWF"
    assert "ecmwf_ifs04" not in fetcher.MODELS
    assert fetcher.MODELS["gfs_seamless"] == "GFS"
    assert fetcher.MODELS["icon_seamless"] == "ICON"


def test_fetch_uses_plural_models_param(monkeypatch):
    captured: list[dict] = []

    def fake_get_json(client, url, params):
        captured.append(dict(params))
        return {
            "hourly": {
                "time": ["2026-09-01T00:00"],
                "temperature_2m_previous_day1": [20.0],
            }
        }

    monkeypatch.setattr(fetcher, "_get_json", fake_get_json)
    fetcher.fetch_model_day1_forecasts(None, 40.0, -74.0, "America/New_York", 1)  # type: ignore[arg-type]

    assert len(captured) == len(fetcher.MODELS)
    for params in captured:
        assert "models" in params, f"must use plural 'models' param, got {params}"
        assert "model" not in params


def test_diversity_stats_detects_collapsed_pipeline():
    collapsed = {
        "new york": {
            f"2026-05-{d:02d}": {
                "actual_high": 20.0,
                "forecasts": {"GFS": 19.2, "ECMWF": 19.2, "ICON": 19.2},
            }
            for d in range(1, 11)
        }
    }
    stats = history_diversity_stats(collapsed)
    assert stats["total"] == 10
    assert stats["identical"] == 10
    assert stats["identical_share"] == 1.0

    healthy = {
        "new york": {
            "2026-05-01": {
                "actual_high": 20.0,
                "forecasts": {"GFS": 19.2, "ECMWF": 20.1, "ICON": 18.7},
            },
            "2026-05-02": {
                "actual_high": 21.0,
                "forecasts": {"GFS": 20.5, "ECMWF": 21.2, "ICON": 20.9},
            },
        }
    }
    stats = history_diversity_stats(healthy)
    assert stats["identical"] == 0
    assert stats["identical_share"] == 0.0


def test_build_records_marks_synthetic_market():
    history = {
        "new york": {
            f"2026-05-{d:02d}": {
                "actual_high": 20.0 + (d % 3),
                "forecasts": {
                    "GFS": 19.0 + (d % 3) * 0.5,
                    "ECMWF": 19.4 + (d % 3) * 0.5,
                    "ICON": 18.8 + (d % 3) * 0.5,
                },
            }
            for d in range(1, 8)
        }
    }
    records = build_records(history)
    assert records, "expected records from healthy diverse history"
    assert all(r["metadata"].get("synthetic_market") is True for r in records)
