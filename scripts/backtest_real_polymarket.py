#!/usr/bin/env python3
"""Backtest DEB predictions against REAL historical Polymarket prices.

This is the non-circular counterpart to
``scripts/build_openmeteo_backtest_records.py`` (whose market is synthetic,
derived from the same forecast signal). Here the market side comes from
Polymarket itself:

- market discovery: Gamma API (``GammaClient``) — weather events/markets,
  including closed (settled) ones;
- price at decision time: CLOB ``/prices-history`` last price at or before
  ``decision_cutoff`` (default 24h before market end); for closed markets
  (whose CLOB series is purged) Data API ``/trades?market=<condition_id>``
  fallback — last trade for the outcome token before the cutoff;
- outcome: market resolution when available (``GammaMarket.raw`` resolution
  fields), otherwise ERA5 bucket fallback flagged in metadata.

DEB side is walk-forward only (no lookahead): for each city-day the
prediction uses strictly prior days' history, converted to a bucket
probability with the same Gaussian-CDF mapping as the synthetic builder
(Platt-calibrated probability when ``--calibrated-probs`` JSON is given).

Start narrow (problems.md): default ``--city "new york"`` where
Polymarket temperature liquidity/history is deepest.

Example:
    .venv/bin/python scripts/backtest_real_polymarket.py --city "new york" \\
        --history data/openmeteo_history.json --output data/real_backtest_records.json
    .venv/bin/python scripts/backtester/run.py --records data/real_backtest_records.json \\
        --strategy forecast_gap --output-json data/real_backtest_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis import deb_algorithm  # noqa: E402
from src.analysis.settlement_rounding import apply_city_settlement  # noqa: E402

DEFAULT_HISTORY = ROOT / "data" / "openmeteo_history.json"
DEFAULT_OUTPUT = ROOT / "data" / "real_backtest_records.json"
DEFAULT_POLY_TRADES = ROOT / "data" / "poly_trades.csv"
DEFAULT_POLY_MARKETS = ROOT / "data" / "poly_markets.csv"

BASE_SIGMA = 0.8

# Polymarket daily temperature markets use short names ("NYC") while our
# history keys use full names ("new york") — match on either.
CITY_ALIASES: dict[str, list[str]] = {
    "new york": ["new york", "nyc", "new york city"],
    "london": ["london"],
    "tokyo": ["tokyo"],
    "sydney": ["sydney"],
    "dubai": ["dubai"],
}


def _bucket_prob(mean: float, sigma: float, bucket: int) -> float:
    lo = (bucket - 0.5 - mean) / (sigma * math.sqrt(2))
    hi = (bucket + 0.5 - mean) / (sigma * math.sqrt(2))
    return 0.5 * (math.erf(hi) - math.erf(lo))


# ------------------------------------------------------------------
# poly_data integration (Section 3.1 / 3.4 of DATA_SOURCES_GUIDE)
# ------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float conversion for ragged CSV fields."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_poly_ts(value: Any) -> float:
    """Parse a poly_data v2 ``timestamp`` to unix seconds.

    Real ``processed/trades.csv`` carries ISO datetime strings
    (``2026-09-04T07:54:07.000000``, naive = UTC); synthetic extracts
    and tests use unix seconds (or ms). Returns 0.0 when unparseable
    so the caller skips the row.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        ts = float(value)
    else:
        text = str(value).strip()
        if not text:
            return 0.0
        try:
            ts = float(text)
        except (TypeError, ValueError):
            ts = 0.0
            iso = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
                try:
                    return dt.timestamp()
                except (OverflowError, OSError, ValueError):
                    return 0.0
            return 0.0
    if ts > 1e12:  # ms -> s
        ts /= 1000.0
    return ts if ts > 0 else 0.0


