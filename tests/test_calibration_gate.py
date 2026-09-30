"""Calibration gate: trade on calibrated edge, not raw model output.

- calibration.build_table / lookup / calibrated_gap (pure).
- Feed: thin-evidence bins skip; calibrated edge trades with
  calibrated metadata; every evaluated city is prediction-logged.
- Settlement: resolved outcomes are logged for the offline join.
- scripts/build_calibration_table joins the log into a table file.
"""

import asyncio
import json

from src.trading.engine.trading_engine import EngineConfig, TradingEngine
from src.trading.signals import calibration


def _rows():
    # Bin [0.8, 1.0]: 4 outcomes, 3 hits -> hit_rate 0.75.
    rows = [
        {"model_probability": 0.85, "market_price": 0.5, "actual_outcome": 1},
        {"model_probability": 0.90, "market_price": 0.5, "actual_outcome": 1},
        {"model_probability": 0.95, "market_price": 0.5, "actual_outcome": 1},
        {"model_probability": 0.82, "market_price": 0.5, "actual_outcome": 0},
        {"model_probability": 0.30, "market_price": 0.5, "actual_outcome": 0},
    ]
    return rows


def test_build_table_hit_rate_per_bin():
    table = calibration.build_table(_rows(), n_bins=5)
    top = table[4]
    assert (top["bin_lo"], top["bin_hi"]) == (0.8, 1.0)
    assert top["n"] == 4 and top["outcomes"] == 4
    assert top["hit_rate"] == 0.75


def test_lookup_empty_table_returns_none():
    assert calibration.lookup({"n_bins": 5, "bins": []}, 0.9) is None
    assert calibration.lookup(None, 0.9) is None


def test_gap_falls_back_to_raw_without_table():
    out = calibration.calibrated_gap(0.93, 0.05, None, min_n=10)
    assert out is not None
    assert out["gap"] == 0.93 - 0.05
    assert out["calibrated"] is False


def test_gap_skips_thin_bin():
    table = {"n_bins": 5, "bins": calibration.build_table(_rows(), n_bins=5)}
    assert calibration.calibrated_gap(0.93, 0.05, table, min_n=10) is None


def test_gap_uses_hit_rate_with_evidence():
    table = {"n_bins": 5, "bins": calibration.build_table(_rows(), n_bins=5)}
    out = calibration.calibrated_gap(0.93, 0.05, table, min_n=4)
    assert out is not None
    assert out["gap"] == 0.75 - 0.05
    assert out["n"] == 4 and out["calibrated"] is True


def _table_file(tmp_path):
    table = {"n_bins": 5, "bins": calibration.build_table(_rows(), n_bins=5)}
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(table))
    return str(p)


def _engine():
    eng = TradingEngine(
        wallet=None,
        config=EngineConfig(
            enabled=True,
            paper_mode=True,
            city_to_market_map={"KLGA": ("c1", "t1")},
        ),
    )
    eng._cached_cash = 2000.0
    return eng


def test_feed_skips_thin_bin_and_logs_prediction(tmp_path, monkeypatch):
    import web.services.trading_api as tapi

    log = str(tmp_path / "pred.jsonl")
    monkeypatch.setenv("POLY_CALIBRATION_PATH", _table_file(tmp_path))
    monkeypatch.setenv("POLY_CALIBRATION_MIN_N", "10")
    monkeypatch.setenv("POLY_PREDICTION_LOG", log)
    eng = _engine()
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            probability_provider=lambda *a: 0.93,
            price_fetcher=lambda tok: 0.05,
        )
    )
    assert out["signals"] == 0 and out["orders"] == 0
    assert "KLGA" in out["skipped"]
    rows = [json.loads(line) for line in open(log)]
    assert len(rows) == 1 and rows[0]["type"] == "prediction"
    assert rows[0]["model_p"] == 0.93


def test_feed_trades_calibrated_edge(tmp_path, monkeypatch):
    import web.services.trading_api as tapi

    log = str(tmp_path / "pred.jsonl")
    monkeypatch.setenv("POLY_CALIBRATION_PATH", _table_file(tmp_path))
    monkeypatch.setenv("POLY_CALIBRATION_MIN_N", "4")
    monkeypatch.setenv("POLY_PREDICTION_LOG", log)
    eng = _engine()
    out = asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            probability_provider=lambda *a: 0.93,
            price_fetcher=lambda tok: 0.05,
        )
    )
    assert out["signals"] == 1 and out["orders"] == 1
    recs = eng._paper_store.get_open_positions()
    assert len(recs) == 1
    assert recs[0].metadata["calibrated"] is True
    assert recs[0].metadata["calibration_n"] == 4


def test_settlement_logs_outcome(tmp_path, monkeypatch):
    import web.services.trading_api as tapi

    log = str(tmp_path / "pred.jsonl")
    monkeypatch.setenv("POLY_PREDICTION_LOG", log)
    eng = _engine()
    asyncio.run(
        tapi.run_signal_feed_once(
            engine=eng,
            probability_provider=lambda *a: 0.70,
            price_fetcher=lambda tok: 0.05,
        )
    )
    assert len(eng._paper_store.get_open_positions()) == 1

    class _Won:
        async def get_markets(self, condition_ids=None):
            from src.trading.polymarket.gamma_client import GammaMarket

            return [
                GammaMarket(
                    condition_id="c1",
                    clob_token_ids=["t1", "t2"],
                    question="q",
                    description="",
                    volume=1.0,
                    liquidity=1.0,
                    active=False,
                    closed=True,
                    end_date_iso="",
                    neg_risk=False,
                    raw={"outcomePrices": ["1.0", "0.0"]},
                )
            ]

    settled = asyncio.run(
        tapi.check_and_settle_closed_markets(engine=eng, client=_Won())
    )
    assert len(settled) == 1
    rows = [json.loads(line) for line in open(log)]
    outcomes = [r for r in rows if r.get("type") == "outcome"]
    assert len(outcomes) == 1 and outcomes[0]["outcome"] == 1


def test_offline_builder_joins_log(tmp_path):
    from scripts.build_calibration_table import build_table_from_log

    log = tmp_path / "pred.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "prediction",
                        "condition_id": "c1",
                        "model_p": 0.9,
                        "market_p": 0.5,
                    }
                ),
                json.dumps(
                    {"type": "outcome", "condition_id": "c1", "outcome": 1}
                ),
                json.dumps(
                    {
                        "type": "prediction",
                        "condition_id": "c2",
                        "model_p": 0.9,
                        "market_p": 0.5,
                    }
                ),
            ]
        )
        + "\n"
    )
    table = build_table_from_log(str(log), n_bins=5)
    top = table["bins"][4]
    assert top["n"] == 1 and top["hit_rate"] == 1.0
