"""Backtest report generation — assembles BacktestResult from raw data.

Produces both the in-memory ``BacktestResult`` dataclass and JSON/CSV
serialisation for external analysis.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .base import BacktestConfig, BacktestRecord, BacktestResult, TradeRecord

logger = logging.getLogger(__name__)


def build_report(
    config: BacktestConfig,
    trades: list[TradeRecord],
    equity_curve: list[float],
    metrics: dict[str, Any],
    start_date: str,
    end_date: str,
    records: list[BacktestRecord],
) -> BacktestResult:
    """Assemble all backtest data into a ``BacktestResult``."""
    closed = [t for t in trades if t.outcome is not None]

    n_wins = sum(1 for t in closed if t.pnl_usdc > 0)
    n_losses = sum(1 for t in closed if t.pnl_usdc < 0)

    cities = sorted({r.city for r in records})

    winners = [t for t in closed if t.pnl_usdc > 0]
    losers = [t for t in closed if t.pnl_usdc < 0]
    avg_winner_pct = (
        sum(t.pnl_pct for t in winners) / len(winners) if winners else 0.0
    )
    avg_loser_pct = (
        sum(t.pnl_pct for t in losers) / len(losers) if losers else 0.0
    )

    # Average hold "days" = average gap between trade entry and target date
    hold_days = 0.0
    if trades:
        days = []
        for t in trades:
            try:
                entry = datetime.strptime(t.timestamp[:10], "%Y-%m-%d")
                target = datetime.strptime(t.target_date[:10], "%Y-%m-%d")
                days.append(abs((target - entry).days))
            except (ValueError, IndexError):
                days.append(0)
        hold_days = sum(days) / len(days) if days else 0.0

    return BacktestResult(
        config=config,
        trades=trades,
        final_bankroll=round(equity_curve[-1], 2) if equity_curve else config.initial_bankroll,
        total_pnl_usdc=metrics.get("total_pnl_usdc", 0.0),
        total_pnl_pct=metrics.get("total_pnl_pct", 0.0),
        total_fees=sum(
            t.size_usdc * config.maker_fee for t in trades if t.size_usdc
        ),
        n_trades=metrics.get("n_trades", len(trades)),
        n_wins=n_wins,
        n_losses=n_losses,
        win_rate=metrics.get("win_rate", 0.0),
        total_volume=metrics.get("total_volume", 0.0),
        gross_profit=metrics.get("gross_profit_usdc", 0.0),
        gross_loss=metrics.get("gross_loss_usdc", 0.0),
        profit_factor=metrics.get("profit_factor", 0.0),
        sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
        max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
        avg_return_pct=metrics.get("avg_return_pct", 0.0),
        std_return_pct=metrics.get("std_return_pct", 0.0),
        best_trade_pnl=metrics.get("best_trade_pnl", 0.0),
        worst_trade_pnl=metrics.get("worst_trade_pnl", 0.0),
        avg_winner_pct=round(avg_winner_pct, 4),
        avg_loser_pct=round(avg_loser_pct, 4),
        avg_hold_days=round(hold_days, 1),
        start_date=start_date,
        end_date=end_date,
        cities=cities,
        equity_curve=equity_curve,
        metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_records": len(records),
        },
    )


def result_to_dict(result: BacktestResult) -> dict[str, Any]:
    """Serialize a ``BacktestResult`` to a plain dict for JSON export."""
    return {
        "schema_version": "backtest_report.v1",
        "config": {
            "initial_bankroll": result.config.initial_bankroll,
            "edge_threshold": result.config.edge_threshold,
            "max_price_for_buy": result.config.max_price_for_buy,
            "min_price_for_sell": result.config.min_price_for_sell,
            "kelly_fraction": result.config.kelly_fraction,
            "max_position_size_usdc": result.config.max_position_size_usdc,
            "max_open_positions": result.config.max_open_positions,
            "maker_fee": result.config.maker_fee,
            "two_sided": result.config.two_sided,
            "min_confidence": result.config.min_confidence,
        },
        "summary": {
            "final_bankroll": result.final_bankroll,
            "total_pnl_usdc": result.total_pnl_usdc,
            "total_pnl_pct": result.total_pnl_pct,
            "total_fees": result.total_fees,
            "n_trades": result.n_trades,
            "n_wins": result.n_wins,
            "n_losses": result.n_losses,
            "win_rate": result.win_rate,
            "total_volume": result.total_volume,
            "gross_profit": result.gross_profit,
            "gross_loss": result.gross_loss,
            "profit_factor": result.profit_factor,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "avg_return_pct": result.avg_return_pct,
            "std_return_pct": result.std_return_pct,
            "best_trade_pnl": result.best_trade_pnl,
            "worst_trade_pnl": result.worst_trade_pnl,
            "avg_winner_pct": result.avg_winner_pct,
            "avg_loser_pct": result.avg_loser_pct,
            "avg_hold_days": result.avg_hold_days,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "cities": result.cities,
        },
        "trades": [
            {
                "city": t.city,
                "target_date": t.target_date,
                "direction": t.direction.value,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "size_usdc": t.size_usdc,
                "size_tokens": t.size_tokens,
                "pnl_usdc": t.pnl_usdc,
                "pnl_pct": t.pnl_pct,
                "outcome": t.outcome,
                "model_probability": t.model_probability,
                "market_price_at_entry": t.market_price_at_entry,
                "gap": t.gap,
                "entry_bankroll": t.entry_bankroll,
                "exit_bankroll": t.exit_bankroll,
                "timestamp": t.timestamp,
            }
            for t in result.trades
        ],
        "equity_curve": result.equity_curve,
        "metadata": result.metadata,
    }


def write_report(
    result: BacktestResult,
    *,
    json_path: Optional[str | Path] = None,
    csv_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Write backtest report to JSON and/or CSV.

    Args:
        result: The backtest result to export.
        json_path: Write JSON summary + trades to this path.
        csv_path: Write flat CSV of all trades to this path.

    Returns:
        The serialized dict (so the caller can inspect it).
    """
    data = result_to_dict(result)

    if json_path:
        target = Path(json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info("Backtest report written to %s", target)

    if csv_path:
        target = Path(csv_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if data["trades"]:
            fieldnames = list(data["trades"][0].keys())
            with open(target, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(data["trades"])
        else:
            target.write_text("")
        logger.info("Backtest trades CSV written to %s", target)

    return data
