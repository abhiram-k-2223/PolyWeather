"""Pendulum Flow V3 orderbook helpers (pure functions, no heavy deps).

Real V3 schema (verified against ``2026-09-09T06.parquet`` — the old
``ts/token_id/side/price/size`` layout described in early drafts does
NOT exist):

- ``best_bid_ask``: ``timestamp, asset_id, best_bid, best_ask`` (touch)
- ``book``: ``timestamp, market, asset_id, bids[], asks[]`` where
  ``market`` (BLOB) is shared by the YES/NO pair, ``asset_id`` (BLOB)
  is the 32-byte big-endian uint256 CTF token ID, and ``bids``/``asks``
  are ``STRUCT(price, size)[]`` (up to ~99 levels/side, may be empty).
- ``last_trade_price``: ``timestamp, price, size, side`` (prints).
- ``new_market`` / ``market_resolved``: ``question, slug, outcomes,
  assets_ids`` (pairing metadata).

Hour files are ~900MB — never bulk-download. Filter to one market in
DuckDB (see :func:`pendulum_export_sql`) and convert the exported rows
with :func:`book_rows_to_snapshots`. Fills use :func:`fill_buy_asks`
(VWAP walk); see DATA_SOURCES_GUIDE sections 3.2/3.3.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_asset_id(asset: Any) -> str:
    """Normalise a CTF asset id to lowercase hex without ``0x``.

    Accepts hex (any case, ``0x``-optional) or decimal token IDs so
    Gamma decimal IDs and Pendulum hex blobs compare equal.
    """
    text = str(asset or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return ""
    if all(c in "0123456789" for c in text):
        try:
            return format(int(text, 10), "x")
        except ValueError:
            return text
    return text.lstrip("0") or "0"


def asset_hex_to_token_id(asset_hex: str) -> str:
    """Convert a Pendulum ``asset_id`` blob hex to a decimal CTF token ID.

    The blob is the 32-byte big-endian uint256 (``BigInt(hex)``).
    """
    text = str(asset_hex or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        raise ValueError("empty asset hex")
    return str(int(text, 16))


def parse_book_levels(levels: Any) -> list[tuple[float, float]]:
    """Normalise a bids/asks ladder to ``[(price, size)]``.

    Accepts DuckDB ``STRUCT(price, size)[]`` dicts, ``(price, size)``
    tuples/lists, or ``{"p":..,"s":..}`` shapes. Drops invalid levels
    (price outside (0, 1), size <= 0).
    """
    out: list[tuple[float, float]] = []
    if not isinstance(levels, (list, tuple)):
        return out
    for lvl in levels:
        price = size = None
        if isinstance(lvl, dict):
            price = lvl.get("price", lvl.get("p"))
            size = lvl.get("size", lvl.get("s", lvl.get("shares")))
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            price, size = lvl[0], lvl[1]
        try:
            p = float(price)  # type: ignore[arg-type]
            s = float(size)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if 0 < p < 1 and s > 0:
            out.append((p, s))
    return out


def fill_buy_asks(
    asks: list[tuple[float, float]] | Any,
    size_usdc: float,
) -> dict:
    """Walk an ask ladder with a ``$size_usdc`` market BUY (pure).

    Returns ``{vwap, shares, cost, exhausted, levels_used}``. ``vwap``
    is None when no shares fill. When the book is thinner than the
    order, ``exhausted`` is True and vwap covers only resting depth
    (caller decides: abort partial or rest on worst level).
    """
    levels = parse_book_levels(asks) if not (
        asks and isinstance(asks[0] if isinstance(asks, list) else None, tuple)
        and len(asks[0]) == 2
    ) else list(asks)  # type: ignore[union-attr]
    levels = sorted(levels, key=lambda lvl: lvl[0])
    if size_usdc <= 0 or not levels:
        return {"vwap": None, "shares": 0.0, "cost": 0.0,
                "exhausted": True, "levels_used": 0}
    remaining = float(size_usdc)
    shares = cost = 0.0
    used = 0
    for price, size in levels:
        if remaining <= 1e-9:
            break
        take_shares = min(size, remaining / price)
        if take_shares <= 0:
            continue
        shares += take_shares
        cost += take_shares * price
        remaining -= take_shares * price
        used += 1
    exhausted = remaining > 1e-9
    return {
        "vwap": (cost / shares) if shares > 0 else None,
        "shares": shares,
        "cost": cost,
        "exhausted": exhausted,
        "levels_used": used,
    }


def fill_sell_bids(
    bids: list[tuple[float, float]] | Any,
    size_shares: float,
) -> dict:
    """Walk a bid ladder selling ``size_shares`` (pure).

    Returns ``{vwap, proceeds, shares, exhausted, levels_used}`` —
    mirror of :func:`fill_buy_asks` for exits into the bid side.
    """
    levels = parse_book_levels(bids) if not (
        bids and isinstance(bids[0] if isinstance(bids, list) else None, tuple)
        and len(bids[0]) == 2
    ) else list(bids)  # type: ignore[union-attr]
    levels = sorted(levels, key=lambda lvl: -lvl[0])
    if size_shares <= 0 or not levels:
        return {"vwap": None, "proceeds": 0.0, "shares": 0.0,
                "exhausted": True, "levels_used": 0}
    remaining = float(size_shares)
    proceeds = filled = 0.0
    used = 0
    for price, size in levels:
        if remaining <= 1e-9:
            break
        take = min(size, remaining)
        proceeds += take * price
        filled += take
        remaining -= take
        used += 1
    exhausted = remaining > 1e-9
    return {
        "vwap": (proceeds / filled) if filled > 0 else None,
        "proceeds": proceeds,
        "shares": filled,
        "exhausted": exhausted,
        "levels_used": used,
    }


def fok_fillable(levels: Any, size_shares: float) -> bool:
    """Whether a FOK order for ``size_shares`` fills entirely at rest."""
    if size_shares <= 0:
        return False
    return sum(s for _, s in parse_book_levels(levels)) >= size_shares


def fok_fill_rates(snapshots: list[dict], sizes: list[float]) -> dict[float, float]:
    """Fraction of snapshots whose BOTH ask ladders hold >= size (pure)."""
    rates: dict[float, float] = {}
    n = len(snapshots) or 1
    for size in sizes:
        ok = 0
        for snap in snapshots:
            yes = snap.get("yes", {}).get("asks", [])
            no = snap.get("no", {}).get("asks", [])
            if fok_fillable(yes, size) and fok_fillable(no, size):
                ok += 1
        rates[size] = ok / n if snapshots else 0.0
    return rates


def book_top_depth_usd(levels: Any, n: int = 1) -> float:
    """USD depth resting in the top-``n`` ladder levels (pure)."""
    return sum(p * s for p, s in parse_book_levels(levels)[:n])


def book_rows_to_snapshots(
    rows: list[dict],
    yes_asset: Any,
    no_asset: Any,
    *,
    max_levels: int = 10,
) -> list[dict]:
    """Convert exported DuckDB ``book`` rows to paired snapshots (pure).

    *rows* are ``{ts_ms, market, asset, bids, asks}`` dicts (see
    :func:`pendulum_export_sql`). YES/NO rows sharing ``(ts_ms,
    market)`` pair into one snapshot with top-``max_levels`` ladders
    plus touch (best bid/ask per side). Asset IDs accept hex (any case,
    ``0x``-optional) or decimal CTF IDs. Unpaired ticks are kept with
    the known side only. Output sorted by ``ts_ms``.
    """
    yes_key = normalize_asset_id(yes_asset)
    no_key = normalize_asset_id(no_asset)
    grouped: dict[tuple[Any, str], dict] = defaultdict(dict)
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (row.get("ts_ms"), str(row.get("market") or "").lower())
        asset = normalize_asset_id(row.get("asset"))
        if asset == yes_key:
            side = "yes"
        elif asset == no_key:
            side = "no"
        else:
            continue
        bids = sorted(parse_book_levels(row.get("bids")),
                      key=lambda lvl: -lvl[0])[:max_levels]
        asks = sorted(parse_book_levels(row.get("asks")),
                      key=lambda lvl: lvl[0])[:max_levels]
        grouped[key][side] = {"bids": bids, "asks": asks}
    snapshots: list[dict] = []
    for (ts_ms, market), sides in grouped.items():
        snap: dict = {"ts_ms": ts_ms, "market": market, "yes": sides.get("yes", {}),
                      "no": sides.get("no", {})}
        for side in ("yes", "no"):
            ladder = sides.get(side, {})
            bids = ladder.get("bids", []) if isinstance(ladder, dict) else []
            asks = ladder.get("asks", []) if isinstance(ladder, dict) else []
            snap[side] = {"bids": bids, "asks": asks,
                          "best_bid": max((p for p, _ in bids), default=None),
                          "best_ask": min((p for p, _ in asks), default=None)}
        snapshots.append(snap)
    snapshots.sort(key=lambda s: s.get("ts_ms") or 0)
    return snapshots


def pendulum_export_sql(parquet: str, market_hex: str) -> str:
    """DuckDB SQL exporting one market's ``book`` rows to JSON.

    Hour files are ~900MB — ALWAYS filter by ``market`` server-side;
    never ``SELECT *`` a full hour. ``market_hex`` is the market BLOB
    hex (from the market's ``new_market`` row or Gamma decimal IDs).
    """
    mhex = str(market_hex or "").strip()
    if mhex.lower().startswith("0x"):
        mhex = mhex[2:]
    return (
        "COPY (SELECT epoch_ms(timestamp) AS ts_ms, hex(market) AS market, "
        "hex(asset_id) AS asset, bids, asks "
        f"FROM '{parquet}' "
        f"WHERE event_type = 'book' AND market = unhex('{mhex}') "
        "ORDER BY timestamp) TO 'rows.json' (FORMAT JSON);"
    )


def export_market_books(parquet_path: str, market_hex: str, out_json: str) -> int:
    """Export one market's ``book`` rows via DuckDB (lazy import).

    Returns the exported row count. Raises RuntimeError with an install
    hint when ``duckdb`` is missing. Callers must supply a single-hour
    parquet (local path or ``https://archive.pendulumflow.com/v3/...``
    URL) — the ``WHERE market =`` filter keeps the ~900MB hour file
    from ever landing in memory.
    """
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required for Pendulum exports (pip install duckdb)"
        ) from exc
    mhex = str(market_hex or "").strip()
    if mhex.lower().startswith("0x"):
        mhex = mhex[2:]
    con = duckdb.connect()
    try:
        rel = con.execute(
            "SELECT epoch_ms(timestamp) AS ts_ms, hex(market) AS market, "
            "hex(asset_id) AS asset, bids, asks "
            f"FROM '{parquet_path}' "
            f"WHERE event_type = 'book' AND market = unhex('{mhex}') "
            "ORDER BY timestamp"
        )
        rows = rel.fetchall()
        cols = [d[0] for d in rel.description]
        import json

        with open(out_json, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(zip(cols, row,
                             strict=True))) + "\n")
        return len(rows)
    finally:
        con.close()
