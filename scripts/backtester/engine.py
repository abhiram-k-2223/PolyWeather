"""Walk-forward portfolio simulation engine.

Drives a chronological backtest by feeding historical records to a
strategy, executing simulated trades on a portfolio, tracking P&L,
and producing an equity curve. Supports per-city walk-forward and
global portfolio aggregation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from .base import (
    BacktestConfig,
    BacktestRecord,
    BacktestResult,
    SignalDirection,
    Strategy,
    TradeRecord,
)
from .metrics import compute_metrics
from .report import build_report

logger = logging.getLogger(__name__)


def _compute_kelly_size(
    model_prob: float,
    market_price: float,
    direction: SignalDirection,
    bankroll: float,
    config: BacktestConfig,
) -> float:
    """Compute Quarter Kelly position size (matches production code)."""
    if direction == SignalDirection.SELL:
        win_p = 1.0 - model_prob
    else:
        win_p = model_prob

    if not (0 < win_p < 1) or not (0 < market_price < 1):
        return 0.0

    edge = win_p - market_price
    if edge <= 1e-8:
        return 0.0

    odds = 1.0 - market_price
    f_star = edge / odds
    quarter_kelly = config.kelly_fraction * max(0.0, min(1.0, f_star))
    raw_size = quarter_kelly * bankroll
    return min(raw_size, config.max_position_size_usdc)


def run_backtest(
    records: list[BacktestRecord],
    strategy: Strategy,
    config: Optional[BacktestConfig] = None,
) -> BacktestResult:
    """Run a chronological walk-forward backtest.

    Args:
        records: Historical data sorted by (city, target_date).
        strategy: Strategy instance whose ``generate_signals`` is called
                  for each record.
        config: Backtest configuration.

    Returns:
        A populated ``BacktestResult`` with trades, equity curve, and
        aggregated performance metrics.
    """
    cfg = config or strategy.config or BacktestConfig()

    # Sort chronologically per city then globally
    records = sorted(records, key=lambda r: (r.city, r.target_date))
    by_city: dict[str, list[BacktestRecord]] = defaultdict(list)
    for r in records:
        by_city[r.city].append(r)

    trades: list[TradeRecord] = []
    bankroll = cfg.initial_bankroll
    open_positions: dict[str, dict] = {}  # city -> {size_usdc, size_tokens, entry_price}
    equity_curve: list[float] = [bankroll]
    total_fees = 0.0
    start_date = records[0].target_date if records else ""
    end_date = records[-1].target_date if records else ""

    strategy.on_backtest_start(cfg)

    for city, city_records in by_city.items():
        for idx in range(len(city_records)):
            rec = city_records[idx]
            local_bankroll = bankroll

            # --- Close position if settlement data is available ---
            if rec.actual_outcome is not None and city in open_positions:
                pos = open_positions.pop(city)
                payout = pos["size_tokens"] * rec.actual_outcome
                cost = pos["size_usdc"]
                fee = cost * cfg.maker_fee
                total_fees += fee
                bankroll += payout - fee

            # --- Generate signals via strategy ---
            signals = strategy.generate_signals(city_records, idx)

            for sig_dir in signals:
                if sig_dir == SignalDirection.HOLD:
                    continue
                if city in open_positions:
                    continue
                if len(open_positions) >= cfg.max_open_positions:
                    continue
                if rec.market_price <= 0 or rec.model_probability <= 0:
                    continue

                gap = rec.model_probability - rec.market_price
                size_usdc = _compute_kelly_size(
                    model_prob=rec.model_probability,
                    market_price=rec.market_price,
                    direction=sig_dir,
                    bankroll=local_bankroll,
                    config=cfg,
                )
                if size_usdc <= 0:
                    continue

                size_tokens = size_usdc / rec.market_price
                entry_fee = size_usdc * cfg.maker_fee
                total_fees += entry_fee
                bankroll -= size_usdc + entry_fee
                open_positions[city] = {
                    "size_usdc": size_usdc,
                    "size_tokens": size_tokens,
                    "entry_price": rec.market_price,
                }

                trade = TradeRecord(
                    direction=sig_dir,
                    entry_price=rec.market_price,
                    size_usdc=size_usdc,
                    size_tokens=size_tokens,
                    entry_bankroll=local_bankroll,
                    exit_bankroll=0.0,
                    pnl_usdc=0.0,
                    pnl_pct=0.0,
                    exit_price=0.0,
                    outcome=None,
                    city=city,
                    target_date=rec.target_date,
                    model_probability=rec.model_probability,
                    market_price_at_entry=rec.market_price,
                    gap=gap,
                    timestamp=rec.target_date,
                )
                trades.append(trade)

        # --- Final settlement for remaining positions ---
        last_rec = city_records[-1]
        if city in open_positions and last_rec.actual_outcome is not None:
            pos = open_positions.pop(city)
            payout = pos["size_tokens"] * last_rec.actual_outcome
            cost = pos["size_usdc"]
            fee = cost * cfg.maker_fee
            bankroll += payout - fee
            total_fees += fee

        equity_curve.append(bankroll)

    strategy.on_backtest_end(BacktestResult())

    # --- Reconcile open positions with actual outcomes ---
    for t in trades:
        outcome = None
        for r in records:
            if r.city == t.city and r.target_date == t.target_date:
                outcome = r.actual_outcome
                break
        if outcome is not None:
            t.outcome = outcome
            t.exit_price = outcome
            cost = t.size_usdc
            payout = t.size_tokens * outcome
            fee = cost * cfg.maker_fee
            t.pnl_usdc = payout - cost - fee
            t.exit_bankroll = t.entry_bankroll + t.pnl_usdc
            t.pnl_pct = t.pnl_usdc / cost if cost > 0 else 0.0

    n_closed = sum(1 for t in trades if t.outcome is not None)
    if n_closed < len(trades):
        logger.warning(
            "%d/%d trades could not be settled (missing outcome data)",
            len(trades) - n_closed,
            len(trades),
        )

    metrics = compute_metrics(trades, equity_curve, cfg)
    return build_report(cfg, trades, equity_curve, metrics, start_date, end_date, records)
