"""CLOB public book midpoint (fixes dead gamma /price path)."""

import asyncio

from src.trading.polymarket.clob_book import fetch_book_midpoint


def _resp(payload):
    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return _R()


def _get(payload, seen):
    async def _fetch(url, params):
        seen.append((url, dict(params)))
        return _resp(payload)

    return _fetch


def test_book_midpoint_uses_best_bid_and_ask():
    seen = []
    payload = {
        "bids": [{"price": "0.04", "size": "100"}, {"price": "0.03", "size": "50"}],
        "asks": [{"price": "0.06", "size": "80"}, {"price": "0.07", "size": "60"}],
    }
    out = asyncio.run(fetch_book_midpoint("tok1", http_get=_get(payload, seen)))
    assert out == 0.05
    url, params = seen[0]
    assert url.endswith("/book") and params.get("token_id") == "tok1"


def test_book_midpoint_one_sided_falls_back_to_touch():
    seen = []
    out = asyncio.run(
        fetch_book_midpoint("t", http_get=_get({"bids": [], "asks": [{"price": "0.06", "size": "10"}]}, seen))
    )
    assert out == 0.06
    out = asyncio.run(
        fetch_book_midpoint("t", http_get=_get({"bids": [{"price": "0.04", "size": "10"}], "asks": []}, seen))
    )
    assert out == 0.04


def test_book_midpoint_empty_book_is_none():
    seen = []
    assert asyncio.run(fetch_book_midpoint("t", http_get=_get({}, seen))) is None
    assert (
        asyncio.run(fetch_book_midpoint("t", http_get=_get({"bids": [], "asks": []}, seen))) is None
    )
