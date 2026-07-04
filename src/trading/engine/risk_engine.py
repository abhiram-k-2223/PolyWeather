"""Risk engine — position sizing, exposure limits, and circuit breakers.

Enforces configurable risk controls on all trading activity:
max position size per market, max total exposure, max drawdown,
and cooldown periods after losses.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .order_manager import TrackedOrder

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Risk parameters for the trading engine.

    Attributes:
        max_position_size_usdc: Maximum USDC committed to any single market.
        max_total_exposure_usdc: Maximum total USDC across all open positions.
        max_order_count: Maximum number of simultaneous open orders.
        max_daily_trades: Maximum trades per day.
        min_confidence_threshold: Minimum signal confidence (0-1) to trade.
        cooldown_seconds: Seconds to wait after a losing trade before trading again.
        max_drawdown_pct: Maximum allowed drawdown as fraction of portfolio.
        max_slippage_bps: Maximum allowed slippage in basis points.
    """

    max_position_size_usdc: float = 500.0
    max_total_exposure_usdc: float = 5000.0
    max_order_count: int = 10
    max_daily_trades: int = 50
    min_confidence_threshold: float = 0.6
    cooldown_seconds: float = 300.0  # 5 min after loss
    max_drawdown_pct: float = 0.15  # 15%
    max_slippage_bps: int = 50  # 0.5%


@dataclass
class RiskAssessment:
    """Result of a risk check before placing an order."""

    allowed: bool
    reason: str = ""
    current_exposure: float = 0.0
    current_positions: int = 0


class RiskEngine:
    """Evaluates trades against risk parameters before execution.

    Tracks portfolio-level metrics and enforces hard limits.
    State is held in-memory and should be periodically persisted.
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self._config = config or RiskConfig()
        self._daily_trades: int = 0
        self._daily_trades_date: Optional[str] = None
        self._last_loss_time: float = 0.0
        self._drawdown_pct: float = 0.0
        self._peak_portfolio_value: float = 0.0

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def assess(
        self,
        signal_confidence: float,
        position_size: float,
        open_orders: list[TrackedOrder],
        total_portfolio_value: float,
        condition_exposure: float = 0.0,
    ) -> RiskAssessment:
        """Run all risk checks for a proposed trade.

        Args:
            signal_confidence: Confidence in the signal (0–1).
            position_size: Proposed position size in USDC.
            open_orders: Currently open orders.
            total_portfolio_value: Total account value in USDC.
            condition_exposure: Current exposure to this specific condition.

        Returns:
            RiskAssessment with allowed=True if all checks pass.
        """
        self._update_daily_trades()

        # -- confidence check --
        if signal_confidence < self._config.min_confidence_threshold:
            return RiskAssessment(
                allowed=False,
                reason=(
                    f"Confidence {signal_confidence:.2f} < "
                    f"min {self._config.min_confidence_threshold}"
                ),
                current_exposure=self._current_exposure(open_orders),
                current_positions=len(open_orders),
            )

        # -- position size check --
        if position_size > self._config.max_position_size_usdc:
            return RiskAssessment(
                allowed=False,
                reason=(
                    f"Position size ${position_size:.0f} > "
                    f"max ${self._config.max_position_size_usdc:.0f}"
                ),
                current_exposure=self._current_exposure(open_orders),
                current_positions=len(open_orders),
            )

        # -- total exposure check --
        new_exposure = self._current_exposure(open_orders) + position_size
        if new_exposure > self._config.max_total_exposure_usdc:
            return RiskAssessment(
                allowed=False,
                reason=(
                    f"Total exposure ${new_exposure:.0f} > "
                    f"max ${self._config.max_total_exposure_usdc:.0f}"
                ),
                current_exposure=self._current_exposure(open_orders),
                current_positions=len(open_orders),
            )

        # -- max orders check --
        if len(open_orders) >= self._config.max_order_count:
            return RiskAssessment(
                allowed=False,
                reason=f"Order count {len(open_orders)} >= max {self._config.max_order_count}",
                current_exposure=self._current_exposure(open_orders),
                current_positions=len(open_orders),
            )

        # -- daily trade limit --
        if self._daily_trades >= self._config.max_daily_trades:
            return RiskAssessment(
                allowed=False,
                reason=f"Daily trades {self._daily_trades} >= max {self._config.max_daily_trades}",
                current_exposure=self._current_exposure(open_orders),
                current_positions=len(open_orders),
            )

        # -- cooldown after loss --
        if self._last_loss_time > 0:
            elapsed = time.time() - self._last_loss_time
            if elapsed < self._config.cooldown_seconds:
                remaining = self._config.cooldown_seconds - elapsed
                return RiskAssessment(
                    allowed=False,
                    reason=f"In cooldown for {remaining:.0f}s more",
                    current_exposure=self._current_exposure(open_orders),
                    current_positions=len(open_orders),
                )

        # -- drawdown check --
        if total_portfolio_value > 0:
            self._peak_portfolio_value = max(
                self._peak_portfolio_value, total_portfolio_value
            )
            self._drawdown_pct = (
                self._peak_portfolio_value - total_portfolio_value
            ) / self._peak_portfolio_value
            if self._drawdown_pct > self._config.max_drawdown_pct:
                return RiskAssessment(
                    allowed=False,
                    reason=(
                        f"Drawdown {self._drawdown_pct:.1%} > "
                        f"max {self._config.max_drawdown_pct:.1%}"
                    ),
                    current_exposure=self._current_exposure(open_orders),
                    current_positions=len(open_orders),
                )

        return RiskAssessment(
            allowed=True,
            current_exposure=self._current_exposure(open_orders),
            current_positions=len(open_orders),
        )

    # ------------------------------------------------------------------
    # Portfolio tracking
    # ------------------------------------------------------------------

    def record_trade(self, pnl: float) -> None:
        """Record a completed trade for tracking daily limits and cooldown."""
        self._daily_trades += 1
        if pnl < 0:
            self._last_loss_time = time.time()
            logger.info("Trade recorded with PnL=%.2f — loss cooldown started", pnl)

    def reset_daily(self) -> None:
        """Reset daily counters (called at UTC midnight)."""
        self._daily_trades = 0
        self._daily_trades_date = None
        logger.info("Daily trade counter reset")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _current_exposure(self, open_orders: list[TrackedOrder]) -> float:
        return sum(
            o.price * o.size
            for o in open_orders
        )

    def _update_daily_trades(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_trades_date != today:
            self._daily_trades = 0
            self._daily_trades_date = today

    @property
    def config(self) -> RiskConfig:
        return self._config

    @config.setter
    def config(self, value: RiskConfig) -> None:
        self._config = value
