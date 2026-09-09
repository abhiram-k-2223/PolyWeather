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


def compute_slippage_fraction(
    size_usdc: float,
    slippage_bps: float,
    orderbook_depth_usd: float = 0.0,
) -> float:
    """Fractional price worsening for a market order (pure function).

    Section 3.2 (Pendulum Flow): linear fallback charges
    ``slippage_bps`` per $100 of size; when ``orderbook_depth_usd``
    is known a square-root market-impact model is used instead::

        linear:  slip = (bps / 1e4) * (size / 100)
        sqrt:    slip = (bps / 1e4) * sqrt(size / depth)

    Returns 0.0 when slippage is disabled (bps <= 0) or size <= 0.
    """
    if slippage_bps <= 0 or size_usdc <= 0:
        return 0.0
    rate = slippage_bps / 10_000.0
    if orderbook_depth_usd and orderbook_depth_usd > 0:
        import math

        return rate * math.sqrt(size_usdc / orderbook_depth_usd)
    return rate * (size_usdc / 100.0)


def apply_slippage_to_price(
    market_price: float,
    size_usdc: float,
    slippage_bps: float,
    orderbook_depth_usd: float = 0.0,
) -> float:
    """Worsen ``market_price`` for a BUY fill (pure function).

    The buyer pays up: ``effective = price * (1 + slip)``, clamped
    below 1.0 so fills stay in valid probability space. SELL/short
    fills use the same adverse direction (fewer tokens per USDC).
    """
    slip = compute_slippage_fraction(size_usdc, slippage_bps, orderbook_depth_usd)
    if slip <= 0:
        return market_price
    return min(market_price * (1.0 + slip), 0.99)


def apply_book_slippage(
    market_price: float,
    size_usdc: float,
    asks: object,
) -> tuple[float, str]:
    """VWAP fill against a real Pendulum ``book`` ask ladder (pure).

    Returns ``(effective_price, fill_source)`` where source is
    ``book_vwap`` when the ladder fills (fully or partially) and
    ``book_empty`` when the ladder carries no depth (caller falls back
    to the bps proxy). Exhausted books are conservative: the VWAP over
    resting depth is used (partial fill aborts are the caller's policy
    — see ``fill_buy_asks``). Clamped below 1.0.
    """
    try:
        from src.trading.polymarket.pendulum_book import fill_buy_asks
    except ImportError:
        return market_price, "book_empty"
    fill = fill_buy_asks(asks, size_usdc)
    vwap = fill.get("vwap")
    if vwap is None:
        return market_price, "book_empty"
    try:
        vwap_f = float(vwap)
    except (TypeError, ValueError):
        return market_price, "book_empty"
    if not 0 < vwap_f < 1:
        return market_price, "book_empty"
    return min(vwap_f, 0.99), "book_vwap"


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

                # Section 3.2: realistic fills. When the record carries a
                # real Pendulum ask ladder (metadata["asks"]) the backtest
                # pays the book VWAP; otherwise the bps proxy applies.
                # No asks + 0 bps (defaults) = legacy point-price fills,
                # so existing results are unchanged.
                book_asks = rec.metadata.get("asks") if rec.metadata else None
                fill_source = "bps_proxy"
                if book_asks:
                    entry_price, fill_source = apply_book_slippage(
                        rec.market_price, size_usdc, book_asks,
                    )
                    if fill_source == "book_empty":
                        entry_price = apply_slippage_to_price(
                            rec.market_price,
                            size_usdc,
                            cfg.slippage_bps,
                            cfg.orderbook_depth_usd,
                        )
                else:
                    entry_price = apply_slippage_to_price(
                        rec.market_price,
                        size_usdc,
                        cfg.slippage_bps,
                        cfg.orderbook_depth_usd,
                    )
                slip_bps_charged = (
                    (entry_price / rec.market_price - 1.0) * 10_000.0
                    if rec.market_price > 0
                    else 0.0
                )
                size_tokens = size_usdc / entry_price
                entry_fee = size_usdc * cfg.maker_fee
                total_fees += entry_fee
                bankroll -= size_usdc + entry_fee
                open_positions[city] = {
                    "size_usdc": size_usdc,
                    "size_tokens": size_tokens,
                    "entry_price": entry_price,
                }

                trade = TradeRecord(
                    direction=sig_dir,
                    entry_price=entry_price,
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
                    metadata={"slippage_bps_charged": round(slip_bps_charged, 2),
                              "fill_source": fill_source},
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