def load_poly_markets(csv_path: Path | str) -> dict[str, dict]:
    """Load poly_data ``data/markets.csv`` token mapping.

    Returns ``{condition_id: {token1, token2, question, slug}}`` where
    ``token1``/``token2`` are decimal CTF token IDs from ``clobTokenIds``
    (first element = token1, second = token2 per the poly_data README).
    Only the join-relevant columns are retained so the ~1.5M-row file
    stays lean. Missing file degrades to ``{}``.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return {}
    out: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cid = (row.get("id") or row.get("condition_id") or "").strip()
            if not cid:
                continue
            token1 = token2 = ""
            raw_tokens = row.get("clobTokenIds") or ""
            if raw_tokens:
                try:
                    parsed = json.loads(raw_tokens)
                except ValueError:
                    parsed = []
                if isinstance(parsed, list) and len(parsed) >= 2:
                    token1, token2 = str(parsed[0]), str(parsed[1])
            out[cid] = {
                "token1": token1,
                "token2": token2,
                "question": row.get("question") or "",
                "slug": row.get("market_slug") or row.get("slug") or "",
            }
    return out


def resolve_poly_trade_token(trade: dict, market_tokens: dict | None) -> str:
    """Resolve a poly_data trade to its decimal CTF token ID.

    v2 ``trades.csv`` has no per-row asset column — the side is
    ``nonusdc_side`` (``token1``/``token2``) resolved through
    ``markets.csv`` ``clobTokenIds``. Legacy extracts carrying
    ``asset``/``token_id`` return that directly.
    """
    asset = (trade.get("asset") or trade.get("token_id") or "").strip()
    if asset:
        return asset
    if market_tokens:
        side = (trade.get("nonusdc_side") or "").strip().lower()
        if side in ("token1", "token2"):
            return str(market_tokens.get(side) or "")
    return ""


def load_poly_trades(
    csv_path: Path | str,
    condition_ids: set[str] | None = None,
) -> dict[str, list[dict]]:
    """Load poly_data processed/trades.csv and index by market_id.

    Real v2 columns (verified against ``processed/trades.csv``):
    ``timestamp`` (ISO datetime), ``market_id``, ``maker``, ``taker``,
    ``nonusdc_side`` (token1/token2), ``maker_direction``/
    ``taker_direction`` (BUY/SELL), ``price``, ``usd_amount``,
    ``token_amount``, ``transactionHash``. There is NO per-row asset
    column — use :func:`resolve_poly_trade_token` with
    :func:`load_poly_markets` to map ``nonusdc_side`` to a token ID.

    Returns ``{market_id: [trade, ...]}`` where each trade has
    ``timestamp`` (float, unix seconds), ``price`` (float, 0-1),
    ``asset`` (str, only when the extract carries it),
    ``nonusdc_side`` (str), ``maker``/``taker`` (str),
    ``maker_direction``/``taker_direction`` (str), ``usd_amount``,
    ``token_amount`` (floats), ``transactionHash`` (str).

    When *condition_ids* is given, only trades for those markets are
    retained — always pass the discovered weather-market IDs so the
    full ~50M+ row file never lands in memory.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return {}

    by_market: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            mkt = (row.get("market_id") or "").strip()
            if not mkt:
                continue
            if condition_ids is not None and mkt not in condition_ids:
                continue
            ts = _parse_poly_ts(row.get("timestamp"))
            if ts <= 0:
                continue
            price = _safe_float(row.get("price"))
            if not (0 < price < 1):
                continue
            by_market[mkt].append({
                "timestamp": ts,
                "price": price,
                "asset": (row.get("asset") or row.get("token_id") or "").strip(),
                "nonusdc_side": (row.get("nonusdc_side") or "").strip(),
                "maker": (row.get("maker") or "").strip(),
                "taker": (row.get("taker") or "").strip(),
                "maker_direction": (row.get("maker_direction") or "").strip(),
                "taker_direction": (row.get("taker_direction") or "").strip(),
                "usd_amount": _safe_float(row.get("usd_amount")),
                "token_amount": _safe_float(row.get("token_amount")),
                "transactionHash": (row.get("transactionHash") or "").strip(),
            })
    # Sort each market's trades by timestamp ascending.
    for mkt in by_market:
        by_market[mkt].sort(key=lambda t: t["timestamp"])
    return dict(by_market)


def pick_poly_data_price(
    trades: list[dict],
    token_id: str,
    *,
    cutoff_ts: float,
    market_tokens: dict | None = None,
) -> float | None:
    """Return the last poly_data trade price at or before *cutoff_ts*.

    Side resolution order per trade: explicit ``asset``/``token_id``
    column first, else ``nonusdc_side`` mapped through *market_tokens*
    (``{token1, token2}`` from :func:`load_poly_markets`). Rows with no
    side info fall back to the most recent market trade (legacy
    extracts). A condition_id hosts both YES and NO fills — when side
    info exists but nothing matches *token_id*, return None rather
    than the opposite side's price.
    """
    wanted = str(token_id or "")
    resolved = [resolve_poly_trade_token(t, market_tokens) for t in trades]
    scoped = [t for t, tok in zip(trades, resolved) if tok in ("", wanted)]
    # If the CSV carries asset IDs but none match this token, there is
    # no usable price — do NOT fall back to the opposite side's fills.
    if wanted and trades and any(resolved) and not scoped:
        return None
    best: tuple[float, float] | None = None
    for trade in scoped:
        ts = trade["timestamp"]
        price = trade["price"]
        if ts <= cutoff_ts and (best is None or ts >= best[0]):
            best = (ts, price)
    return best[1] if best else None


def compute_liquidity_from_poly_data(
    all_trades: dict[str, list[dict]],
    *,
    lookback_days: int = 7,
    reference_ts: float | None = None,
) -> dict[str, dict]:
    """Compute per-market liquidity metrics from poly_data trades.

    Returns ``{market_id: {volume_usd, trade_count, unique_traders,
    avg_trade_size, total_volume_usd}}``. ``volume_usd`` covers the
    trailing *lookback_days* window ending at *reference_ts*; the
    window exists so live screening ignores stale history.
    ``total_volume_usd`` covers all loaded trades.

    *reference_ts* defaults to the newest trade in the dataset (NOT
    wall-clock now) so historical backtests screen against the era
    they actually ran in.

    This is the basis for Section 3.4 (weather market liquidity screening).
    """
    if reference_ts is None:
        reference_ts = max(
            (t["timestamp"] for trades in all_trades.values() for t in trades),
            default=0.0,
        )
    cutoff = reference_ts - lookback_days * 86400

    result: dict[str, dict] = {}
    for mkt, trades in all_trades.items():
        total_vol = sum(t["usd_amount"] for t in trades)
        recent = [t for t in trades if t["timestamp"] >= cutoff]
        if not recent:
            result[mkt] = {
                "volume_usd": 0.0,
                "trade_count": 0,
                "unique_traders": 0,
                "avg_trade_size": 0.0,
                "total_volume_usd": total_vol,
            }
            continue
        vol = sum(t["usd_amount"] for t in recent)
        traders = {t["maker"] for t in recent if t.get("maker")}
        result[mkt] = {
            "volume_usd": vol,
            "trade_count": len(recent),
            "unique_traders": len(traders),
            "avg_trade_size": vol / len(recent),
            "total_volume_usd": total_vol,
        }
    return result


