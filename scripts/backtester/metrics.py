"""Performance metrics computation for backtest results.

Provides standard trading metrics: win rate, profit factor, Sharpe
ratio, max drawdown, and per-trade distribution statistics.
"""

from __future__ import annotations

import math
from typing import Any

from .base import BacktestConfig, TradeRecord


def compute_metrics(
    trades: list[TradeRecord],
    equity_curve: list[float],
    config: BacktestConfig,
) -> dict[str, Any]:
    """Compute all performance metrics from a list of closed trades.

    Args:
        trades: All trade records (open trades are excluded from
                rate-based metrics but included in exposure stats).
        equity_curve: Portfolio value after each step.
        config: Backtest configuration (used for reference).

    Returns:
        Dictionary of metric names to values.
    """
    closed = [t for t in trades if t.outcome is not None]
    n_total = len(trades)
    n_closed = len(closed)
    n_open = n_total - n_closed

    if not closed:
        return {
            "n_trades": 0,
            "n_closed": 0,
            "n_open": n_open,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "total_pnl_usdc": 0.0,
            "total_pnl_pct": 0.0,
            "total_fees": sum(t.metadata.get("fee", 0) for t in trades),
            "total_volume": sum(t.size_usdc for t in trades),
            "avg_return_pct": 0.0,
            "std_return_pct": 0.0,
            "best_trade_pnl": 0.0,
            "worst_trade_pnl": 0.0,
            "avg_winner_pct": 0.0,
            "avg_loser_pct": 0.0,
        }

    pnls = [t.pnl_usdc for t in closed]
    returns_pct = [t.pnl_pct for t in closed]

    n_wins = sum(1 for p in pnls if p > 0)
    n_losses = sum(1 for p in pnls if p < 0)
    win_rate = n_wins / n_closed if n_closed > 0 else 0.0

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe ratio (annualised, assuming daily returns)
    avg_ret = sum(returns_pct) / len(returns_pct)
    std_ret = (
        math.sqrt(sum((r - avg_ret) ** 2 for r in returns_pct) / len(returns_pct))
        if len(returns_pct) > 1
        else 0.0
    )
    sharpe = (avg_ret / std_ret * math.sqrt(365)) if std_ret > 0 else 0.0

    # Max drawdown from equity curve
    max_dd = 0.0
    peak = equity_curve[0] if equity_curve else 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    total_pnl = sum(pnls)
    initial = config.initial_bankroll
    total_pnl_pct = total_pnl / initial if initial > 0 else 0.0

    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    avg_winner_pct = (
        sum(t.pnl_pct for t in closed if t.pnl_usdc > 0) / len(winners) if winners else 0.0
    )
    avg_loser_pct = (
        sum(t.pnl_pct for t in closed if t.pnl_usdc < 0) / len(losers) if losers else 0.0
    )

    return {
        "n_trades": n_total,
        "n_closed": n_closed,
        "n_open": n_open,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate": round(win_rate, 4),
        "gross_profit_usdc": round(gross_profit, 2),
        "gross_loss_usdc": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "total_pnl_usdc": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 4),
        "total_volume": round(sum(t.size_usdc for t in trades), 2),
        "avg_return_pct": round(avg_ret, 4),
        "std_return_pct": round(std_ret, 4),
        "best_trade_pnl": round(max(pnls), 2),
        "worst_trade_pnl": round(min(pnls), 2),
        "avg_winner_pct": round(avg_winner_pct, 4),
        "avg_loser_pct": round(avg_loser_pct, 4),
    }
