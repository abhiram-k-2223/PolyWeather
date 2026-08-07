"""Backtesting framework base — Strategy ABC, data models, config.

Every backtestable strategy subclasses :class:`Strategy` and implements
:meth:`generate_signals`. The engine walks through historical records
chronologically, calls the strategy for each one, executes trades on
a simulated portfolio, and produces performance metrics.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SignalDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class BacktestRecord:
    """A single historical data point used to drive the backtest.

    Columns describe what the engine was able to know *at the time*
    the decision was made:

        city             — City name (lowercase).
        target_date      — Settlement date (YYYY-MM-DD).
        model_probability — Forecast probability (0–1) of the YES outcome
                           as known on the evaluation date.
        market_price     — Observed market price (0–1) at decision time.
        actual_outcome   — 1 if the YES outcome occurred, 0 otherwise.
                           ``None`` if settlement hasn't happened yet.
        icao             — Airport ICAO code.
        metadata         — Any extra info preserved for reporting.
    """

    city: str
    target_date: str
    model_probability: float
    market_price: float
    actual_outcome: Optional[float] = None
    icao: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeRecord:
    """A single simulated trade produced by the backtest engine.

    Every signal that passes risk checks generates one TradeRecord.
    If ``actual_outcome`` is known at simulation time the P&L is
    computed immediately; otherwise the trade is recorded as "open"
    and finalised when the engine receives a settlement record.
    """

    condition_id: str = ""
    direction: SignalDirection = SignalDirection.HOLD
    entry_price: float = 0.0
    size_usdc: float = 0.0
    size_tokens: float = 0.0
    entry_bankroll: float = 0.0
    exit_bankroll: float = 0.0
    pnl_usdc: float = 0.0
    pnl_pct: float = 0.0
    exit_price: float = 0.0
    outcome: Optional[float] = None
    city: str = ""
    target_date: str = ""
    model_probability: float = 0.0
    market_price_at_entry: float = 0.0
    gap: float = 0.0
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run.

    Attributes:
        initial_bankroll: Starting USDC balance.
        edge_threshold: Minimum gap to accept a trade (default 8 %).
        max_price_for_buy: Max market price for a BUY signal.
        min_price_for_sell: Min market price for a SELL signal.
        kelly_fraction: Fraction of full Kelly (0.25 = Quarter Kelly).
        max_position_size_usdc: Hard per-trade cap.
        max_open_positions: Simultaneous position limit.
        maker_fee: CLOB maker fee as a fraction (default 0.001).
        two_sided: Generate SELL signals too.
        min_confidence: Minimum model_probability to consider a signal.
    """

    initial_bankroll: float = 10_000.0
    edge_threshold: float = 0.08
    max_price_for_buy: float = 0.10
    min_price_for_sell: float = 0.90
    kelly_fraction: float = 0.25
    max_position_size_usdc: float = 500.0
    max_open_positions: int = 10
    maker_fee: float = 0.001
    two_sided: bool = False
    min_confidence: float = 0.6


@dataclass
class BacktestResult:
    """Aggregated result of a backtest run."""

    config: BacktestConfig = field(default_factory=BacktestConfig)
    trades: list[TradeRecord] = field(default_factory=list)
    final_bankroll: float = 0.0
    total_pnl_usdc: float = 0.0
    total_pnl_pct: float = 0.0
    total_fees: float = 0.0
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0
    total_volume: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_return_pct: float = 0.0
    std_return_pct: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    avg_winner_pct: float = 0.0
    avg_loser_pct: float = 0.0
    avg_hold_days: float = 0.0
    start_date: str = ""
    end_date: str = ""
    cities: list[str] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    """Abstract base class for backtest strategies.

    A strategy receives a historical record and returns a decision:
    BUY / SELL / HOLD with an optional confidence override.
    """

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self.config = config or BacktestConfig()

    @abstractmethod
    def generate_signals(
        self, records: list[BacktestRecord], idx: int
    ) -> list[SignalDirection]:
        """Return signal directions for the record at ``idx``.

        Most strategies return a single-element list, but multi-market
        strategies may return several signals from one record.

        Args:
            records: Full chronological history for one city.
            idx: Current index being evaluated.

        Returns:
            One or more ``SignalDirection`` values.
        """
        ...

    def on_backtest_start(self, config: BacktestConfig) -> None:
        """Hook called once before the backtest begins."""
        ...

    def on_backtest_end(self, result: BacktestResult) -> None:
        """Hook called once after the backtest finishes."""
        ...
