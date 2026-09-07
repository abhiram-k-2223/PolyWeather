"""Platt scaling for binary probability calibration.

Maps raw model probabilities (e.g. the Gaussian-CDF bucket probability in
``scripts/build_openmeteo_backtest_records.py``) to calibrated probabilities
via a logistic fit on historical (model_p, outcome) pairs:

    p_cal = 1 / (1 + exp(-(a * logit(p_raw) + b)))

Fitting minimizes negative log-likelihood with Newton-Raphson on the two
logit-space parameters. Pure stdlib — no sklearn dependency.

Typical use::

    params = fit_platt(probs, outcomes)
    calibrated = [apply_platt(p, params) for p in probs]

A CLI (``scripts/fit_platt_calibration.py``) fits params from backtest
records and emits the ``{city|date: prob}`` JSON consumed by
``scripts/backtest_real_polymarket.py --calibrated-probs``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PlattParams:
    """Fitted Platt scaling parameters (logit space)."""

    a: float
    b: float
    n: int
    brier_before: float | None = None
    brier_after: float | None = None

    def to_dict(self) -> dict:
        return {
            "a": self.a,
            "b": self.b,
            "n": self.n,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "PlattParams":
        return cls(
            a=float(raw.get("a", 1.0)),
            b=float(raw.get("b", 0.0)),
            n=int(raw.get("n", 0)),
            brier_before=raw.get("brier_before"),
            brier_after=raw.get("brier_after"),
        )


_EPS = 1e-6


def _logit(p: float) -> float:
    p = min(max(float(p), _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def apply_platt(p_raw: float, params: PlattParams) -> float:
    """Apply fitted Platt scaling to a raw probability."""
    try:
        z = params.a * _logit(float(p_raw)) + params.b
    except (TypeError, ValueError):
        return float(p_raw)
    return _sigmoid(z)


def brier_score(probs: list[float], outcomes: list[float]) -> float | None:
    """Mean squared error between probabilities and binary outcomes."""
    pairs = [
        (float(p), float(y))
        for p, y in zip(probs, outcomes)
        if p is not None and y is not None
    ]
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def fit_platt(
    probs: list[float],
    outcomes: list[float],
    *,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> PlattParams:
    """Fit Platt scaling params on historical (prob, outcome) pairs.

    Uses Platt's target correction (positives -> (N+1)/(N+2)) to avoid
    overfitting on small samples, then Newton-Raphson on (a, b).
    Returns identity params (a=1, b=0) when fewer than 10 valid pairs.
    """
    pairs: list[tuple[float, float]] = []
    for p, y in zip(probs, outcomes):
        try:
            pf, yf = float(p), float(y)
        except (TypeError, ValueError):
            continue
        if not (0 < pf < 1) or yf not in (0.0, 1.0):
            continue
        pairs.append((pf, yf))
    n = len(pairs)
    before = brier_score([p for p, _ in pairs], [y for _, y in pairs])
    if n < 10:
        return PlattParams(a=1.0, b=0.0, n=n, brier_before=before, brier_after=before)

    # Logit-space features with Platt target smoothing.
    n_pos = sum(1 for _, y in pairs if y == 1.0)
    n_neg = n - n_pos
    t_pos = (n_pos + 1.0) / (n_pos + 2.0) if n_pos else 1.0
    t_neg = 1.0 / (n_neg + 2.0) if n_neg else 0.0
    xs = [_logit(p) for p, _ in pairs]
    ts = [t_pos if y == 1.0 else t_neg for _, y in pairs]

    a, b = 1.0, 0.0
    for _ in range(max_iter):
        grad_a = grad_b = h_aa = h_ab = h_bb = 0.0
        for x, t in zip(xs, ts):
            f = _sigmoid(a * x + b)
            d = f - t
            w = max(f * (1.0 - f), 1e-12)
            grad_a += d * x
            grad_b += d
            h_aa += w * x * x
            h_ab += w * x
            h_bb += w
        # 2x2 Newton step with damping for stability.
        det = h_aa * h_bb - h_ab * h_ab + 1e-9
        step_a = (h_bb * grad_a - h_ab * grad_b) / det
        step_b = (h_aa * grad_b - h_ab * grad_a) / det
        a -= step_a
        b -= step_b
        if abs(step_a) < tol and abs(step_b) < tol:
            break

    after = brier_score(
        [_sigmoid(a * x + b) for x in xs], [y for _, y in pairs]
    )
    # Guard: never ship a fit that hurts in-sample Brier — fall back.
    if after is not None and before is not None and after > before + 1e-9:
        return PlattParams(a=1.0, b=0.0, n=n, brier_before=before, brier_after=before)
    return PlattParams(a=a, b=b, n=n, brier_before=before, brier_after=after)
