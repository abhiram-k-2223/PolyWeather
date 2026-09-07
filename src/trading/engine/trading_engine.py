"""Trading engine — orchestrates signal ingestion, order management,
risk controls, and position tracking.

The engine runs as a background coroutine loop (driven by AsyncManager)
that:
  1. Ingests weather signals from the analysis pipeline
  2. Assesses risk and filters signals
  3. Places/cancels orders on the Polymarket CLOB
  4. Tracks positions and reconciles state
  5. Logs all activity to storage
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ...async_infra.event_loop import get_async_manager
from ..polymarket.clob_client import CLOBClient
from ..polymarket.data_api_client import DataAPIClient
from ..polymarket.wallet import WalletManager
from .order_manager import OrderManager, OrderState, TrackedOrder
from .position_tracker import PositionTracker
from .risk_engine import RiskConfig, RiskEngine
from .signal_ingestion import (
    SignalDirection,
    SignalIngestor,
    TradeSignal,
    WeatherObservationSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """Top-level configuration for the trading engine.

    Attributes:
        enabled: Master kill switch (set via env var or config).
        poll_interval_seconds: How often to check for new signals.
        reconcile_interval_seconds: How often to sync with CLOB state.
        max_orders_per_run: Max orders to place per engine cycle.
        trade_on_signals: If True, auto-place orders on signals.
        risk: Risk parameters.
        city_to_market_map: ICAO -> (condition_id, token_id) mapping.
    """

    enabled: bool = False
    poll_interval_seconds: float = 60.0
    reconcile_interval_seconds: float = 300.0
    max_orders_per_run: int = 3
    trade_on_signals: bool = True
    risk: RiskConfig = field(default_factory=RiskConfig)

    # ICAO -> (condition_id, token_id)
    city_to_market_map: dict[str, tuple[str, str]] = field(default_factory=dict)

    # Paper-trading cash baseline used by _estimate_portfolio_value() when
    # no live CLOB balance has been cached yet. Explicitly a paper figure —
    # live trading must call refresh_portfolio_from_clob() first.
    paper_base_usdc: float = 10000.0


class TradingEngine:
    """Main trading engine — orchestrates the full pipeline.

    Typical lifecycle:
        engine = TradingEngine(wallet, config, signal_callback)
        engine.start()  # starts background loop
        # ... engine runs autonomously ...
        engine.stop()

    For the web service integration, use:
        engine = TradingEngine(...)
        async with engine.lifespan():
            await engine.process_signal(signal)
    """

    def __init__(
        self,
        wallet: WalletManager,
        config: Optional[EngineConfig] = None,
        signal_callback: Optional[Callable[[], list[TradeSignal]]] = None,
    ) -> None:
        """
        Args:
            wallet: WalletManager for signing and CLOB auth.
            config: Engine configuration.
            signal_callback: Optional synchronous callback that returns
                new TradeSignals. Used as an alternative to direct
                ingestion from the analysis pipeline.
        """
        self._config = config or EngineConfig()

        # Build Polymarket clients
        self._clob = CLOBClient(wallet)
        self._data_api = DataAPIClient(wallet)

        # Build engine components
        self._order_manager = OrderManager(self._clob)
        self._position_tracker = PositionTracker()
        self._risk_engine = RiskEngine(self._config.risk)
        self._signal_ingestor = SignalIngestor()
        self._signal_callback = signal_callback

        # Feed verified order closures into risk accounting so the daily
        # trade limit and post-loss cooldown actually engage.
        self._order_manager.on_order_closed = self._on_order_closed

        # Register markets
        for icao, (cond_id, tok_id) in self._config.city_to_market_map.items():
            self._signal_ingestor.register_market(icao, cond_id, tok_id)

        # Background loop state
        self._running = False
        self._loop_task: Optional[Any] = None
        self._last_reconcile: float = 0.0
        # Last live cash balance fetched from the CLOB (None = not fetched
        # yet; portfolio estimates fall back to paper_base_usdc).
        self._cached_cash: Optional[float] = None
        self._stats: dict[str, Any] = {
            "signals_processed": 0,
            "orders_placed": 0,
            "orders_failed": 0,
            "trades_executed": 0,
            "started_at": None,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background trading loop via AsyncManager."""
        if self._running:
            logger.warning("TradingEngine already running")
            return
        if not self._config.enabled:
            logger.info("TradingEngine is disabled — not starting")
            return

        self._running = True
        mgr = get_async_manager()
        mgr.start()
        self._loop_task = asyncio.run_coroutine_threadsafe(
            self._run_loop(), mgr._loop or asyncio.get_event_loop()
        )
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("TradingEngine started")

    def stop(self) -> None:
        """Stop the trading engine."""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None
        logger.info("TradingEngine stopped")

    @property
    def running(self) -> bool:
        return self._running

    @property
    def config(self) -> EngineConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public API (called from FastAPI or collector)
    # ------------------------------------------------------------------

    def _on_order_closed(self, order: TrackedOrder) -> None:
        """Risk accounting for verified order closures.

        MATCHED fills open a position in the tracker (cost basis for
        exposure/P&L); settlement PnL itself is recorded later via
        :meth:`settle_matched_position`, which is the path that feeds
        real PnL into ``RiskEngine.record_trade`` and drives the loss
        cooldown. CANCELLED orders only advance the daily trade counter.
        """
        try:
            if order.state == OrderState.MATCHED and order.filled_size > 0:
                self._position_tracker.open_position(
                    condition_id=order.condition_id,
                    token_id=order.token_id,
                    side="YES" if order.side == "BUY" else "NO",
                    size=order.filled_size,
                    entry_price=order.avg_fill_price or order.price,
                    metadata={"local_id": order.local_id},
                )
                # Settlement unknown yet: count the trade without
                # fabricating PnL.
                self._risk_engine.record_trade(0.0)
            else:
                self._risk_engine.record_trade(0.0)
        except Exception as exc:
            logger.warning("Failed to record closed order %s: %s", order.local_id, exc)

    def settle_matched_position(self, token_id: str, payout_per_share: float) -> float:
        """Settle a filled position and record realized PnL for risk.

        Args:
            token_id: Outcome token of the filled position.
            payout_per_share: Settlement payout per share (1.0 win / 0.0 loss).

        Returns the realized PnL in USDC (0.0 if no such position).
        """
        pos = self._position_tracker.close_position(token_id)
        if pos is None:
            return 0.0
        if pos.side == "YES":
            realized = (payout_per_share - pos.avg_entry_price) * pos.size
        else:
            realized = (pos.avg_entry_price - payout_per_share) * pos.size
        self._risk_engine.record_trade(realized)
        logger.info(
            "Settled %s: payout=%.2f realized PnL=%.2f",
            token_id[:10], payout_per_share, realized,
        )
        return realized

    async def process_signal(self, signal: TradeSignal) -> Optional[TrackedOrder]:
        """Process a single trade signal: risk check -> place order.

        Returns the TrackedOrder if placed, None otherwise.
        This is the primary entry point for signal ingestion.
        """
        self._stats["signals_processed"] += 1
        logger.info(
            "Processing signal: %s %s conf=%.2f target=%.4f",
            signal.direction.value,
            signal.condition_id[:10],
            signal.confidence,
            signal.target_price,
        )

        # -- resolve token_id if not set --
        token_id = signal.token_id
        if not token_id:
            token_id = self._resolve_token_id(signal.condition_id)
        if not token_id:
            logger.warning(
                "Signal for condition %s has no token_id and no registered "
                "mapping — rejecting (refusing to place token-less order)",
                signal.condition_id[:10],
            )
            self._stats["orders_failed"] += 1
            return None

        # -- risk check --
        if self._config.trade_on_signals:
            risk = self._risk_engine.assess(
                signal_confidence=signal.confidence,
                position_size=signal.size or 100.0,
                open_orders=self._order_manager.get_open_orders(),
                total_portfolio_value=self._estimate_portfolio_value(),
                condition_exposure=self._condition_exposure(signal.condition_id),
            )
            if not risk.allowed:
                logger.info("Signal rejected by risk engine: %s", risk.reason)
                return None

        # -- place order --
        if signal.direction == SignalDirection.HOLD:
            return None

        side = "BUY" if signal.direction == SignalDirection.BUY else "SELL"
        size = signal.size or self._compute_position_size(signal)
        if size <= 0:
            logger.info(
                "Signal for %s sized to 0 by Kelly (no edge) — skipping order",
                signal.condition_id[:10],
            )
            return None

        order = await self._order_manager.place_order(
            condition_id=signal.condition_id,
            token_id=token_id,
            side=side,
            price=signal.target_price,
            size=size,
            metadata={
                "source": signal.source.value,
                "confidence": signal.confidence,
                "signal_timestamp": signal.timestamp.isoformat(),
            },
        )

        if order.state == OrderState.OPEN:
            self._stats["orders_placed"] += 1
        else:
            self._stats["orders_failed"] += 1

        return order

    async def process_observation(
        self, snapshot: WeatherObservationSnapshot
    ) -> list[TrackedOrder]:
        """Process a weather observation and place trades for any signals.

        This is the main integration point — called from the collector
        or analysis pipeline whenever new weather data arrives.
        """
        signals = self._signal_ingestor.ingest_observation(snapshot)
        orders: list[TrackedOrder] = []
        for sig in signals:
            if len(orders) >= self._config.max_orders_per_run:
                break
            order = await self.process_signal(sig)
            if order:
                orders.append(order)
        return orders

    async def process_analysis(
        self, analysis_result: dict[str, Any]
    ) -> list[TrackedOrder]:
        """Process a city analysis result and place trades."""
        signals = self._signal_ingestor.ingest_from_analysis(analysis_result)
        orders: list[TrackedOrder] = []
        for sig in signals:
            if len(orders) >= self._config.max_orders_per_run:
                break
            order = await self.process_signal(sig)
            if order:
                orders.append(order)
        return orders

    # ------------------------------------------------------------------
    # Status & stats
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return engine status for health/status endpoints."""
        return {
            "running": self._running,
            "enabled": self._config.enabled,
            "stats": {**self._stats},
            "orders": {
                "open": len(self._order_manager.get_open_orders()),
                "total": len(self._order_manager._orders),
            },
            "positions": {
                "count": self._position_tracker.position_count(),
                "exposure": self._position_tracker.get_total_exposure(),
                "unrealized_pnl": self._position_tracker.get_total_unrealized_pnl(),
            },
        }

    # ------------------------------------------------------------------
    # Internal background loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main background loop — polls for signals and reconciles."""
        logger.info("TradingEngine background loop started")

        while self._running:
            try:
                # 1. Fetch signals via callback (if registered)
                if self._signal_callback:
                    signals = self._signal_callback()
                    for sig in signals[: self._config.max_orders_per_run]:
                        await self.process_signal(sig)

                # 2. Periodic reconciliation
                now = asyncio.get_event_loop().time()
                if now - self._last_reconcile > self._config.reconcile_interval_seconds:
                    await self._order_manager.reconcile()
                    self._last_reconcile = now

                # 3. Sleep
                await asyncio.sleep(self._config.poll_interval_seconds)

            except asyncio.CancelledError:
                logger.info("TradingEngine loop cancelled")
                break
            except Exception:
                logger.exception("Error in trading engine loop")
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.info("TradingEngine background loop stopped")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_token_id(self, condition_id: str) -> str:
        """Resolve a token ID for a condition from registered mappings.

        Checks the engine config map first, then the signal ingestor's
        condition -> token registry. Returns "" when unmapped — callers
        must reject the signal rather than placing a token-less order.
        """
        for _icao, (cond_id, tok_id) in self._config.city_to_market_map.items():
            if cond_id == condition_id and tok_id:
                return tok_id
        return self._signal_ingestor.get_token_id(condition_id)

    def _compute_position_size(self, signal: TradeSignal) -> float:
        """Compute Quarter Kelly position size, capped by risk config.

        Uses the signal's model_probability metadata when present,
        falling back to confidence. Returns 0.0 when there is no edge.
        """
        from .kelly_sizing import compute_kelly_size_from_signal

        bankroll = self._estimate_portfolio_value()
        model_probability = 0.0
        try:
            model_probability = float(signal.metadata.get("model_probability", 0.0) or 0.0)
        except (TypeError, ValueError):
            model_probability = 0.0
        size = compute_kelly_size_from_signal(
            model_probability=model_probability,
            confidence=signal.confidence,
            direction=signal.direction.value,
            target_price=signal.target_price,
            bankroll=bankroll,
            max_position_size=self._config.risk.max_position_size_usdc,
        )
        return min(size, self._config.risk.max_position_size_usdc)

    async def refresh_portfolio_from_clob(self) -> float:
        """Refresh cached cash + positions from the CLOB (live trading).

        Returns the resulting portfolio value. Failures degrade
        gracefully to the last cached/paper estimate.
        """
        try:
            balance = await self._clob.get_balance()
            for key in ("balance", "cash", "usdc", "available"):
                try:
                    self._cached_cash = float(balance.get(key))  # type: ignore[union-attr]
                    break
                except (TypeError, ValueError, AttributeError):
                    continue
            positions = await self._clob.get_positions()
            items = positions.get("data", positions if isinstance(positions, list) else [])
            if isinstance(items, list):
                self._position_tracker.sync_from_clob_response(items)
        except Exception as exc:
            logger.warning("CLOB portfolio refresh failed, using cached estimate: %s", exc)
        return self._estimate_portfolio_value()

    def _estimate_portfolio_value(self) -> float:
        """Estimate total portfolio value (cash + open positions).

        Cash is the last CLOB balance from refresh_portfolio_from_clob(),
        falling back to the explicit paper-trading baseline
        (paper_base_usdc) — never a silent magic constant.
        """
        cash = self._cached_cash if self._cached_cash is not None else self._config.paper_base_usdc
        exposure = self._position_tracker.get_total_exposure()
        pnl = self._position_tracker.get_total_unrealized_pnl()
        return cash + exposure + pnl

    def _condition_exposure(self, condition_id: str) -> float:
        """Compute current exposure to a specific condition."""
        orders = self._order_manager.get_orders_by_condition(condition_id)
        positions = self._position_tracker.get_positions_by_condition(condition_id)
        order_exposure = sum(o.price * o.size for o in orders)
        pos_exposure = sum(p.avg_entry_price * p.size for p in positions)
        return order_exposure + pos_exposure
