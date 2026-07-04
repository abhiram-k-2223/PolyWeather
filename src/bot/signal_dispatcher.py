"""Trade signal dispatcher — pushes TradeSignal objects to Telegram.

This module bridges the trading engine's signal output to the Telegram
bot, formatting ``TradeSignal`` instances as human-readable messages
sent to the configured personal chat.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from loguru import logger

from src.trading.engine.signal_ingestion import TradeSignal


def _resolve_chat_id() -> str | None:
    for var in ("POLYWEATHER_SIGNAL_CHAT_ID", "TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_IDS"):
        raw = os.getenv(var, "").strip()
        if raw:
            return raw.split(",")[0].strip()
    return None


def format_signal_message(signal: TradeSignal) -> str:
    """Format a TradeSignal as a Telegram message (HTML)."""
    emoji = {
        "BUY": "🟢",
        "SELL": "🔴",
        "HOLD": "⚪",
    }.get(signal.direction.value, "⚪")

    conf_pct = round(signal.confidence * 100, 1)
    price_pct = round(signal.target_price * 100, 1)

    lines = [
        f"{emoji} <b>Trade Signal</b>",
        f"Direction: <b>{signal.direction.value}</b>",
        f"Confidence: <code>{conf_pct}%</code>",
        f"Target price: <code>${price_pct:.1f}</code>",
        f"Source: <code>{signal.source.value}</code>",
        f"Condition: <code>{signal.condition_id[:12]}…</code>",
    ]

    if signal.size is not None:
        lines.append(f"Size: <code>${signal.size:.2f}</code>")

    ts = signal.timestamp
    if isinstance(ts, datetime):
        lines.append(f"At: <code>{ts.strftime('%H:%M UTC')}</code>")

    meta = signal.metadata
    if meta and isinstance(meta, dict):
        extra = meta.get("summary") or meta.get("city") or ""
        if extra:
            lines.append(f"<code>{extra}</code>")

    return "\n".join(lines)


def dispatch_signal(
    bot: Any,
    signal: TradeSignal,
    *,
    chat_id: str | None = None,
) -> bool:
    """Send a formatted TradeSignal to the configured Telegram chat.

    Args:
        bot: A telebot.TeleBot instance (or any object with
             ``send_message(chat_id, text, **kwargs)``).
        signal: The TradeSignal to format and send.
        chat_id: Override the default chat ID from env.

    Returns True on success.
    """
    cid = chat_id or _resolve_chat_id()
    if not cid:
        logger.warning("No chat_id configured — signal undelivered")
        return False

    text = format_signal_message(signal)
    try:
        bot.send_message(cid, text, parse_mode="HTML", disable_web_page_preview=True)
        logger.info(
            "Signal dispatched direction=%s confidence=%.2f source=%s",
            signal.direction.value,
            signal.confidence,
            signal.source.value,
        )
        return True
    except Exception as exc:
        logger.error("Signal dispatch failed: %s", exc)
        return False
