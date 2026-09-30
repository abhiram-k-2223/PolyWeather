"""Build a calibration table from the prediction/outcome log.

The feed tick appends ``prediction`` rows (condition_id, model_p,
market_p) and the settlement loop appends ``outcome`` rows
(condition_id, outcome 0/1) to the same JSONL log. This script joins
predictions to their market outcome by condition_id (first outcome
wins) and writes the ``{"n_bins", "bins"}`` table consumed by
``POLY_CALIBRATION_PATH``.

Usage:
    .venv/bin/python scripts/build_calibration_table.py \
        --log data/prediction_log.jsonl --out data/calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from src.trading.signals.calibration import build_table


def build_table_from_log(log_path: str, n_bins: int = 5) -> dict:
    """Join a prediction/outcome JSONL log into a calibration table."""
    predictions: list[dict] = []
    outcomes: dict[str, float] = {}
    with open(log_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            kind = row.get("type")
            if kind == "outcome" and "condition_id" in row:
                try:
                    outcome = float(row["outcome"])
                except (TypeError, ValueError):
                    continue
                if outcome in (0.0, 1.0):
                    outcomes.setdefault(str(row["condition_id"]), outcome)
            elif kind == "prediction":
                predictions.append(row)
    rows = []
    for pred in predictions:
        outcome = outcomes.get(str(pred.get("condition_id", "")))
        if outcome is None:
            continue
        try:
            rows.append({
                "model_probability": float(pred["model_p"]),
                "market_price": float(pred["market_p"]),
                "actual_outcome": outcome,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return {"n_bins": n_bins, "bins": build_table(rows, n_bins=n_bins)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="data/prediction_log.jsonl")
    parser.add_argument("--out", default="data/calibration.json")
    parser.add_argument("--n-bins", type=int, default=5)
    args = parser.parse_args()
    table = build_table_from_log(args.log, n_bins=args.n_bins)
    with open(args.out, "w") as fh:
        json.dump(table, fh, indent=2)
    total = sum(b["n"] for b in table["bins"])
    print(f"Wrote {args.out}: {total} joined predictions")


if __name__ == "__main__":
    main()
