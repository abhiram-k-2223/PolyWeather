"""Tests for the real-Polymarket backtest join (offline, pure functions)."""

from __future__ import annotations

import asyncio
from typing import Any

from scripts.backtest_real_polymarket import (
    build_records_from_joined,
    extract_resolution,
    match_market_to_city,
    pick_decision_price,
)
from src.trading.polymarket.data_api_client import DataAPIClient
from src.trading.polymarket.gamma_client import GammaClient


def test_match_market_to_city():
    assert match_market_to_city("Will NYC high hit 90F?", "New York weather", "new york")
    assert match_market_to_city(
        "Will the highest temperature in New York City be 69°F or below?",
        "Highest temperature in NYC on September 7?",
        "new york",
    )
    assert not match_market_to_city("Will London rain?", "London weather", "new york")


def test_pick_decision_price_uses_last_before_cutoff():
    history = {
        "history": [
            {"t": 1000, "p": 0.2},
            {"t": 2000, "p": 0.35},
            {"t": 3000, "p": 0.9},
        ]
    }
    assert pick_decision_price(history, cutoff_ts=2500) == 0.35
    assert pick_decision_price(history, cutoff_ts=500) is None
    # ms timestamps are normalised.
    ms = {"history": [{"t": 2_000_000_000_000, "p": 0.4}]}
    assert pick_decision_price(ms, cutoff_ts=2_000_000_001) == 0.4


def test_extract_resolution_shapes():
    assert extract_resolution({"resolvedOutcome": "Yes"}) == 1.0
    assert extract_resolution({"resolvedOutcome": "No"}) == 0.0
    assert extract_resolution({"outcomePrices": ["1", "0"]}) == 1.0
    assert extract_resolution({"outcomePrices": ["0", "1"]}) == 0.0
    assert extract_resolution({"question": "open market"}) is None


def test_build_records_flags_real_not_synthetic():
    joined = [
        {
            "city": "New York",
            "target_date": "2026-06-01",
            "model_probability": 0.65,
            "market_price": 0.4,
            "actual_outcome": 1.0,
            "metadata": {"condition_id": "0xabc"},
        },
        # Invalid row (no edge data) is dropped.
        {"city": "New York", "target_date": "2026-06-02"},
    ]
    records = build_records_from_joined(joined)
    assert len(records) == 1
    rec = records[0]
    assert rec["metadata"]["real_market"] is True
    assert rec["metadata"]["synthetic_market"] is False
    assert rec["city"] == "new york"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeShared:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.payload: object = []

    async def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return _FakeResp(self.payload)


class _FakeLimiter:
    async def wait(self, _key):
        pass


def test_get_events_uses_tag_slug_filter():
    client = GammaClient.__new__(GammaClient)
    client._base_url = "https://gamma-api.polymarket.com"
    shared: Any = _FakeShared()
    client._shared = shared
    client._limiter = _FakeLimiter()  # type: ignore[assignment]
    asyncio.run(client.get_events(tag_slug="weather", limit=5))
    url, params = client._shared.calls[0]
    assert url.endswith("/events")
    assert params.get("tag_slug") == "weather"
    assert "tag" not in params
    # Legacy tag= maps to tag_slug (bare tag= is ignored server-side).
    asyncio.run(client.get_events(tag="weather", limit=5))
    _, params = client._shared.calls[1]
    assert params.get("tag_slug") == "weather"
    assert "tag" not in params


def test_price_history_uses_clob_endpoint():
    client = DataAPIClient.__new__(DataAPIClient)
    client._base_url = "https://data-api.polymarket.com"
    client._clob_url = "https://clob.polymarket.com"
    shared2: Any = _FakeShared()
    client._shared = shared2
    client._limiter = _FakeLimiter()  # type: ignore[assignment]
    client._shared.payload = {"history": [{"t": 1000, "p": 0.4}]}
    out = asyncio.run(
        client.get_market_price_history("tok-1", interval="1h", limit=200)
    )
    url, params = client._shared.calls[0]
    assert url == "https://clob.polymarket.com/prices-history"
    assert params == {"market": "tok-1", "interval": "1h"}
    assert out == {"history": [{"t": 1000, "p": 0.4}]}