def detect_whale_flow(
    all_trades: dict[str, list[dict]],
    *,
    concentration_threshold: float = 0.3,
) -> dict[str, dict]:
    """Detect whale/institutional flow concentration per market.

    Returns ``{market_id: {top_maker, top_fraction, side_dominance,
    alert}}``. ``side_dominance`` is the net BUY/SELL skew of the top
    trader (+1 = all buys, -1 = all sells). Rows without a maker
    address are excluded from concentration math.

    Implements Section 3.5 (Whale & Institutional Flow Detection).
    """
    result: dict[str, dict] = {}
    for mkt, trades in all_trades.items():
        if not trades:
            continue
        by_maker: dict[str, dict[str, float]] = defaultdict(lambda: {"BUY": 0.0, "SELL": 0.0})
        for t in trades:
            maker = (t.get("maker") or "").strip()
            if not maker:
                continue
            side = (t.get("maker_direction") or "").upper()
            if side in ("BUY", "SELL"):
                by_maker[maker][side] += t["usd_amount"]

        if not by_maker:
            continue
        total_vol = sum(s["BUY"] + s["SELL"] for s in by_maker.values())
        if total_vol <= 0:
            continue

        # Find the dominant maker.
        top_maker = max(by_maker, key=lambda m: by_maker[m]["BUY"] + by_maker[m]["SELL"])
        top_vol = by_maker[top_maker]["BUY"] + by_maker[top_maker]["SELL"]
        top_fraction = top_vol / total_vol

        buy_vol = by_maker[top_maker]["BUY"]
        sell_vol = by_maker[top_maker]["SELL"]
        side_dominance = (buy_vol - sell_vol) / top_vol if top_vol > 0 else 0.0

        result[mkt] = {
            "top_maker": top_maker,
            "top_fraction": round(top_fraction, 4),
            "side_dominance": round(side_dominance, 4),
            "alert": top_fraction >= concentration_threshold,
        }
    return result


def discover_weather_markets_from_poly(
    markets: dict[str, dict],
    *,
    city: str = "",
    min_volume_usd: float = 0.0,
    liquidity: dict[str, dict] | None = None,
    limit: int = 100,
) -> list[dict]:
    """Rank poly_data ``markets.csv`` rows for weather trading (Sec 3.4).

    Pure function: keeps markets whose question/slug mentions
    temperature/weather (and *city* aliases when given), joins optional
    per-market *liquidity* (``compute_liquidity_from_poly_data`` output),
    drops rows below *min_volume_usd* trailing volume, and returns at
    most *limit* rows sorted by total volume descending::

        [{condition_id, question, token1, token2, volume_usd,
          total_volume_usd}]

    Full-corpus Gamma discovery replacement: scan the ~1.5M-row
    ``markets.csv`` once instead of paging Gamma search per city.
    """
    keys = [k for k in CITY_ALIASES.get(city.lower(), [city.lower()]) if k] if city else []
    scored: list[dict] = []
    for cid, meta in markets.items():
        hay = f"{meta.get('question', '')} {meta.get('slug', '')}".lower()
        if "temperatur" not in hay and "weather" not in hay:
            continue
        if keys and not any(k in hay for k in keys):
            continue
        liq = (liquidity or {}).get(cid, {})
        vol = float(liq.get("volume_usd", 0.0) or 0.0)
        total = float(liq.get("total_volume_usd", 0.0) or 0.0)
        if min_volume_usd > 0 and vol < min_volume_usd:
            continue
        scored.append({
            "condition_id": cid,
            "question": meta.get("question", ""),
            "token1": meta.get("token1", ""),
            "token2": meta.get("token2", ""),
            "volume_usd": vol,
            "total_volume_usd": total,
        })
    scored.sort(key=lambda r: r["total_volume_usd"], reverse=True)
    return scored[:limit]


def match_market_to_city(market_question: str, market_title: str, city: str) -> bool:
    """Narrow city match: city name (or alias) in question or event title."""
    hay = f"{market_question} {market_title}".lower()
    keys = CITY_ALIASES.get(city.lower(), [city.lower()])
    return any(key in hay for key in keys)


def is_high_temp_market(question: str) -> bool:
    """Whether a market is about the daily HIGH temperature.

    Our history holds daily highs only — lowest-temperature markets are
    a different underlying and must not join the backtest.
    """
    return "highest temperature" in (question or "").lower()


def city_uses_fahrenheit(city: str) -> bool:
    """Whether the city's Polymarket temperature markets settle in °F."""
    try:
        from src.data_collection.city_registry import ALIASES, CITY_REGISTRY

        canonical = ALIASES.get(city.lower().strip(), city.lower().strip())
        meta = CITY_REGISTRY.get(canonical) or {}
        return bool(meta.get("use_fahrenheit"))
    except Exception:
        return False


