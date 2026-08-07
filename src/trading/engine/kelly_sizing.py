"""Kelly criterion position sizing for binary prediction markets.

Provides Quarter Kelly position sizing — a conservative variant of the
Kelly criterion that uses 25% of the full Kelly fraction. Quarter Kelly
preserves ~85% of the optimal growth rate while capping drawdown to ~15%,
matching the existing ``RiskConfig.max_drawdown_pct = 0.15``.

Background
----------
The Kelly criterion computes the optimal fraction ``f*`` of bankroll to
wager on a binary outcome with known probabilities:

    f* = (p * b - q) / b

where:
    p = probability of winning
    q = 1 - p
    b = net odds received (profit / loss per unit wagered)

For a prediction market token at price ``m`` (0 <= m <= 1):

    If the token pays out at $1 on win and $0 on loss:
        b = (1 - m) / m
        f* = (p - m) / (1 - m)      [BUY side]

    Quarter Kelly = 0.25 * f*

References
----------
- Kelly, J. L. (1956). "A New Interpretation of Information Rate."
- Thorp, E. O. (2008). "The Kelly Criterion in Blackjack, Sports Betting,
  and the Stock Market."
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Minimum edge threshold to prevent false positives from floating point noise.
# Must be large enough to absorb FP accumulation: e.g., 0.3 + 1e-10 - 0.3 > 1e-10.
_EDGE_EPSILON = 1e-8


def compute_kelly_fraction(
    win_probability: float,
    token_price: float,
) -> float:
    """Compute the full Kelly fraction ``f*`` for a binary prediction market bet.

    Parameters
    ----------
    win_probability:
        Our estimated probability (0–1) that the token pays out $1.
        For a YES token, this is the probability the event happens.
        For a NO token, this is the probability the event does NOT happen.
    token_price:
        The price (0–1) of the outcome token being purchased.

    Returns
    -------
    The full Kelly fraction ``f*`` — the fraction of bankroll to wager.
    Returns 0.0 when there is no positive edge (i.e., no expected
    advantage over the market price).

    Notes
    -----
    Derived from the classic Kelly formula:
        f* = (p * b - q) / b
        b = (1 - m) / m
        f* = (p - m) / (1 - m)

    Edge is defined as ``p - m`` (our probability vs. market price).
    """
    if not 0 < token_price < 1:
        logger.warning(
            "token_price=%.4f outside (0,1) — treating as no edge", token_price
        )
        return 0.0

    if not 0 < win_probability < 1:
        logger.warning(
            "win_probability=%.4f outside (0,1) — treating as no edge",
            win_probability,
        )
        return 0.0

    edge = win_probability - token_price
    if edge <= _EDGE_EPSILON:
        return 0.0

    # f* = edge / payout_odds  where payout_odds = 1 - token_price
    odds = 1.0 - token_price
    f_star = edge / odds

    # Clamp to [0, 1] — never bet more than 100% of bankroll
    return max(0.0, min(1.0, f_star))


def compute_quarter_kelly(
    win_probability: float,
    token_price: float,
) -> float:
    """Compute the Quarter Kelly fraction = 0.25 * f*.

    This is the recommended position sizing for PolyWeather. Quarter Kelly
    cuts the aggressive full-Kelly stake to 25%, reducing volatility while
    preserving approximately 85% of the long-term growth rate. It provides
    a margin of safety against edge misspecification (our calibrated
    probability may be wrong) and limits drawdown to ~15%.

    See :func:`compute_kelly_fraction` for parameter definitions.
    """
    return 0.25 * compute_kelly_fraction(win_probability, token_price)


def compute_kelly_position_size(
    win_probability: float,
    token_price: float,
    bankroll: float,
    max_position_size: float,
) -> float:
    """Compute the capped Quarter Kelly position size in USDC.

    Parameters
    ----------
    win_probability:
        Our estimated probability (0–1) of the token paying out.
    token_price:
        The price (0–1) of the outcome token.
    bankroll:
        Total trading bankroll in USDC.
    max_position_size:
        Hard cap on per-market position size in USDC.

    Returns
    -------
    The position size in USDC, capped to ``max_position_size``.
    Returns 0.0 if no positive edge exists.

    Notes
    -----
    The hard cap ``max_position_size_usdc`` from ``RiskConfig`` is enforced
    *after* the Kelly computation — it serves as an absolute circuit breaker
    above the mathematically optimal size.
    """
    if bankroll <= 0:
        return 0.0

    qk = compute_quarter_kelly(win_probability, token_price)
    raw_size = qk * bankroll
    return min(raw_size, max_position_size)


def compute_kelly_size_from_signal(
    model_probability: float,
    confidence: float,
    direction: str,
    target_price: float,
    bankroll: float,
    max_position_size: float,
) -> float:
    """Convenience wrapper that computes Quarter Kelly position size
    from a ``TradeSignal``'s probability, direction, and target price.

    Parameters
    ----------
    model_probability:
        Raw model probability estimate (0–1), or 0 if unavailable.
    confidence:
        Signal confidence (0–1), used as fallback when model_probability
        is unavailable (0 or None).
    direction:
        ``"BUY"`` (buying YES) or ``"SELL"`` (buying NO / selling YES).
    target_price:
        Target execution price of the token being bought (0–1).
    bankroll:
        Total trading bankroll in USDC.
    max_position_size:
        Hard per-market cap in USDC.

    Returns
    -------
    Position size in USDC.
    """
    # Determine the winning probability for the token we're buying.
    if model_probability > 0:
        raw_p = model_probability
    else:
        raw_p = confidence

    # For SELL signals (BUY NO), our win probability is the complement.
    if direction.upper() == "SELL":
        win_p = 1.0 - raw_p
    else:
        win_p = raw_p

    return compute_kelly_position_size(
        win_probability=win_p,
        token_price=target_price,
        bankroll=bankroll,
        max_position_size=max_position_size,
    )