def test_parse_market_f_range():
    from scripts.backtest_real_polymarket import parse_market_f_range

    assert parse_market_f_range(
        "Will the highest temperature in New York City be between 70-71°F on September 7?"
    ) == (70, 71)
    assert parse_market_f_range(
        "Will the highest temperature in New York City be 69°F or below on Sept 7?"
    ) == (-40, 69)
    assert parse_market_f_range(
        "Will the highest temperature in New York City be 92°F or higher on July 5?"
    ) == (92, 130)
    assert parse_market_f_range("Will it rain in London tomorrow?") is None


def test_model_prob_for_f_range_concentrates_on_predicted_bucket():
    from scripts.backtest_real_polymarket import model_prob_for_f_range

    # 25°C ≈ 77°F: the 76-77°F market should carry high mass, 90°F+ ~zero.
    assert model_prob_for_f_range(25.0, 0.8, 76, 77) > 0.3
    assert model_prob_for_f_range(25.0, 0.8, 90, 130) < 0.05
    # Full-range market is ~certain.
    assert model_prob_for_f_range(25.0, 0.8, -40, 130) > 0.99


def test_pick_decision_trade_price_filters_token_and_cutoff():
    from scripts.backtest_real_polymarket import pick_decision_trade_price

    trades = [
        {"asset": "tokA", "price": 0.2, "timestamp": 1000},
        {"asset": "tokB", "price": 0.9, "timestamp": 1000},
        {"asset": "tokA", "price": 0.35, "timestamp": 2000},
        {"asset": "tokA", "price": 0.99, "timestamp": 3000},  # after cutoff
    ]
    assert pick_decision_trade_price(trades, "tokA", cutoff_ts=2500) == 0.35
    assert pick_decision_trade_price(trades, "tokB", cutoff_ts=2500) == 0.9
    assert pick_decision_trade_price(trades, "tokA", cutoff_ts=500) is None
    assert pick_decision_trade_price([], "tokA", cutoff_ts=2500) is None


def test_extract_resolution_handles_string_outcome_prices():
    # Closed-market shape: outcomePrices serialized as a JSON string plus
    # umaResolutionStatus (no `resolved` key).
    assert (
        extract_resolution(
            {"outcomePrices": '["0", "1"]', "umaResolutionStatus": "resolved"}
        )
        == 0.0
    )
    assert (
        extract_resolution(
            {"outcomePrices": '["1", "0"]', "umaResolutionStatus": "resolved"}
        )
        == 1.0
    )
    assert extract_resolution({"outcomePrices": '["0.4", "0.6"]'}) is None


def test_get_market_trades_uses_condition_id():
    client = DataAPIClient.__new__(DataAPIClient)
    client._base_url = "https://data-api.polymarket.com"
    client._clob_url = "https://clob.polymarket.com"
    shared: Any = _FakeShared()
    client._shared = shared
    client._limiter = _FakeLimiter()  # type: ignore[assignment]
    client._shared.payload = [{"asset": "t", "price": 0.5, "timestamp": 1}]
    out = asyncio.run(client.get_market_trades("0xabc", limit=500))
    url, params = client._shared.calls[0]
    assert url == "https://data-api.polymarket.com/trades"
    assert params == {"market": "0xabc", "limit": 500}
    assert out == [{"asset": "t", "price": 0.5, "timestamp": 1}]


def test_is_high_temp_market_excludes_low_markets():
    from scripts.backtest_real_polymarket import is_high_temp_market

    assert is_high_temp_market("Will the highest temperature in NYC be 80°F or higher?")
    assert not is_high_temp_market("Will the lowest temperature in NYC be between 60-61°F?")
    assert not is_high_temp_market("")
