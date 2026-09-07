#!/usr/bin/env python3
"""Convert Open-Meteo history into trading-backtest records.

.. warning::
    SYNTHETIC MARKET — NOT real Polymarket prices. The "market price" here
    is a manufactured reference derived from the same forecast signal
    (naive equal-weight ensemble plus noise). Any edge measured against it
    is circular and does NOT imply edge against real traders. Use
    ``scripts/backtest_real_polymarket.py`` for validation against real
    historical Polymarket prices before allocating capital.

Simulates a Polymarket-style daily market per city-day:

- Market question: "will the daily high settle in bucket B?" where B is
  the bucket implied by a naive equal-weight ensemble (the "market's"
  reference forecast), with a small price noise for liquidity.
- Model probability: DEB walk-forward prediction (using only prior
  days' history), converted to a bucket probability with a Gaussian
  CDF whose sigma reflects inter-model disagreement.
- actual_outcome: 1 if the ERA5 observed high settled in bucket B.

Output feeds scripts/backtester/run.py --records.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis import deb_algorithm  # noqa: E402
from src.analysis.settlement_rounding import apply_city_settlement  # noqa: E402

DEFAULT_HISTORY = ROOT / "data" / "openmeteo_history.json"
DEFAULT_OUTPUT = ROOT / "data" / "openmeteo_backtest_records.json"

BASE_SIGMA = 0.8  # °C, minimum forecast uncertainty
MARKET_NOISE_SIGMA = 0.35  # °C, market's extra error on top of equal-weight


def _bucket_prob(mean: float, sigma: float, bucket: int) -> float:
    """P(high in [bucket-0.5, bucket+0.5)) under N(mean, sigma)."""
    lo = (bucket - 0.5 - mean) / (sigma * math.sqrt(2))
    hi = (bucket + 0.5 - mean) / (sigma * math.sqrt(2))
    return 0.5 * (math.erf(hi) - math.erf(lo))


def build_records(
    history: dict,
    *,
    calibrated: dict[str, float] | None = None,
) -> list[dict]:
    original_load = deb_algorithm.load_history
    records: list[dict] = []
    identical_days = 0
    comparable_days = 0
    try:
        for city, city_history in history.items():
            dates = sorted(city_history)
            for i, date in enumerate(dates):
                record = city_history[date]
                forecasts = record["forecasts"]
                actual_high = record["actual_high"]
                if actual_high is None:
                    # Forecast-only tail (no ERA5 observation yet):
                    # no settled outcome exists — skip in the synthetic
                    # builder (the real-price path resolves via market).
                    continue
                values = [v for v in forecasts.values() if v is not None]
                if len(values) < 2:
                    continue
                comparable_days += 1
                if len(set(round(float(v), 1) for v in values)) == 1:
                    identical_days += 1

                equal_weight = sum(values) / len(values)
                market_bucket = apply_city_settlement(city, equal_weight + 0.0)
                if market_bucket is None:
                    continue

                prior = {d: city_history[d] for d in dates[:i]}
                deb_algorithm.load_history = lambda _path, _prior=prior: _prior
                components = deb_algorithm.calculate_dynamic_weight_components(
                    city, forecasts
                )
                prediction = components.get("prediction")
                if prediction is None:
                    continue

                disagreement = max(values) - min(values)
                sigma = max(BASE_SIGMA, disagreement / 2.0)
                model_prob = _bucket_prob(prediction, sigma, market_bucket)
                cal_key = f"{city}|{date}"
                if calibrated and cal_key in calibrated:
                    try:
                        cal = float(calibrated[cal_key])
                        if 0 < cal < 1:
                            model_prob = cal
                    except (TypeError, ValueError):
                        pass
                market_prob = _bucket_prob(
                    equal_weight,
                    sigma + MARKET_NOISE_SIGMA,
                    market_bucket,
                )

                actual_bucket = apply_city_settlement(city, actual_high)
                outcome = 1.0 if actual_bucket == market_bucket else 0.0

                records.append(
                    {
                        "city": city,
                        "target_date": date,
                        "model_probability": round(model_prob, 4),
                        "market_price": round(market_prob, 4),
                        "actual": outcome,
                        "metadata": {
                            "deb_prediction": prediction,
                            "equal_weight": round(equal_weight, 1),
                            "actual_high": actual_high,
                            "market_bucket": market_bucket,
                            "synthetic_market": True,
                        },
                    }
                )
    finally:
        deb_algorithm.load_history = original_load
    if comparable_days:
        share = identical_days / comparable_days
        print(
            f"Model diversity: {identical_days}/{comparable_days} city-days "
            f"identical ({share:.1%})",
            file=sys.stderr,
        )
        if share > 0.5:
            print(
                "WARNING: >50% of city-days have identical model forecasts — "
                "the ensemble has collapsed to a single signal and this "
                "synthetic backtest is circular. Refetch history with "
                "scripts/fetch_openmeteo_history.py (fixed 'models' param).",
                file=sys.stderr,
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=str, default=str(DEFAULT_HISTORY))
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--calibrated-probs", type=str, default=None,
                        help="Optional JSON {city|date: prob} from "
                             "scripts/fit_platt_calibration.py")
    args = parser.parse_args()

    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    calibrated = None
    if args.calibrated_probs:
        calibrated = json.loads(Path(args.calibrated_probs).read_text(encoding="utf-8"))
        if isinstance(calibrated, dict) and "calibrated" in calibrated:
            calibrated = calibrated["calibrated"]
    records = build_records(history, calibrated=calibrated)
    Path(args.output).write_text(
        json.dumps({"records": records}, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(records)} records to {args.output}")


if __name__ == "__main__":
    main()
