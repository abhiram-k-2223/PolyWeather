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
import json
import math
import re
import sys
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
        price_history: dict | list | None = None
        last_exc: Exception | None = None
        for _ in range(3):  # CLOB is intermittently slow — retry
            try:
                price_history = await data_api.get_market_price_history(
                    token_id, interval="1h", limit=200
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if price_history is None:
            print(f"Price history failed for {market.condition_id[:10]}: {last_exc}",
                  file=sys.stderr)
            stats["no_price"] += 1
            continue
        market_price = pick_decision_price(price_history, cutoff_ts=cutoff)
        price_source = "clob_history"
        if market_price is None:
            # Closed markets get their CLOB series purged — fall back
            # to individual trades (Data API, keyed by condition ID).
            try:
                trades = await data_api.get_market_trades(
                    market.condition_id, limit=500
                )
            except Exception as exc:  # noqa: BLE001
                print(f"Trades fetch failed for {market.condition_id[:10]}: {exc}",
                      file=sys.stderr)
                trades = []
            market_price = pick_decision_trade_price(
                trades, token_id, cutoff_ts=cutoff
            )
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
    args = parser.parse_args()

    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    calibrated = None
    if args.calibrated_probs:
        calibrated = json.loads(Path(args.calibrated_probs).read_text(encoding="utf-8"))

    joined = asyncio.run(
        _fetch_real_joined(
            history,
            city=args.city.lower(),
            decision_offset_hours=args.decision_offset_hours,
            max_markets=args.max_markets,
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
