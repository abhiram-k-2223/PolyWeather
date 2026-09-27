"""Pure resolution parsing for closed Polymarket markets (Step 3).

Gamma exposes no dedicated outcome field — a closed market's ``raw``
payload carries ``outcomePrices`` (e.g. ``["1", "0"]``), where index 0
is the first ``clob_token_id``. This module maps that to a per-token
win/loss boolean, returning ``None`` whenever the outcome is unknown
so callers never fabricate wins.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def _outcome_list(raw: Any) -> Optional[list]:
    """Extract the outcomePrices list from a market raw payload."""
    if not isinstance(raw, dict):
        return None
    outcomes = raw.get("outcomePrices", raw.get("outcome_prices"))
    if isinstance(outcomes, str):
        stripped = outcomes.strip()
        if not stripped.startswith("["):
            return None
        try:
            outcomes = json.loads(stripped)
        except (ValueError, TypeError):
            return None
    if not isinstance(outcomes, list) or not outcomes:
        return None
    return outcomes


def _is_winner(value: Any) -> bool:
    try:
        return abs(float(value) - 1.0) < 1e-9
    except (TypeError, ValueError):
        return False


def resolve_token_outcome(market: Any, token_id: str) -> Optional[bool]:
    """Return True (won) / False (lost) for ``token_id``, or None if unknown.

    Rules: market must be closed; exactly one outcome must read 1.0;
    ``token_id`` must be a known ``clob_token_ids`` entry. Anything
    else (open market, missing/ambiguous prices, unknown token) is
    unresolved — the caller must skip, never settle.
    """
    closed = bool(getattr(market, "closed", False))
    if not closed:
        return None
    token_ids = list(getattr(market, "clob_token_ids", []) or [])
    if token_id not in token_ids:
        return None
    outcomes = _outcome_list(getattr(market, "raw", None))
    if outcomes is None:
        return None
    winners = [i for i, v in enumerate(outcomes) if _is_winner(v)]
    if len(winners) != 1:
        return None
    return token_ids.index(token_id) == winners[0]
