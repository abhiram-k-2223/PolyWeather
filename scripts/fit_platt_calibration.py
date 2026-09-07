#!/usr/bin/env python3
"""Fit Platt scaling params from backtest records and emit calibrated probs.

Reads a records file produced by ``scripts/build_openmeteo_backtest_records.py``
or ``scripts/backtest_real_polymarket.py`` (entries with ``model_probability``
plus ``actual``/``actual_outcome``), fits a global logistic map plus per-city
maps (falling back to global when a city has < 50 samples), and writes::

    {"params": {"global": {...}, "cities": {city: {...}}},
     "calibrated": {"city|target_date": prob}}

The ``calibrated`` map is consumed directly by
``scripts/backtest_real_polymarket.py --calibrated-probs`` (and the matching
option on the synthetic builder) to measure edge on calibrated — not raw —
probabilities.

Example:
    .venv/bin/python scripts/fit_platt_calibration.py \\
        --records data/openmeteo_backtest_records.json \\
        --output data/platt_calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.platt_calibration import (  # noqa: E402
    PlattParams,
    apply_platt,
    fit_platt,
)

DEFAULT_RECORDS = ROOT / "data" / "openmeteo_backtest_records.json"
DEFAULT_OUTPUT = ROOT / "data" / "platt_calibration.json"
MIN_CITY_SAMPLES = 50


def _extract_pairs(records: list[dict]) -> tuple[list[float], list[float], list[dict]]:
    probs: list[float] = []
    outcomes: list[float] = []
    valid: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        raw_p = rec.get("model_probability")
        if raw_p is None:
            continue
        try:
            p = float(raw_p)
        except (TypeError, ValueError):
            continue
        actual = rec.get("actual", rec.get("actual_outcome"))
        try:
            y = float(actual) if actual is not None else None
        except (TypeError, ValueError):
            y = None
        if not (0 < p < 1) or y not in (0.0, 1.0):
            continue
        probs.append(p)
        outcomes.append(y)
        valid.append(rec)
    return probs, outcomes, valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", default=str(DEFAULT_RECORDS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    payload = json.loads(Path(args.records).read_text(encoding="utf-8"))
    records = payload.get("records", payload) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise SystemExit("Records file must contain a list under 'records'.")

    probs, outcomes, valid = _extract_pairs(records)
    if len(valid) < 10:
        raise SystemExit(
            f"Only {len(valid)} valid (prob, outcome) pairs — need >= 10 to fit."
        )

    global_params = fit_platt(probs, outcomes)

    by_city: dict[str, tuple[list[float], list[float]]] = {}
    for rec, p, y in zip(valid, probs, outcomes):
        by_city.setdefault(str(rec.get("city", "")).lower(), ([], []))[0].append(p)
        by_city[str(rec.get("city", "")).lower()][1].append(y)
    city_params: dict[str, PlattParams] = {}
    for city, (cp, cy) in by_city.items():
        if len(cp) >= MIN_CITY_SAMPLES:
            city_params[city] = fit_platt(cp, cy)
        else:
            city_params[city] = global_params

    calibrated: dict[str, float] = {}
    for rec, p in zip(valid, probs):
        city = str(rec.get("city", "")).lower()
        params = city_params.get(city, global_params)
        key = f"{city}|{rec.get('target_date', '')}"
        calibrated[key] = round(apply_platt(p, params), 4)

    out = {
        "params": {
            "global": global_params.to_dict(),
            "cities": {c: pr.to_dict() for c, pr in city_params.items()},
        },
        "calibrated": calibrated,
    }
    Path(args.output).write_text(json.dumps(out, indent=2), encoding="utf-8")
    g = global_params
    print(
        f"Fit Platt on {g.n} samples: a={g.a:.3f} b={g.b:+.3f} "
        f"Brier {g.brier_before} -> {g.brier_after}; "
        f"wrote {len(calibrated)} calibrated probs to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
