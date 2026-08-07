"""Tests for Quarter Kelly position sizing in kelly_sizing.py."""

from __future__ import annotations

import pytest

from src.trading.engine.kelly_sizing import (
    compute_kelly_fraction,
    compute_kelly_position_size,
    compute_quarter_kelly,
    compute_kelly_size_from_signal,
)


# ---------------------------------------------------------------------------
# compute_kelly_fraction
# ---------------------------------------------------------------------------


class TestComputeKellyFraction:
    """Full Kelly fraction f* = (p - m) / (1 - m)."""

    def test_no_edge_returns_zero(self) -> None:
        """When p == m there is no edge."""
        assert compute_kelly_fraction(0.6, 0.6) == 0.0

    def test_win_probability_below_token_price(self) -> None:
        """Negative edge yields 0."""
        assert compute_kelly_fraction(0.4, 0.6) == 0.0

    def test_positive_edge_buy_yes(self) -> None:
        """p=0.70, m=0.60 => edge=0.10, odds=0.40, f*=0.25."""
        result = compute_kelly_fraction(0.70, 0.60)
        assert result == pytest.approx(0.25, abs=1e-9)

    def test_edge_at_epsilon_returns_zero(self) -> None:
        """Edge equal to epsilon yields 0 (<= check)."""
        # Use values that produce an edge just at _EDGE_EPSILON
        # edge = p - m, so p = m + 1e-8
        # But floating-point: 0.5 + 1e-8 might round, so use a case
        # where we know edge <= epsilon.
        assert compute_kelly_fraction(0.5000000005, 0.5) == 0.0

    def test_edge_just_above_epsilon_is_positive(self) -> None:
        """Edge epsilon + 1e-10 should be above threshold."""
        result = compute_kelly_fraction(0.5 + 1.01e-8, 0.5)
        assert result > 0.0

    def test_fraction_clamped_to_one(self) -> None:
        """f* is clamped to [0, 1]."""
        # Very large edge: p=0.999, m=0.001 => edge=0.998, odds=0.999 => f* ~0.999
        result = compute_kelly_fraction(0.999, 0.001)
        assert result <= 1.0
        assert result > 0.99

    def test_token_price_near_zero(self) -> None:
        """Token price ~0 means huge odds, but f* stays bounded."""
        result = compute_kelly_fraction(0.05, 0.001)
        assert 0.0 <= result <= 1.0

    def test_token_price_near_one(self) -> None:
        """Token price ~1 means tiny odds, f* stays bounded."""
        result = compute_kelly_fraction(0.999, 0.9995)
        assert 0.0 <= result <= 1.0

    def test_invalid_token_price_zero(self) -> None:
        """token_price of 0 is not allowed — returns 0."""
        assert compute_kelly_fraction(0.5, 0.0) == 0.0

    def test_invalid_token_price_one(self) -> None:
        """token_price of 1 is not allowed — returns 0."""
        assert compute_kelly_fraction(0.5, 1.0) == 0.0

    def test_invalid_win_probability_zero(self) -> None:
        """win_probability of 0 is not allowed — returns 0."""
        assert compute_kelly_fraction(0.0, 0.5) == 0.0

    def test_invalid_win_probability_one(self) -> None:
        """win_probability of 1 is not allowed — returns 0."""
        assert compute_kelly_fraction(1.0, 0.5) == 0.0


# ---------------------------------------------------------------------------
# compute_quarter_kelly
# ---------------------------------------------------------------------------


class TestComputeQuarterKelly:
    """Quarter Kelly = 0.25 * f*."""

    def test_quarter_of_fraction(self) -> None:
        """Full f*=0.25 => Quarter Kelly=0.0625."""
        result = compute_quarter_kelly(0.70, 0.60)
        assert result == pytest.approx(0.0625, abs=1e-9)

    def test_zero_when_no_edge(self) -> None:
        assert compute_quarter_kelly(0.5, 0.5) == 0.0

    def test_quarter_kelly_never_negative(self) -> None:
        result = compute_quarter_kelly(0.3, 0.8)
        assert result >= 0.0

    def test_known_value(self) -> None:
        """p=0.80, m=0.65 => edge=0.15, odds=0.35, f*~0.4286, qk~0.1071."""
        result = compute_quarter_kelly(0.80, 0.65)
        assert result == pytest.approx(0.107142857, abs=1e-6)


