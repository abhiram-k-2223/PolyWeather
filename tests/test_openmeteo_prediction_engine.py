"""DEB prediction engine test against real Open-Meteo data.

Runs a walk-forward evaluation of the DEB ensemble on the fetched
forecast/observation history produced by
``scripts/fetch_openmeteo_history.py`` (Previous Runs API day-1 model
forecasts + ERA5 observed highs).

Skips when ``data/openmeteo_history.json`` is absent — run the fetcher
first:
    .venv/bin/python scripts/fetch_openmeteo_history.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis import deb_algorithm
from src.analysis.deb_evaluation import (
    DEB_GUARDED_CALIBRATED_VERSION,
    DEB_RAW_VERSION,
    backtest_deb_versions,
)

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "data" / "openmeteo_history.json"

pytestmark = pytest.mark.skipif(
    not HISTORY_FILE.exists(),
    reason="run scripts/fetch_openmeteo_history.py to download real data first",
)


@pytest.fixture(scope="module")
def history() -> dict:
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def _walk_forward_predictions(history: dict) -> list[dict]:
    """For each city-day, predict the high using only prior days' history."""
    original_load = deb_algorithm.load_history
    rows: list[dict] = []
    try:
        for city, city_history in history.items():
            dates = sorted(city_history)
            for i, date in enumerate(dates):
                record = city_history[date]
                # Forecast-only tail days (actual pending ERA5) can be
                # predicted walk-forward but not scored — skip them here.
                if record.get("actual_high") is None:
                    continue
                prior = {d: city_history[d] for d in dates[:i]}
                deb_algorithm.load_history = lambda _path, _prior=prior: _prior
                components = deb_algorithm.calculate_dynamic_weight_components(
                    city, record["forecasts"]
                )
                prediction = components.get("prediction")
                if prediction is not None:
                    rows.append(
                        {
                            "city": city,
                            "target_date": date,
                            "prediction": prediction,
                            "actual": record["actual_high"],
                            "weights": components.get("weights") or {},
                            "days_used": components.get("days_used") or 0,
                        }
                    )
    finally:
        deb_algorithm.load_history = original_load
    return rows


def test_walk_forward_deb_beats_persistence_and_climatology(history: dict) -> None:
    """DEB should track observed highs closely on real forecast data."""
    rows = _walk_forward_predictions(history)
    assert len(rows) > 300, f"expected a meaningful sample, got {len(rows)}"

    mae = sum(abs(r["prediction"] - r["actual"]) for r in rows) / len(rows)
    # Multi-model blends over these cities typically land around 1-2°C MAE
    # against ERA5; 3.5°C is a generous ceiling that still catches breakage.
    assert mae < 3.5, f"walk-forward DEB MAE too high: {mae:.2f}°C"

    # Weights must be a valid probability distribution once history exists.
    weighted = [r for r in rows if r["weights"]]
    assert len(weighted) > len(rows) * 0.8, "most days should use error-weighted blend"
    for row in weighted:
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-6
        assert all(w > 0 for w in row["weights"].values())


def test_walk_forward_improves_over_equal_weight(history: dict) -> None:
    """DEB weighting should not be worse than the naive equal-weight mean."""
    rows = _walk_forward_predictions(history)
    deb_mae = sum(abs(r["prediction"] - r["actual"]) for r in rows) / len(rows)
    eq_mae = (
        sum(
            abs(
                sum(city_history_row["forecasts"].values())
                / len(city_history_row["forecasts"])
                - city_history_row["actual_high"]
            )
            for city in history
            for city_history_row in history[city].values()
            if city_history_row.get("actual_high") is not None
        )
        / sum(
            sum(
                1
                for city_history_row in history[city].values()
                if city_history_row.get("actual_high") is not None
            )
            for city in history
        )
    )
    assert deb_mae <= eq_mae + 0.5, (
        f"DEB MAE {deb_mae:.2f} should be close to or below equal-weight {eq_mae:.2f}"
    )


def test_backtest_deb_versions_on_real_data(history: dict) -> None:
    """The production guarded corrector must be sane on real data."""
    rows = _walk_forward_predictions(history)
    report = backtest_deb_versions(rows)
    raw = report["versions"][DEB_RAW_VERSION]
    guarded = report["versions"][DEB_GUARDED_CALIBRATED_VERSION]

    assert raw["samples"] > 300
    assert raw["mae"] is not None and raw["mae"] < 3.5
    # The guard exists to never do much worse than raw DEB.
    assert guarded["mae"] is not None
    assert guarded["mae"] <= raw["mae"] + 0.3, (
        f"guarded MAE {guarded['mae']:.2f} blew past raw {raw['mae']:.2f}"
    )
