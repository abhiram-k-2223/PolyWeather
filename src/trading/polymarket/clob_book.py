"""Public CLOB book reader (no wallet/auth required).

The feed prices YES tokens here — not on the gamma ``/price`` endpoint,
which 404s on every request live. ``GET {CLOB}/book?token_id=...``
returns ``{\"bids\": [{\"price\", \"size\"}], \"asks\": [...]}``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_CLOB_BOOK_URL = "https://clob.polymarket.com/book"


async def fetch_book_midpoint(
    token_id: str,
    *,
    http_get: Callable[..., Any] | None = None,
) -> Optional[float]:
    """Best bid/ask midpoint for a token, or None when unquotable.

    One-sided books fall back to the touch on the quoted side; empty
    books return None. ``http_get`` is injectable for tests (async,
    takes url + params, returns a response with
    .raise_for_status()/.json()). Defaults to the shared async client.
    """
    if http_get is None:
        from ...async_infra.http_client import get_shared_client

        shared = get_shared_client()

        async def http_get(url: str, params: dict[str, Any]) -> Any:  # type: ignore[no-redef]
            return await shared.get(url, params=params)

    resp = await http_get(_CLOB_BOOK_URL, {"token_id": token_id})
    resp.raise_for_status()
    book = resp.json() or {}

    def _best(side: Any) -> Optional[float]:
        if not isinstance(side, list) or not side:
            return None
        first = side[0]
        if isinstance(first, dict):
            raw = first.get("price")
        elif isinstance(first, (list, tuple)) and first:
            raw = first[0]
        else:
            raw = first
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return value if value == value and 0.0 < value < 1.0 else None

    bid = _best(book.get("bids"))
    ask = _best(book.get("asks"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return bid if bid is not None else ask
