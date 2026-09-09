"""Tests for the real-Polymarket backtest join (offline, pure functions)."""

from __future__ import annotations

import asyncio
from typing import Any

from scripts.backtest_real_polymarket import (
    build_records_from_joined,
    compute_liquidity_from_poly_data,
    detect_whale_flow,
    discover_weather_markets_from_poly,
    extract_resolution,
    load_poly_markets,
    load_poly_trades,
    match_market_to_city,
    model_vs_market_calibration,
    pick_decision_price,
    pick_poly_data_price,
    resolve_poly_trade_token,
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


def _poly_csv(tmp_path, rows: str):
    path = tmp_path / "trades.csv"
    path.write_text(
        "market_id,timestamp,price,asset,maker,maker_direction,usd_amount,token_amount\n"
        + rows,
        encoding="utf-8",
    )
    return path


def test_load_poly_trades_filters_markets_and_skips_bad_rows(tmp_path):
    path = _poly_csv(
        tmp_path,
        "0xAAA,1000,0.4,tokA,0xmaker1,BUY,50,125\n"
        "0xAAA,2000,0.45,tokA,0xmaker1,BUY,30,60\n"
        "0xBBB,1500,0.6,tokB,0xmaker2,SELL,20,33\n"
        "0xAAA,bad-ts,0.5,tokA,0xmaker1,BUY,10,20\n"  # bad ts skipped
        "0xAAA,3000,9.99,tokA,0xmaker1,BUY,10,1\n"  # bad price skipped
        "0xAAA,4000,0.5,tokA,0xmaker1,BUY,not-a-number,10\n",  # bad usd -> 0
    )
    out = load_poly_trades(path, {"0xAAA"})
    assert set(out) == {"0xAAA"}
    assert [t["price"] for t in out["0xAAA"]] == [0.4, 0.45, 0.5]
    assert out["0xAAA"][-1]["usd_amount"] == 0.0
    # Missing file degrades to empty (CLOB/Data API fallback).
    assert load_poly_trades(tmp_path / "missing.csv") == {}


def test_pick_poly_data_price_respects_token_and_cutoff():
    trades = [
        {"timestamp": 1000, "price": 0.2, "asset": "tokA"},
        {"timestamp": 1000, "price": 0.8, "asset": "tokB"},
        {"timestamp": 2000, "price": 0.35, "asset": "tokA"},
        {"timestamp": 3000, "price": 0.9, "asset": "tokA"},  # after cutoff
    ]
    assert pick_poly_data_price(trades, "tokA", cutoff_ts=2500) == 0.35
    assert pick_poly_data_price(trades, "tokB", cutoff_ts=2500) == 0.8
    # Opposite side never substitutes when this token has no fills.
    assert pick_poly_data_price(trades[1:2], "tokA", cutoff_ts=2500) is None
    assert pick_poly_data_price(trades, "tokA", cutoff_ts=500) is None
    # Legacy rows without an asset column fall back to market order.
    legacy = [{"timestamp": 1000, "price": 0.5}, {"timestamp": 2000, "price": 0.6}]
    assert pick_poly_data_price(legacy, "tokA", cutoff_ts=2500) == 0.6


def test_compute_liquidity_uses_dataset_era_not_wallclock():
    trades = {
        "0xAAA": [
            {"timestamp": 1_000_000.0, "price": 0.4, "maker": "0xm1",
             "maker_direction": "BUY", "usd_amount": 100.0},
            {"timestamp": 1_000_000.0 + 8 * 86400, "price": 0.45, "maker": "0xm2",
             "maker_direction": "SELL", "usd_amount": 50.0},
        ],
    }
    out = compute_liquidity_from_poly_data(trades, lookback_days=7)
    # Reference = newest trade (2026-era), not wall-clock now: the old
    # $100 fill is outside the 7d window, the recent $50 counts.
    assert out["0xAAA"]["volume_usd"] == 50.0
    assert out["0xAAA"]["total_volume_usd"] == 150.0
    assert out["0xAAA"]["trade_count"] == 1


def test_detect_whale_flow_flags_concentration():
    trades = {
        "0xAAA": [
            {"timestamp": 1000.0, "price": 0.4, "maker": "0xwhale",
             "maker_direction": "BUY", "usd_amount": 800.0},
            {"timestamp": 1001.0, "price": 0.41, "maker": "0xsmall",
             "maker_direction": "BUY", "usd_amount": 200.0},
        ],
        "0xBBB": [
            {"timestamp": 1000.0, "price": 0.5, "maker": "",
             "maker_direction": "BUY", "usd_amount": 999.0},
        ],
    }
    out = detect_whale_flow(trades, concentration_threshold=0.3)
    assert out["0xAAA"]["alert"] is True
    assert out["0xAAA"]["top_maker"] == "0xwhale"
    assert out["0xAAA"]["top_fraction"] == 0.8
    assert out["0xAAA"]["side_dominance"] == 1.0
    # Maker-less rows carry no concentration signal.
    assert "0xBBB" not in out


def test_model_vs_market_calibration_bins_gaps_and_hit_rates():
    joined = [
        {"model_probability": 0.7, "market_price": 0.5, "actual_outcome": 1.0},
        {"model_probability": 0.75, "market_price": 0.55, "actual_outcome": 0.0},
        {"model_probability": 0.1, "market_price": 0.08, "actual_outcome": 0.0},
        {"model_probability": 0.72, "market_price": 0.6, "actual_outcome": None},
    ]
    table = model_vs_market_calibration(joined, n_bins=5)
    top = table[3]  # [0.6, 0.8)
    assert top["n"] == 3
    assert top["avg_gap"] > 0  # model persistently above market
    assert top["hit_rate"] == 0.5  # pending row excluded
    low = table[0]  # [0.0, 0.2)
    assert low["n"] == 1 and low["hit_rate"] == 0.0
    assert table[1]["n"] == 0 and table[1]["hit_rate"] is None


def test_load_poly_trades_parses_v2_iso_rows(tmp_path):
    # Real v2 header: ISO timestamp, taker, nonusdc_side, taker_direction.
    path = tmp_path / "trades.csv"
    path.write_text(
        "timestamp,market_id,maker,taker,nonusdc_side,maker_direction,"
        "taker_direction,price,usd_amount,token_amount,transactionHash\n"
        "2026-09-04T07:54:07.000000,0xAAA,0xmaker1,0xtaker1,token1,BUY,SELL,"
        "0.26,2.08,8.0,0xhash1\n"
        "2026-09-04T07:55:07.000000,0xAAA,0xmaker2,0xtaker2,token2,SELL,BUY,"
        "0.74,11.84,16.0,0xhash2\n",
        encoding="utf-8",
    )
    out = load_poly_trades(path, {"0xAAA"})
    assert set(out) == {"0xAAA"}
    assert len(out["0xAAA"]) == 2
    first = out["0xAAA"][0]
    assert first["timestamp"] > 1_700_000_000  # ISO parsed, not skipped
    assert first["nonusdc_side"] == "token1"
    assert first["taker"] == "0xtaker1"
    assert first["taker_direction"] == "SELL"
    assert first["transactionHash"] == "0xhash1"


def test_poly_side_resolution_via_markets_csv(tmp_path):
    markets = tmp_path / "markets.csv"
    markets.write_text(
        "id,clobTokenIds,question,market_slug\n"
        '0xAAA,"[""111"", ""222""]","Highest temperature in NYC?","nyc-high"\n',
        encoding="utf-8",
    )
    tokens = load_poly_markets(markets)
    assert tokens["0xAAA"]["token1"] == "111"
    assert tokens["0xAAA"]["token2"] == "222"
    assert "NYC" in tokens["0xAAA"]["question"]

    v2 = [
        {"timestamp": 1000.0, "price": 0.26, "nonusdc_side": "token1"},
        {"timestamp": 1000.0, "price": 0.74, "nonusdc_side": "token2"},
        {"timestamp": 2000.0, "price": 0.30, "nonusdc_side": "token1"},
    ]
    mt = tokens["0xAAA"]
    assert resolve_poly_trade_token(v2[0], mt) == "111"
    assert resolve_poly_trade_token(v2[1], mt) == "222"
    assert pick_poly_data_price(v2, "111", cutoff_ts=2500, market_tokens=mt) == 0.30
    assert pick_poly_data_price(v2, "222", cutoff_ts=2500, market_tokens=mt) == 0.74
    # token1-only fills never substitute for a token2 query.
    assert pick_poly_data_price(v2[0:1], "222", cutoff_ts=2500, market_tokens=mt) is None


def test_discover_weather_markets_ranks_and_filters():
    markets = {
        "0xAAA": {"question": "Highest temperature in NYC?", "slug": "nyc",
                  "token1": "111", "token2": "222"},
        "0xBBB": {"question": "Will BTC pump?", "slug": "btc",
                  "token1": "333", "token2": "444"},
        "0xCCC": {"question": "Lowest temperature in NYC?", "slug": "nyc-low",
                  "token1": "555", "token2": "666"},
    }
    liq = {
        "0xAAA": {"volume_usd": 500.0, "total_volume_usd": 5000.0},
        "0xCCC": {"volume_usd": 5.0, "total_volume_usd": 50.0},
    }
    out = discover_weather_markets_from_poly(
        markets, city="new york", min_volume_usd=100.0, liquidity=liq)
    assert [r["condition_id"] for r in out] == ["0xAAA"]
    assert out[0]["token1"] == "111"


def test_pendulum_book_vwap_and_snapshots():
    from src.trading.polymarket.pendulum_book import (
        asset_hex_to_token_id,
        book_rows_to_snapshots,
        fill_buy_asks,
        fok_fill_rates,
        normalize_asset_id,
        pendulum_export_sql,
    )
    # 32-byte big-endian blob -> decimal CTF id.
    assert asset_hex_to_token_id("0x" + "00" * 31 + "01") == "1"
    assert normalize_asset_id("0XABC") == normalize_asset_id("abc")
    # Decimal Gamma IDs normalise to the same hex key.
    assert normalize_asset_id("255") == "ff"

    asks = [(0.40, 100.0), (0.42, 100.0)]
    fill = fill_buy_asks(asks, 40.0)  # $40 @ 0.40 = 100 shares, one level
    assert fill["exhausted"] is False
    assert abs(fill["vwap"] - 0.40) < 1e-9  # type: ignore[operator]
    thin = fill_buy_asks([(0.5, 10.0)], 100.0)
    assert thin["exhausted"] is True and thin["shares"] == 10.0

    rows = [
        {"ts_ms": 1, "market": "AA", "asset": "0x1111",
         "bids": [{"price": 0.39, "size": 50}], "asks": [{"price": 0.41, "size": 60}]},
        {"ts_ms": 1, "market": "AA", "asset": "0x2222",
         "bids": [{"price": 0.58, "size": 40}], "asks": [{"price": 0.60, "size": 70}]},
    ]
    snaps = book_rows_to_snapshots(rows, "0x1111", "0x2222")
    assert len(snaps) == 1
    assert snaps[0]["yes"]["best_ask"] == 0.41
    assert snaps[0]["no"]["best_ask"] == 0.60
    assert snaps[0]["yes"]["asks"] and snaps[0]["no"]["asks"]
    rates = fok_fill_rates(snaps, [50.0, 100.0])
    assert rates[50.0] == 1.0 and rates[100.0] == 0.0

    sql = pendulum_export_sql("hour.parquet", "0xAABB")
    assert "event_type = 'book'" in sql and "unhex('AABB')" in sql


def test_backtester_prefers_book_vwap_over_proxy():
    from scripts.backtester.base import (
        BacktestConfig,
        BacktestRecord,
        SignalDirection,
        Strategy,
    )
    from scripts.backtester.engine import run_backtest

    class _AlwaysBuy(Strategy):
        def generate_signals(self, records, idx):
            return [SignalDirection.BUY]

    rec = BacktestRecord(
        city="new york", target_date="2026-09-01", model_probability=0.9,
        market_price=0.40, actual_outcome=1.0,
        metadata={"asks": [[0.40, 1000.0], [0.42, 1000.0]]},
    )
    res = run_backtest([rec], _AlwaysBuy(BacktestConfig(slippage_bps=0)))
    assert res.n_trades == 1
    assert res.trades[0].metadata["fill_source"] == "book_vwap"
    # $500 capped Kelly buy walks two ask levels: 1000 sh @ 0.40 ($400)
    # + $100 @ 0.42 (~238.1 sh) -> VWAP = 500 / total shares.
    assert abs(res.trades[0].entry_price - 500 / (1000 + 100 / 0.42)) < 1e-9
