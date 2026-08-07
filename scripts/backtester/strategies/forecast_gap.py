"""ForecastGapStrategy — the primary AI Bot strategy.

Compares the model's forecast probability to the observed market price.
When the gap exceeds the configurable threshold, a BUY signal is
generated. Optionally generates SELL signals when the market overprices
an outcome the model says is unlikely (two-sided mode).

This is the strategy that maps directly to the proven AI Bot approach
($63K from $27): buy when the forecast says 60 % and the market prices
it at 10 %, hold to settlement, repeat.
"""

from __future__ import annotations

from ..base import BacktestConfig, BacktestRecord, SignalDirection, Strategy


class ForecastGapStrategy(Strategy):
    """Buy when forecast probability exceeds market price by threshold.

    The core logic matches ``SignalIngestor.ingest_forecast_gap`` in
    ``src/trading/engine/signal_ingestion.py`` — this strategy backtests
    the exact same decision rule that runs in production.
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        *,
        edge_threshold: float | None = None,
        max_price_for_buy: float | None = None,
        two_sided: bool | None = None,
    ) -> None:
        super().__init__(config)
        cfg = self.config
        if edge_threshold is not None:
            cfg.edge_threshold = edge_threshold
        if max_price_for_buy is not None:
            cfg.max_price_for_buy = max_price_for_buy
        if two_sided is not None:
            cfg.two_sided = two_sided

    def generate_signals(
        self, records: list[BacktestRecord], idx: int
    ) -> list[SignalDirection]:
        rec = records[idx]
        gap = rec.model_probability - rec.market_price

        if gap > self.config.edge_threshold and rec.market_price <= self.config.max_price_for_buy:
            return [SignalDirection.BUY]

        if self.config.two_sided:
            if -gap > self.config.edge_threshold and rec.market_price >= self.config.min_price_for_sell:
                return [SignalDirection.SELL]

        return [SignalDirection.HOLD]


class ConservativeGapStrategy(ForecastGapStrategy):
    """More conservative variant — requires higher edge, cheaper entry.

    Uses:
        - 12 % edge threshold (vs 8 % default)
        - 5 % max price (vs 10 % default)
        - 0.125 Quarter Kelly (half the default 0.25)
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        super().__init__(
            config,
            edge_threshold=0.12,
            max_price_for_buy=0.05,
        )
        self.config.kelly_fraction = 0.125
