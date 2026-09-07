"""Tests for Platt scaling calibration (problems.md #3)."""

import math
import random

from src.analysis.platt_calibration import (
    apply_platt,
    brier_score,
    fit_platt,
)


def _overconfident_data(n: int = 400, seed: int = 7):
    rng = random.Random(seed)
    probs, outcomes = [], []
    for _ in range(n):
        true_p = rng.random() * 0.6 + 0.2  # 0.2..0.8
        # Overconfident raw model: push toward extremes.
        raw = true_p + (0.25 if true_p > 0.5 else -0.25)
        raw = min(max(raw, 0.01), 0.99)
        probs.append(raw)
        outcomes.append(1.0 if rng.random() < true_p else 0.0)
    return probs, outcomes


def test_fit_platt_improves_brier_on_miscalibrated_probs():
    probs, outcomes = _overconfident_data()
    before = brier_score(probs, outcomes)
    params = fit_platt(probs, outcomes)
    after = brier_score([apply_platt(p, params) for p in probs], outcomes)
    assert params.n == len(probs)
    assert after is not None and before is not None
    assert after <= before


def test_fit_platt_identity_on_too_few_samples():
    params = fit_platt([0.7, 0.3], [1.0, 0.0])
    assert (params.a, params.b) == (1.0, 0.0)
    assert apply_platt(0.7, params) == 0.7


def test_apply_platt_clamps_and_monotonic():
    probs, outcomes = _overconfident_data()
    params = fit_platt(probs, outcomes)
    cal = [apply_platt(p, params) for p in (0.01, 0.5, 0.99)]
    assert all(math.isfinite(c) and 0 < c < 1 for c in cal)
    assert cal[0] < cal[1] < cal[2]


def test_synthetic_builder_applies_calibrated_override():
    from scripts.build_openmeteo_backtest_records import build_records

    history = {
        "testcity": {
            f"2026-01-{d:02d}": {
                "actual_high": 20.0,
                "forecasts": {"GFS": 20.0, "ECMWF": 21.0},
            }
            for d in range(1, 6)
        }
    }
    plain = build_records(history)
    key = "testcity|2026-01-05"
    over = build_records(history, calibrated={key: 0.9})
    by_date = {r["target_date"]: r for r in over}
    assert by_date["2026-01-05"]["model_probability"] == 0.9
    plain_by_date = {r["target_date"]: r for r in plain}
    assert plain_by_date["2026-01-05"]["model_probability"] != 0.9