# Matches "between 70-71°F", "69°F or below", "92°F or higher", etc.
_BETWEEN_RE = re.compile(
    r"between\s+(-?\d+)\s*[-–]\s*(-?\d+)\s*°?\s*f", re.IGNORECASE
)
_BELOW_RE = re.compile(
    r"(-?\d+)\s*°?\s*f\s*(or\s+below|or\s+lower|or\s+under)", re.IGNORECASE
)
_ABOVE_RE = re.compile(
    r"(-?\d+)\s*°?\s*f\s*(or\s+above|or\s+higher|or\s+hotter|or\s+over)",
    re.IGNORECASE,
)


def parse_market_f_range(question: str) -> tuple[int, int] | None:
    """Parse the °F bucket range a temperature market covers.

    Returns ``(lo, hi)`` inclusive integer °F buckets, or ``None`` when
    the question has no recognizable range. Open ends use wide bounds
    ((-40, hi) / (lo, 130)) — far outside plausible settled highs so
    the Gaussian tail mass is unaffected in practice.
    """
    text = question or ""
    match = _BETWEEN_RE.search(text)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
        return (min(lo, hi), max(lo, hi))
    match = _BELOW_RE.search(text)
    if match:
        return (-40, int(match.group(1)))
    match = _ABOVE_RE.search(text)
    if match:
        return (int(match.group(1)), 130)
    return None


def model_prob_for_f_range(
    prediction_c: float, sigma_c: float, lo_f: int, hi_f: int
) -> float:
    """P(settled °F high in [lo_f, hi_f]) under a Gaussian (°C params)."""
    pred_f = prediction_c * 9.0 / 5.0 + 32.0
    sigma_f = max(sigma_c * 9.0 / 5.0, 0.5)
    total = 0.0
    for bucket in range(lo_f, hi_f + 1):
        total += _bucket_prob(pred_f, sigma_f, bucket)
    return min(max(total, 1e-4), 1.0 - 1e-4)


def pick_decision_price(
    price_history: dict | list,
    *,
    cutoff_ts: float,
) -> float | None:
    """Return the last observed price at or before ``cutoff_ts``.

    Accepts the Data API shapes: ``{"history": [{"t": ts, "p": price}]}``,
    ``{"history": [{"timestamp": ..., "price": ...}]}``, or a bare list.
    """
    if isinstance(price_history, dict):
        points = price_history.get("history", price_history.get("data", []))
    else:
        points = price_history
    if not isinstance(points, list):
        return None
    best: tuple[float, float] | None = None
    for point in points:
        if not isinstance(point, dict):
            continue
        ts = point.get("t", point.get("timestamp", point.get("time")))
        price = point.get("p", point.get("price", point.get("value")))
        try:
            ts_f = float(ts)  # type: ignore[arg-type]
            p_f = float(price)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        # Normalise ms -> s timestamps from the Data API.
        if ts_f > 1e12:
            ts_f /= 1000.0
        if not math.isfinite(p_f) or not (0 < p_f < 1):
            continue
        if ts_f <= cutoff_ts and (best is None or ts_f >= best[0]):
            best = (ts_f, p_f)
    return best[1] if best else None


