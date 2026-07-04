"""Orchestrator — minimal Telegram bot for personal trade signals only.

Starts a polling bot that can push TradeSignals from the trading engine
to a configured personal chat. No community commands, no group
management, no points or rewards.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from src.utils.config_validation import validate_or_raise
from src.utils.config_loader import load_config

# Module-level bot reference for the signal dispatcher
_bot: Any = None


def get_bot() -> Any:
    """Return the running bot instance, or None if not started."""
    global _bot
    return _bot


def start_bot() -> None:
    global _bot

    import telebot

    from src.database.db_manager import DBManager

    validate_or_raise("bot")
    load_config()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot start bot")
        return

    _bot = telebot.TeleBot(token)
    DBManager()

    logger.info("Bot started in signal-only mode")
    _bot.infinity_polling(allowed_updates=["message"])