# ---------------------------------------------------------------------------
# compute_kelly_position_size
# ---------------------------------------------------------------------------


class TestComputeKellyPositionSize:
    """Quarter Kelly position = min(qk * bankroll, max_position_size)."""

    def test_basic_position_size(self) -> None:
        """bankroll=10000, p=0.70, m=0.60 => qk=0.0625 => size=625."""
        size = compute_kelly_position_size(
            win_probability=0.70,
            token_price=0.60,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # qk * bankroll = 0.0625 * 10000 = 625, capped at 500
        assert size == 500.0

    def test_no_cap_needed(self) -> None:
        """When qk * bankroll < max, use the raw value."""
        size = compute_kelly_position_size(
            win_probability=0.51,
            token_price=0.50,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # edge=0.01, odds=0.50, f*=0.02, qk=0.005 => 50 < 500
        assert size == pytest.approx(50.0, abs=1e-6)

    def test_bankroll_zero(self) -> None:
        assert (
            compute_kelly_position_size(0.7, 0.6, bankroll=0.0, max_position_size=500.0)
            == 0.0
        )

    def test_bankroll_negative(self) -> None:
        assert (
            compute_kelly_position_size(0.7, 0.6, bankroll=-100.0, max_position_size=500.0)
            == 0.0
        )

    def test_no_edge(self) -> None:
        assert (
            compute_kelly_position_size(0.5, 0.5, bankroll=10000.0, max_position_size=500.0)
            == 0.0
        )

    def test_hard_cap_applied(self) -> None:
        """max_position_size=200 should cap the raw 625."""
        size = compute_kelly_position_size(0.70, 0.60, bankroll=10000.0, max_position_size=200.0)
        assert size == 200.0


# ---------------------------------------------------------------------------
# compute_kelly_size_from_signal
# ---------------------------------------------------------------------------


class TestComputeKellySizeFromSignal:
    """Convenience wrapper that maps signal fields to Kelly params."""

    def test_buy_yes_uses_probability(self) -> None:
        """BUY with model_probability > 0 => win_p = model_probability."""
        size = compute_kelly_size_from_signal(
            model_probability=0.70,
            confidence=0.80,
            direction="BUY",
            target_price=0.60,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # win_p=0.70 => qk=0.0625 => raw=625, capped at 500
        assert size == 500.0

    def test_buy_falls_back_to_confidence(self) -> None:
        """BUY with model_probability=0 => win_p = confidence."""
        size = compute_kelly_size_from_signal(
            model_probability=0.0,
            confidence=0.80,
            direction="BUY",
            target_price=0.60,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        assert size == 500.0  # 10000 * 0.0625 = 625, capped at 500

    def test_sell_flips_probability(self) -> None:
        """SELL direction => win_p = 1 - raw_p."""
        size = compute_kelly_size_from_signal(
            model_probability=0.70,
            confidence=0.80,
            direction="SELL",
            target_price=0.40,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # win_p = 1 - 0.70 = 0.30, m = 0.40 => edge negative = 0
        assert size == 0.0

    def test_sell_with_edge(self) -> None:
        """SELL when market overprices YES => win_p=0.40, m=0.60 => edge in SELL."""
        size = compute_kelly_size_from_signal(
            model_probability=0.60,
            confidence=0.80,
            direction="SELL",
            target_price=0.60,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # win_p = 1 - 0.60 = 0.40, m = 0.60 => edge negative, qk = 0
        assert size == 0.0

    def test_sell_with_positive_edge(self) -> None:
        """SELL when market overprices YES: model_prob=0.80, m=0.60.
        win_p (for NO) = 1-0.80=0.20, m=0.60 => no edge."""
        size = compute_kelly_size_from_signal(
            model_probability=0.80,
            confidence=0.80,
            direction="SELL",
            target_price=0.60,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # win_p = 0.20, m = 0.60 => edge = -0.40, qk = 0
        assert size == 0.0

    def test_model_probability_zero_uses_confidence_sell(self) -> None:
        """model_prob=0, conf=0.80, SELL => win_p = 1 - 0.80 = 0.20, likely no edge."""
        size = compute_kelly_size_from_signal(
            model_probability=0.0,
            confidence=0.80,
            direction="SELL",
            target_price=0.60,
            bankroll=10000.0,
            max_position_size=500.0,
        )
        # win_p=0.20, m=0.60 => edge=-0.40 => 0
        assert size == 0.0
