"""PolyWeather Backtesting Framework.

Walk-forward portfolio simulator purpose-built for the forecast-gap
strategy that powers the AI Bot approach. Outputs trade-level and
portfolio-level metrics for strategy validation.

Quick start:
    python -m scripts.backtester.run --records data/backtest_records.json

Or run with synthetic demo data:
    python -m scripts.backtester.run --demo
"""

from .base import (
    BacktestConfig,
    BacktestRecord,
    BacktestResult,
    SignalDirection,
    Strategy,
    TradeRecord,
)
from .engine import run_backtest
from .metrics import compute_metrics
from .report import result_to_dict, write_report

__all__ = [
    "BacktestConfig",
    "BacktestRecord",
    "BacktestResult",
    "SignalDirection",
    "Strategy",
    "TradeRecord",
    "run_backtest",
    "compute_metrics",
    "result_to_dict",
    "write_report",
]
