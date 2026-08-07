#!/usr/bin/env python3
"""CLI entry point for the backtesting framework.

Supports running from pre-collected historical records (JSON) or
generating a synthetic demo dataset for testing.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtester.base import BacktestConfig, BacktestRecord  # noqa: E402
from scripts.backtester.engine import run_backtest  # noqa: E402
from scripts.backtester.report import write_report  # noqa: E402
from scripts.backtester.strategies.forecast_gap import (  # noqa: E402
    ConservativeGapStrategy,
    ForecastGapStrategy,
)

STRATEGIES = {
    "forecast_gap": ForecastGapStrategy,
    "conservative_gap": ConservativeGapStrategy,
}


def _parse_records(path: str) -> list[BacktestRecord]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("records", raw.get("data", []))
    records: list[BacktestRecord] = []
    for item in raw:
        records.append(
            BacktestRecord(
                city=str(item.get("city", "")).lower(),
                target_date=str(item.get("target_date", item.get("date", ""))),
                model_probability=float(item.get("model_probability", item.get("prob", 0))),
                market_price=float(item.get("market_price", item.get("price", 0))),
                actual_outcome=(
                    float(item["actual"])
                    if item.get("actual") is not None
                    else None
                ),
                icao=str(item.get("icao", "")),
                metadata=item.get("metadata", {}),
            )
        )
    return records


def _generate_demo_records(
    n_cities: int = 5,
    days: int = 360,
) -> list[BacktestRecord]:
    """Generate synthetic historical data for testing.

    Simulates Polymarket weather markets where:
    - Most days have no mispricing (market ≈ 50-50).
    - On ~5-10 % of days the model detects a genuine edge the market
      hasn't priced in (e.g., model says 60 %, market says 10 %).
    - The model's edge is real — the underestimated outcome happens
      at a rate close to the model's probability.
    """
    random.seed(42)
    cities = [
        ("new york", "KNYC"),
        ("london", "EGLL"),
        ("tokyo", "RJTT"),
        ("sydney", "YSSY"),
        ("dubai", "OMDB"),
        ("paris", "LFPG"),
        ("singapore", "WSSS"),
        ("mumbai", "VABB"),
        ("seoul", "RKSI"),
        ("chicago", "KORD"),
    ][:n_cities]

    records: list[BacktestRecord] = []
    base_date = datetime(2025, 1, 1, tzinfo=timezone.utc)

    for city_name, icao in cities:
        for day_offset in range(days):
            target = base_date + timedelta(days=day_offset)
            target_str = target.strftime("%Y-%m-%d")

            # ~92 % of days: normal market, no edge
            # ~8 % of days: the model spots a real mispricing
            is_mispricing_day = random.random() < 0.08

            if is_mispricing_day:
                # Model sees a genuine anomalous event (e.g., 35°C+ heatwave)
                # True probability of the event is high, market undershoots it
                true_prob = random.uniform(0.40, 0.85)

                # Model captures this well (small noise)
                model_prob = max(0.05, min(0.95, true_prob + random.gauss(0, 0.04)))

                # Market severely underprices the outcome — this is the edge
                market_price = max(0.01, min(0.98, true_prob - random.uniform(0.08, 0.40)))
            else:
                # Normal day: no meaningful edge
                base = 0.50 + random.gauss(0, 0.03)
                base = max(0.10, min(0.90, base))
                model_prob = max(0.05, min(0.95, base + random.gauss(0, 0.03)))
                market_price = max(0.01, min(0.99, base + random.gauss(0, 0.03)))

            # Actual outcome based on true_prob
            if is_mispricing_day:
                outcome = 1.0 if random.random() < true_prob else 0.0
            else:
                outcome = 1.0 if random.random() < 0.50 else 0.0

            records.append(
                BacktestRecord(
                    city=city_name,
                    target_date=target_str,
                    model_probability=round(model_prob, 4),
                    market_price=round(market_price, 4),
                    actual_outcome=outcome,
                    icao=icao,
                )
            )

    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="PolyWeather strategy backtester")
    parser.add_argument("--records", type=str, help="Path to historical records JSON")
    parser.add_argument(
        "--strategy",
        type=str,
        default="forecast_gap",
        choices=list(STRATEGIES),
        help="Trading strategy to backtest",
    )
    parser.add_argument("--initial-bankroll", type=float, default=10_000.0)
    parser.add_argument("--edge-threshold", type=float, default=0.08)
    parser.add_argument("--max-price", type=float, default=0.10)
    parser.add_argument("--kelly-fraction", type=float, default=0.25)
    parser.add_argument("--max-position-size", type=float, default=500.0)
    parser.add_argument("--two-sided", action="store_true")
    parser.add_argument("--output-json", type=str, help="Path for JSON report")
    parser.add_argument("--output-csv", type=str, help="Path for CSV trade log")
    parser.add_argument("--demo", action="store_true", help="Run with synthetic demo data")

    args = parser.parse_args()

    if args.demo:
        records = _generate_demo_records()
        print(f"Generated {len(records)} demo records across "
              f"{len({r.city for r in records})} cities")
    elif args.records:
        records = _parse_records(args.records)
        print(f"Loaded {len(records)} historical records")
    else:
        parser.print_help()
        print("\nError: provide --records or --demo")
        return 1

    if not records:
        print("Error: no records to backtest")
        return 1

    config = BacktestConfig(
        initial_bankroll=args.initial_bankroll,
        edge_threshold=args.edge_threshold,
        max_price_for_buy=args.max_price,
        kelly_fraction=args.kelly_fraction,
        max_position_size_usdc=args.max_position_size,
        two_sided=args.two_sided,
    )

    strategy_cls = STRATEGIES[args.strategy]
    strategy = strategy_cls(config)
    result = run_backtest(records, strategy, config)

    data = write_report(
        result,
        json_path=args.output_json,
        csv_path=args.output_csv,
    )

    summary = data["summary"]
    print(f"\n{'='*60}")
    print(f"  Strategy: {args.strategy}")
    print(f"  Period:   {summary['start_date']} → {summary['end_date']}")
    print(f"  Cities:   {', '.join(summary['cities'][:5])}{'...' if len(summary['cities']) > 5 else ''}")
    print(f"{'='*60}")
    print(f"  Trades:         {summary['n_trades']}")
    print(f"  Win rate:       {summary['win_rate']*100:.1f}%")
    print(f"  Total P&L:      ${summary['total_pnl_usdc']:+,.2f}")
    print(f"  Return:         {summary['total_pnl_pct']*100:+.2f}%")
    print(f"  Profit factor:  {summary['profit_factor']:.2f}")
    print(f"  Sharpe ratio:   {summary['sharpe_ratio']:.2f}")
    print(f"  Max drawdown:   {summary['max_drawdown_pct']*100:.1f}%")
    print(f"  Final bankroll: ${summary['final_bankroll']:,.2f}")
    print(f"  Total volume:   ${summary['total_volume']:,.2f}")
    print(f"  Avg hold:       {summary['avg_hold_days']:.1f} days")
    print(f"  Best trade:     ${summary['best_trade_pnl']:+,.2f}")
    print(f"  Worst trade:    ${summary['worst_trade_pnl']:+,.2f}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