def pick_decision_trade_price(
    trades: list,
    token_id: str,
    *,
    cutoff_ts: float,
) -> float | None:
    """Return the last trade price for ``token_id`` at/before ``cutoff_ts``.

    Fallback for closed markets whose CLOB price history has been purged
    (``{"history": []}``). Data API trade shape: ``{"asset": token_id,
    "price": p, "timestamp": unix seconds}``. ``side`` is ignored — the
    execution price is the decision price either way.
    """
    best: tuple[float, float] | None = None
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        if str(trade.get("asset", "")) != str(token_id):
            continue
        try:
            ts_f = float(trade.get("timestamp"))  # type: ignore[arg-type]
            p_f = float(trade.get("price"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if ts_f > 1e12:
            ts_f /= 1000.0
        if not math.isfinite(p_f) or not (0 < p_f < 1):
            continue
        if ts_f <= cutoff_ts and (best is None or ts_f >= best[0]):
            best = (ts_f, p_f)
    return best[1] if best else None


def extract_resolution(raw: dict) -> float | None:
    """Extract a binary YES resolution (1/0) from a Gamma market payload."""
    if not isinstance(raw, dict):
        return None
    # Common Gamma resolution shapes.
    outcome = raw.get("resolvedOutcome") or raw.get("outcome")
    if isinstance(outcome, str):
        if outcome.lower() in ("yes", "true", "1"):
            return 1.0
        if outcome.lower() in ("no", "false", "0"):
            return 0.0
    prices = raw.get("outcomePrices")
    # Search results serialize this as a JSON string '["0", "1"]'.
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    if isinstance(prices, list) and len(prices) == 2:
        try:
            yes, no = float(prices[0]), float(prices[1])
            if yes == 1.0 and no == 0.0:
                return 1.0
            if yes == 0.0 and no == 1.0:
                return 0.0
        except (TypeError, ValueError):
            pass
    resolved = raw.get("resolved") is True or str(
        raw.get("umaResolutionStatus", "")
    ).lower() == "resolved"
    if resolved and raw.get("lastTradePrice") in (1, 1.0, "1"):
        return 1.0
    if resolved and raw.get("lastTradePrice") in (0, 0.0, "0"):
        return 0.0
    return None


def deb_walk_forward_predictions(history: dict, city: str) -> dict[str, float]:
    """Return {target_date: deb_prediction} using only prior days (no lookahead)."""
    original_load = deb_algorithm.load_history
    city_history = history.get(city, {})
    dates = sorted(city_history)
    out: dict[str, float] = {}
    try:
        for i, date in enumerate(dates):
            record = city_history[date]
            prior = {d: city_history[d] for d in dates[:i]}
            deb_algorithm.load_history = lambda _path, _prior=prior: _prior
            components = deb_algorithm.calculate_dynamic_weight_components(
                city, record["forecasts"]
            )
            prediction = components.get("prediction")
            if prediction is not None:
                out[date] = float(prediction)
    finally:
        deb_algorithm.load_history = original_load
    return out


def build_records_from_joined(
    joined: list[dict],
    *,
    calibrated: dict[str, float] | None = None,
) -> list[dict]:
    """Build backtester records from already-joined market+model rows.

    Each row: {city, target_date, model_probability, market_price,
    actual_outcome (or None), metadata}. Pure function — unit tested.
    """
    records: list[dict] = []
    for row in joined:
        try:
            model_p = float(row["model_probability"])
            market_p = float(row["market_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < model_p < 1 and 0 < market_p < 1):
            continue
        actual = row.get("actual_outcome")
        if actual is not None:
            actual = float(actual)
            if actual not in (0.0, 1.0):
                continue
        key = f"{row.get('city', '')}|{row.get('target_date', '')}"
        if calibrated and key in calibrated:
            try:
                cal = float(calibrated[key])
                if 0 < cal < 1:
                    model_p = cal
            except (TypeError, ValueError):
                pass
        records.append(
            {
                "city": str(row.get("city", "")).lower(),
                "target_date": str(row.get("target_date", "")),
                "model_probability": round(model_p, 4),
                "market_price": round(market_p, 4),
                "actual": actual,
                "metadata": {
                    **(row.get("metadata") or {}),
                    "real_market": True,
                    "synthetic_market": False,
                },
            }
        )
    return records


async def _fetch_real_joined(
    history: dict,
    *,
    city: str,
    decision_offset_hours: float,
    max_markets: int,
    poly_trades: dict[str, list[dict]] | None = None,
    poly_trades_path: Path | str | None = None,
    poly_markets_path: Path | str | None = None,
    market_tokens: dict[str, dict] | None = None,
    whale_alerts: dict[str, dict] | None = None,
    whale_threshold: float = 0.3,
    min_volume_usd: float = 0.0,
    liquidity_metrics: dict[str, dict] | None = None,
) -> list[dict]:
    from src.trading.polymarket.data_api_client import DataAPIClient
    from src.trading.polymarket.gamma_client import GammaClient
    from src.trading.polymarket.wallet import PolyWalletConfig, WalletManager

    predictions = deb_walk_forward_predictions(history, city)
    if not predictions:
        print(f"No DEB predictions for city {city!r}", file=sys.stderr)
        return []

    gamma = GammaClient()
    # Closed markets carry price history + resolution; active ones give
    # decision-time prices for the upcoming settlement.
    # NOTE: discovery goes through /events?tag_slug=weather — the
    # /markets endpoint silently ignores the tag filter and returns
    # unrelated markets.
    markets: list[tuple[str, Any]] = []  # (event_title, market)
    seen_conditions: set[str] = set()

    def _add_events(events: list[Any]) -> None:
        for event in events:
            for m in event.markets:
                if m.condition_id in seen_conditions:
                    continue
                seen_conditions.add(m.condition_id)
                markets.append((event.title, m))

    # Primary path: full-text search lands directly on the daily city
    # temperature markets (paging /events rarely does). Search serves 5
    # events per page and ranks newest-first, so keep walking pages past
    # out-of-window (recent/delisted) results until enough IN-WINDOW
    # markets are found. Plain-alias queries surface older daily markets
    # that the narrower "temperature ..." queries miss.
    in_window: list[tuple[str, Any]] = []  # (event_title, market)

    def _harvest(events: list[Any]) -> None:
        for event in events:
            for m in event.markets:
                if m.condition_id in seen_conditions:
                    continue
                seen_conditions.add(m.condition_id)
                markets.append((event.title, m))
                try:
                    target = datetime.fromisoformat(
                        m.end_date_iso.replace("Z", "+00:00")
                    ).date().isoformat()
                except (ValueError, AttributeError):
                    continue
                if target in predictions:
                    in_window.append((event.title, m))

    for alias in CITY_ALIASES.get(city.lower(), [city.lower()]):
        for query in (
            f"highest temperature {alias}",
            f"temperature {alias}",
            alias,
        ):
            for page in range(1, 25):
                try:
                    found = await gamma.search_events(query, page=page)
                except Exception as exc:
                    print(
                        f"Gamma search {query!r} p{page} failed: {exc}",
                        file=sys.stderr,
                    )
                    break
                if not found:
                    break
                before = len(markets)
                _harvest(found)
                if len(in_window) >= max_markets:
                    break
                if len(markets) == before and page >= 4:
                    break
            if len(in_window) >= max_markets:
                break
        if len(in_window) >= max_markets:
            break
    # Fallback path: page the weather tag (active + closed).
    if len(markets) < max_markets:
        for active, closed in ((False, True), (True, False)):
            try:
                _add_events(
                    await gamma.get_events(
                        tag_slug="weather",
                        active=active,
                        closed=closed,
                        limit=100,
                    )
                )
            except Exception as exc:  # graceful degradation
                print(f"Gamma fetch (closed={closed}) failed: {exc}", file=sys.stderr)
            if len(markets) >= max_markets:
                break
    # Prefilter to dates we can actually score (walk-forward predictions
    # exist) BEFORE truncating and BEFORE the expensive per-market
    # price-history fetch — discovery returns newest-first, so a naive
    # [:max_markets] keeps only out-of-window markets.
    dated: list[tuple[str, Any]] = []
    for event_title, market in markets:
        try:
            target = datetime.fromisoformat(
                market.end_date_iso.replace("Z", "+00:00")
            ).date().isoformat()
        except (ValueError, AttributeError):
            continue
        if target in predictions:
            dated.append((event_title, market))
    print(
        f"Discovered {len(seen_conditions)} markets, "
        f"{len(dated)} within history window",
        file=sys.stderr,
    )
    markets = dated[:max_markets]

    # --- poly_data: load AFTER discovery, filtered to the discovered
    # condition IDs, so the full multi-GB trades.csv never lands in
    # memory (Section 3.1). An explicit ``poly_trades`` dict (tests /
    # pre-filtered extracts) takes precedence over the path.
    # ``markets.csv`` token mapping resolves v2 nonusdc_side to CTF IDs.
    if market_tokens is None and poly_markets_path is not None:
        try:
            market_tokens = load_poly_markets(poly_markets_path)
        except Exception as exc:  # graceful degradation
            print(f"poly markets load failed: {exc}", file=sys.stderr)
            market_tokens = None
    if poly_trades is None and poly_trades_path is not None:
        wanted = {m.condition_id for _, m in markets if m.condition_id}
        if wanted:
            print(
                f"Loading poly_data trades for {len(wanted)} markets ...",
                file=sys.stderr,
            )
            poly_trades = load_poly_trades(poly_trades_path, wanted)
            print(
                f"Loaded {sum(len(v) for v in poly_trades.values())} trades "
                f"across {len(poly_trades)} markets",
                file=sys.stderr,
            )
    if poly_trades and liquidity_metrics is None and min_volume_usd > 0:
        liquidity_metrics = compute_liquidity_from_poly_data(poly_trades)
    if poly_trades and whale_alerts is None:
        whale_alerts = detect_whale_flow(
            poly_trades, concentration_threshold=whale_threshold,
        )
        n_whale = sum(1 for v in whale_alerts.values() if v.get("alert"))
        if n_whale:
            print(f"Whale concentration alerts: {n_whale} markets", file=sys.stderr)

    # DataAPIClient needs a wallet only for base URLs; use a placeholder
    # address config — price history endpoints are public, no signing.
    wallet = WalletManager(
        PolyWalletConfig(private_key="0x" + "00" * 32),
    )
    data_api = DataAPIClient(wallet)

    joined: list[dict] = []
    stats = {
        "city_mismatch": 0,
        "no_tokens": 0,
        "no_price": 0,
        "wrong_market_type": 0,
    }
    for event_title, market in markets:
        if not match_market_to_city(market.question, event_title, city):
            stats["city_mismatch"] += 1
            continue
        # Our history holds daily HIGH temperatures only — lowest-temp
        # (and other) markets are a different underlying entirely.
        if not is_high_temp_market(market.question):
            stats["wrong_market_type"] += 1
            continue
        if not market.clob_token_ids:
            stats["no_tokens"] += 1
            continue
        token_id = market.clob_token_ids[0]
        try:
            end_dt = datetime.fromisoformat(
                market.end_date_iso.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            continue
        target_date = end_dt.date().isoformat()
        if target_date not in predictions:
            continue
        cutoff = (
            end_dt - timedelta(hours=decision_offset_hours)
        ).timestamp()

        # --- Liquidity screening (Section 3.4) ---
        if liquidity_metrics and min_volume_usd > 0:
            lm = liquidity_metrics.get(market.condition_id, {})
            if lm.get("volume_usd", 0) < min_volume_usd:
                stats["low_liquidity"] = stats.get("low_liquidity", 0) + 1
                continue

        # --- Whale flow alert (Section 3.5) ---
        whale_info = None
        if whale_alerts:
            whale_info = whale_alerts.get(market.condition_id)

        # --- Price resolution: poly_data -> CLOB -> Data API trades ---
        market_price: float | None = None
        price_source = ""

        # 1) poly_data: fastest, covers closed markets, no CLOB purging.
        if poly_trades and market.condition_id in poly_trades:
            market_price = pick_poly_data_price(
                poly_trades[market.condition_id], token_id, cutoff_ts=cutoff,
                market_tokens=(market_tokens or {}).get(market.condition_id),
            )
            if market_price is not None:
                price_source = "poly_data_trades"

        # 2) CLOB /prices-history (online, active markets).
        if market_price is None:
            price_history: dict | list | None = None
            last_exc: Exception | None = None
            for _ in range(3):
                try:
                    price_history = await data_api.get_market_price_history(
                        token_id, interval="1h", limit=200
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
            if price_history is not None:
                market_price = pick_decision_price(price_history, cutoff_ts=cutoff)
                if market_price is not None:
                    price_source = "clob_history"
            if market_price is None and price_history is None:
                print(
                    f"Price history failed for {market.condition_id[:10]}: {last_exc}",
                    file=sys.stderr,
                )

        # 3) Data API /trades fallback (closed-market purged series).
        if market_price is None:
            try:
                api_trades = await data_api.get_market_trades(
                    market.condition_id, limit=500
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Trades fetch failed for {market.condition_id[:10]}: {exc}",
                    file=sys.stderr,
                )
                api_trades = []
            market_price = pick_decision_trade_price(
                api_trades, token_id, cutoff_ts=cutoff
            )
            if market_price is not None:
                price_source = "data_api_trades"

        if market_price is None:
            stats["no_price"] += 1
            continue

        # Outcome: market resolution first, ERA5 bucket fallback flagged.
        # US temperature markets settle in °F while history is °C, so the
        # fallback compares buckets in the market's unit.
        f_range = parse_market_f_range(market.question)
        use_f = city_uses_fahrenheit(city) and f_range is not None
        actual: float | None = extract_resolution(market.raw)
        outcome_source = "polymarket_resolution"
        if actual is None:
            city_history = history.get(city, {})
            record = city_history.get(target_date)
            if not record:
                continue
            if record.get("actual_high") is None:
                # No settlement yet (active market or ERA5 lag):
                # keep the record with a real decision price and let
                # the backtester treat it as unsettled.
                outcome_source = "pending"
            elif use_f:
                assert f_range is not None
                actual_f = float(record["actual_high"]) * 9.0 / 5.0 + 32.0
                actual_bucket = wu_round_f(actual_f)
                actual = (
                    1.0
                    if actual_bucket is not None
                    and f_range[0] <= actual_bucket <= f_range[1]
                    else 0.0
                )
                outcome_source = "era5_fallback_f"
            else:
                market_bucket = apply_city_settlement(city, predictions[target_date])
                actual_bucket = apply_city_settlement(city, record["actual_high"])
                if market_bucket is None or actual_bucket is None:
                    continue
                # NOTE: fallback compares DEB-implied bucket vs ERA5 truth.
                # The *price* is still real; only the outcome is fallback.
                actual = 1.0 if actual_bucket == market_bucket else 0.0
                outcome_source = "era5_fallback"

        if use_f:
            assert f_range is not None
            model_probability = _model_bucket_prob_f(
                history, city, target_date, predictions, f_range
            )
        else:
            model_probability = _model_bucket_prob(
                history, city, target_date, predictions
            )
        joined.append(
            {
                "city": city,
                "target_date": target_date,
                "model_probability": model_probability,
                "market_price": market_price,
                "actual_outcome": actual,
                "metadata": {
                    "condition_id": market.condition_id,
                    "token_id": token_id,
                    "question": market.question,
                    "outcome_source": outcome_source,
                    "price_source": price_source,
                    # Only flag markets where concentration actually trips
                    # the threshold — otherwise every market with any
                    # maker history would carry noise metadata (Sec 3.5).
                    **(
                        {"whale_alert": whale_info}
                        if whale_info and whale_info.get("alert")
                        else {}
                    ),
                },
            }
        )
    print(f"Join stats: {stats} -> {len(joined)} joined", file=sys.stderr)
    return joined


def wu_round_f(value_f: float | None) -> int | None:
    """Round-half-up a °F value to an integer settlement bucket."""
    import math as _math

    if value_f is None:
        return None
    x = float(value_f)
    return int(_math.floor(x + 0.5)) if x >= 0 else int(_math.ceil(x - 0.5))


def _model_bucket_prob(
    history: dict, city: str, target_date: str, predictions: dict[str, float]
) -> float:
    record = history[city][target_date]
    values = [v for v in record["forecasts"].values() if v is not None]
    disagreement = (max(values) - min(values)) if len(values) >= 2 else 0.0
    sigma = max(BASE_SIGMA, disagreement / 2.0)
    market_bucket = apply_city_settlement(city, sum(values) / len(values))
    if market_bucket is None:
        return 0.5
    return _bucket_prob(predictions[target_date], sigma, market_bucket)


def _sigma_c_for_day(history: dict, city: str, target_date: str) -> float:
    """Shared spread: model disagreement widened by BASE_SIGMA (in °C)."""
    record = history[city][target_date]
    values = [v for v in record["forecasts"].values() if v is not None]
    disagreement = (max(values) - min(values)) if len(values) >= 2 else 0.0
    return max(BASE_SIGMA, disagreement / 2.0)


def model_vs_market_calibration(
    joined: list[dict],
    *,
    n_bins: int = 5,
) -> list[dict]:
    """Bin joined rows by model probability vs market price (Sec 3.6).

    Pure function: for each row compares DEB ``model_probability``
    against the market ``market_price`` and aggregates hit-rate /
    avg outcome per model-probability bin. A persistently positive
    ``avg_gap`` (model above market) with ``hit_rate`` above the bin
    centre means the market underprices the DEB signal — tradeable
    edge; the reverse means DEB is overconfident.

    Returns ``[{bin_lo, bin_hi, n, avg_model_p, avg_market_p, avg_gap,
    hit_rate}]`` sorted by bin. Rows without a 0/1 outcome are
    counted in ``n`` but excluded from ``hit_rate``.
    """
    bins: list[dict] = [
        {
            "bin_lo": round(i / n_bins, 2),
            "bin_hi": round((i + 1) / n_bins, 2),
            "n": 0,
            "sum_model": 0.0,
            "sum_market": 0.0,
            "sum_gap": 0.0,
            "outcomes": 0,
            "hits": 0.0,
        }
        for i in range(n_bins)
    ]
    for row in joined:
        try:
            model_p = float(row["model_probability"])
            market_p = float(row["market_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < model_p < 1 and 0 < market_p < 1):
            continue
        idx = min(int(model_p * n_bins), n_bins - 1)
        cell = bins[idx]
        cell["n"] += 1
        cell["sum_model"] += model_p
        cell["sum_market"] += market_p
        cell["sum_gap"] += model_p - market_p
        actual = row.get("actual_outcome")
        try:
            outcome = float(actual) if actual is not None else None
        except (TypeError, ValueError):
            outcome = None
        if outcome in (0.0, 1.0):
            cell["outcomes"] += 1
            cell["hits"] += outcome
    table: list[dict] = []
    for cell in bins:
        n = cell["n"]
        table.append({
            "bin_lo": cell["bin_lo"],
            "bin_hi": cell["bin_hi"],
            "n": n,
            "avg_model_p": round(cell["sum_model"] / n, 4) if n else 0.0,
            "avg_market_p": round(cell["sum_market"] / n, 4) if n else 0.0,
            "avg_gap": round(cell["sum_gap"] / n, 4) if n else 0.0,
            "hit_rate": round(cell["hits"] / cell["outcomes"], 4)
            if cell["outcomes"]
            else None,
        })
    return table


def _model_bucket_prob_f(
    history: dict,
    city: str,
    target_date: str,
    predictions: dict[str, float],
    f_range: tuple[int, int],
) -> float:
    """P(settled °F high in ``f_range``) for a °F-settling market."""
    return model_prob_for_f_range(
        predictions[target_date],
        _sigma_c_for_day(history, city, target_date),
        f_range[0],
        f_range[1],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--city", default="new york")
    parser.add_argument("--decision-offset-hours", type=float, default=24.0)
    parser.add_argument("--max-markets", type=int, default=100)
    parser.add_argument("--calibrated-probs", default=None,
                        help="Optional JSON {city|date: prob} from Platt calibration")
    parser.add_argument(
        "--poly-trades", default=str(DEFAULT_POLY_TRADES),
        help="Path to poly_data processed/trades.csv (primary price source, "
             "covers closed markets without CLOB purging)",
    )
    parser.add_argument(
        "--poly-markets", default=str(DEFAULT_POLY_MARKETS),
        help="Path to poly_data data/markets.csv (maps v2 nonusdc_side "
             "token1/token2 to CTF token IDs for side-correct prices)",
    )
    parser.add_argument(
        "--min-volume-usd", type=float, default=0.0,
        help="Minimum trailing-7d USD volume to include a market (liquidity screening)",
    )
    parser.add_argument(
        "--whale-threshold", type=float, default=0.3,
        help="Maker concentration threshold for whale alerts (0-1)",
    )
    args = parser.parse_args()

    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    calibrated = None
    if args.calibrated_probs:
        calibrated = json.loads(Path(args.calibrated_probs).read_text(encoding="utf-8"))

    # --- poly_data (Section 3.1): pass the PATH through and let
    # _fetch_real_joined load it AFTER discovery, filtered to the
    # discovered condition IDs. Preloading the full ~50M-row file here
    # would OOM; an explicit small extract can still be passed via the
    # poly_trades parameter (tests / pre-filtered CSVs).
    poly_path = Path(args.poly_trades)
    poly_trades_path: Path | None = poly_path if poly_path.exists() else None
    if poly_trades_path is None:
        print(
            f"poly_trades not found at {poly_path} — falling back to CLOB/Data API",
            file=sys.stderr,
        )
    markets_path = Path(args.poly_markets)
    poly_markets_path: Path | None = markets_path if markets_path.exists() else None
    if poly_markets_path is None:
        print(
            f"poly_markets not found at {markets_path} — v2 side mapping disabled",
            file=sys.stderr,
        )

    joined = asyncio.run(
        _fetch_real_joined(
            history,
            city=args.city.lower(),
            decision_offset_hours=args.decision_offset_hours,
            max_markets=args.max_markets,
            poly_trades_path=poly_trades_path,
            poly_markets_path=poly_markets_path,
            whale_threshold=args.whale_threshold,
            min_volume_usd=args.min_volume_usd,
        )
    )
    records = build_records_from_joined(joined, calibrated=calibrated)
    Path(args.output).write_text(
        json.dumps({"records": records, "source": "real_polymarket"}, indent=2),
        encoding="utf-8",
    )
    n_real = sum(1 for r in records if r["metadata"].get("real_market"))
    print(f"Wrote {len(records)} records ({n_real} real-market) to {args.output}")
    if not records:
        print("No records joined — check city scope, market discovery, and history overlap.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
