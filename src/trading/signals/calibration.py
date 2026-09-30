"""Model calibration gate: trade on calibrated edge, not raw output.

The Gaussian ensemble probability is unvalidated — a raw 0.93 against a
0.50 market is a claim without evidence. This module bins historical
(model_p, market_p, outcome) rows and maps a fresh model probability to
its bin's observed hit rate. Bins with fewer than ``min_n`` resolved
outcomes carry no evidence: the gate skips instead of trading.

Pure functions, stdlib only. Binning mirrors
``model_vs_market_calibration`` (``scripts/backtest_real_polymarket.py``,
Sec 3.6): bin ``i`` covers ``[i/n, (i+1)/n)`` by model probability.
"""

from __future__ import annotations

from typing import Optional


def bin_index(p: float, n_bins: int = 5) -> int:
    """Bin index for a model probability (clamped to the last bin)."""
    return min(int(p * n_bins), n_bins - 1)


def build_table(rows: list[dict], n_bins: int = 5) -> list[dict]:
    """Aggregate rows into calibration bins.

    Rows carry ``model_probability``, ``market_price``, ``actual_outcome``
    (0/1). Returns ``[{bin_lo, bin_hi, n, outcomes, hit_rate}]`` sorted
    by bin; ``hit_rate`` is None when the bin has no resolved outcomes.
    """
    n = [0] * n_bins
    outcomes = [0] * n_bins
    hits = [0.0] * n_bins
    for row in rows:
        try:
            p = float(row["model_probability"])
            m = float(row["market_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < p < 1 and 0 < m < 1):
            continue
        idx = bin_index(p, n_bins)
        n[idx] += 1
        try:
            outcome = float(row.get("actual_outcome"))
        except (TypeError, ValueError):
            continue
        if outcome in (0.0, 1.0):
            outcomes[idx] += 1
            hits[idx] += outcome
    table = []
    for i in range(n_bins):
        table.append({
            "bin_lo": round(i / n_bins, 2),
            "bin_hi": round((i + 1) / n_bins, 2),
            "n": n[i],
            "outcomes": outcomes[i],
            "hit_rate": (hits[i] / outcomes[i]) if outcomes[i] else None,
        })
    return table


def lookup(table: Optional[dict], p: float) -> Optional[dict]:
    """Return the bin dict for ``p``, or None when unknown/empty."""
    if not table:
        return None
    try:
        n_bins = int(table.get("n_bins", len(table.get("bins", []))))
        bins = table["bins"]
        return bins[bin_index(float(p), n_bins)]
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def calibrated_gap(
    model_p: float,
    market_p: float,
    table: Optional[dict],
    min_n: int,
) -> Optional[dict]:
    """Calibrated edge for a (model, market) pair.

    Returns ``{"gap", "p", "n", "hit_rate", "calibrated"}`` where ``p``
    is the probability the feed should trade on, or None when the bin
    lacks evidence (``outcomes < min_n``) — the caller must skip.
    With no table configured the raw gap passes through uncalibrated:
    the gate only binds once evidence exists.
    """
    if not table:
        return {
            "gap": model_p - market_p,
            "p": model_p,
            "n": 0,
            "hit_rate": None,
            "calibrated": False,
        }
    cell = lookup(table, model_p)
    if cell is None:
        return None
    try:
        resolved = int(cell.get("outcomes", 0) or 0)
    except (TypeError, ValueError):
        return None
    if resolved < min_n:
        return None
    hit = cell.get("hit_rate")
    if hit is None:
        return None
    return {
        "gap": float(hit) - market_p,
        "p": float(hit),
        "n": resolved,
        "hit_rate": float(hit),
        "calibrated": True,
    }
