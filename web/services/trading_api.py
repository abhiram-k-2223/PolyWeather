"""Trading service module — integrates the trading engine with FastAPI.

Provides the singleton engine, lifecycle management (start/stop with
the app), and the async entry points called from routes and from
the existing weather pipeline.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from src.trading.engine import TradingEngine, EngineConfig, RiskConfig
from src.trading.engine.signal_ingestion import (
    TradeSignal,
    WeatherObservationSnapshot,
)
from src.trading.polymarket.wallet import PolyWalletConfig, WalletManager
from src.trading.storage.trade_store import TradeStore

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Singleton state
# ------------------------------------------------------------------

_ENGINE: Optional[TradingEngine] = None
_STORE: Optional[TradeStore] = None
_WALLET: Optional[WalletManager] = None


# ------------------------------------------------------------------
# Initialization
# ------------------------------------------------------------------

def _engine_enabled() -> bool:
    raw = os.environ.get("POLYWEATHER_TRADING_ENABLED", "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_paper_mode() -> bool:
    """Paper execution branch flag — defaults True (safe default).

    Live CLOB writes require explicit opt-out: POLY_PAPER_MODE=0/false/no/off.
    """
    raw = os.environ.get("POLY_PAPER_MODE", "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_city_market_map() -> dict[str, tuple[str, str]]:
    """Load the ICAO -> (condition_id, token_id) seed map.

    Format (``POLY_MARKET_MAP`` env JSON)::

        {"KLGA": ["0xcondition…", "123 token…"], "KLAX": ["0x…", "456…"]}

    Daily temperature markets expire, so this seed goes stale. Prefer the
    daily Gamma refresh — ``refresh_market_map_from_gamma()`` resolves
    each ``POLY_TRADING_FEED_CITIES`` ICAO to today's condition via
    ``GammaClient.resolve_city_markets()`` and pushes the result into the
    engine with ``TradingEngine.update_market_map()`` (refresh overwrites
    resolved entries, leaves the rest alone).
    """
    """Load ICAO -> (condition_id, token_id) mappings from env.

    Format: POLY_MARKET_MAP='{"KLAX":("cond_abc","token_xyz"),...}'
    Falls back to empty dict.
    """
    raw = os.environ.get("POLY_MARKET_MAP", "")
    if not raw:
        return {}
    try:
        import json
        parsed = json.loads(raw)
        return {
            icao: (v[0], v[1])
            for icao, v in parsed.items()
        }
    except Exception as exc:
        logger.warning("Failed to parse POLY_MARKET_MAP: %s", exc)
        return {}


def _build_wallet() -> Optional[WalletManager]:
    """Build a WalletManager from environment config.

    Returns None if the trading private key is not set (trading disabled).
    """
    pk = os.environ.get("POLY_TRADING_PRIVATE_KEY", "")
    if not pk:
        logger.info("POLY_TRADING_PRIVATE_KEY not set — trading engine will not start")
        return None
    chain_id = int(os.environ.get("POLY_CHAIN_ID", "137"))
    rpc_url = os.environ.get("POLY_RPC_URL", "https://polygon-rpc.com")
    return WalletManager(
        PolyWalletConfig(private_key=pk, chain_id=chain_id, rpc_url=rpc_url)
    )


def _feed_cities(explicit: dict[str, str] | None = None) -> dict[str, str]:
    """ICAO -> city-name allowlist for the signal feed.

    Defaults to ``{"KLGA": "new york", "KLAX": "los angeles",
    "KORD": "chicago"}``; override with ``POLY_TRADING_FEED_CITIES``
    (comma-separated ICAOs, e.g. ``"KLGA,KLAX"``). Unknown ICAOs fall
    back to the lowercased ICAO as the Gamma search string (usually
    resolving to nothing and skipped).
    """
    from src.trading.polymarket.market_resolver import DEFAULT_FEED_CITIES

    if explicit:
        return dict(explicit)
    raw = os.environ.get("POLY_TRADING_FEED_CITIES", "")
    if not raw.strip():
        return dict(DEFAULT_FEED_CITIES)
    cities: dict[str, str] = {}
    for token in raw.split(","):
        icao = token.strip().upper()
        if not icao:
            continue
        cities[icao] = DEFAULT_FEED_CITIES.get(icao, icao.lower())
    return cities or dict(DEFAULT_FEED_CITIES)


def refresh_market_map_from_gamma(
    client: Any | None = None,
    engine: TradingEngine | None = None,
    feed_cities: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Resolve each feed city to today's market and push it to the engine.

    Fetch candidates per city via
    ``GammaClient.resolve_city_markets(city)``, pick one market each with
    ``build_market_map()``, and register the result via
    ``TradingEngine.update_market_map()``. Unresolvable cities are left
    out (prior entries stay). Returns the resolved map.
    """
    from src.trading.polymarket.market_resolver import build_market_map

    cities = _feed_cities(feed_cities)
    if client is None:
        from src.trading.polymarket.gamma_client import GammaClient

        client = GammaClient()
    candidates: dict[str, tuple[str, list]] = {}
    for icao, city in cities.items():
        try:
            markets = client.resolve_city_markets(city)
        except Exception as exc:
            logger.warning("Gamma resolve failed for %s (%s): %s", icao, city, exc)
            continue
        candidates[icao] = (city, list(markets or []))
    market_map = build_market_map(candidates)
    target = engine if engine is not None else get_engine()
    if target is not None and market_map:
        target.update_market_map(market_map)
    return market_map


async def check_and_settle_closed_markets(
    engine: TradingEngine | None = None,
    client: Any | None = None,
) -> list:
    """Settle open paper positions whose markets have closed (Step 3 loop).

    For each open paper position, fetch the market via
    ``GammaClient.get_market(condition_id)`` and parse the outcome with
    ``resolve_token_outcome()``. Resolved positions settle through
    ``TradingEngine.settle_paper_position()`` (which feeds realized PnL
    into the risk engine); unresolved markets and per-market fetch
    failures are skipped so one bad market never blocks the loop.
    Returns the settled records.
    """
    from src.trading.polymarket.market_resolution import resolve_token_outcome

    target = engine if engine is not None else get_engine()
    if target is None:
        return []
    store = getattr(target, "_paper_store", None)
    if store is None:
        return []
    if client is None:
        from src.trading.polymarket.gamma_client import GammaClient

        client = GammaClient()
    settled: list = []
    for pos in list(store.get_open_positions()):
        try:
            market = await client.get_market(pos.condition_id)
        except Exception as exc:
            logger.warning(
                "Settlement check failed for %s: %s", pos.condition_id[:10], exc
            )
            continue
        if market is None:
            continue
        won = resolve_token_outcome(market, pos.token_id)
        if won is None:
            continue
        record = target.settle_paper_position(pos.token_id, won=won)
        if record is not None:
            settled.append(record)
    return settled


def _build_engine_config() -> EngineConfig:
    """Build EngineConfig from environment variables with sensible defaults."""
    risk = RiskConfig(
        max_position_size_usdc=float(
            os.environ.get("POLY_MAX_POSITION_SIZE_USDC", "500")
        ),
        max_total_exposure_usdc=float(
            os.environ.get("POLY_MAX_TOTAL_EXPOSURE_USDC", "5000")
        ),
        max_order_count=int(os.environ.get("POLY_MAX_ORDER_COUNT", "10")),
        max_daily_trades=int(os.environ.get("POLY_MAX_DAILY_TRADES", "50")),
        min_confidence_threshold=float(
            os.environ.get("POLY_MIN_CONFIDENCE", "0.6")
        ),
        cooldown_seconds=float(os.environ.get("POLY_COOLDOWN_SEC", "300")),
        max_drawdown_pct=float(os.environ.get("POLY_MAX_DRAWDOWN", "0.15")),
    )
    return EngineConfig(
        enabled=_engine_enabled(),
        paper_mode=_is_paper_mode(),
        poll_interval_seconds=float(
            os.environ.get("POLY_POLL_INTERVAL_SEC", "60")
        ),
        reconcile_interval_seconds=float(
            os.environ.get("POLY_RECONCILE_INTERVAL_SEC", "300")
        ),
        max_orders_per_run=int(os.environ.get("POLY_MAX_ORDERS_PER_RUN", "3")),
        risk=risk,
        city_to_market_map=_load_city_market_map(),
    )


# ------------------------------------------------------------------
# Public API (called by routes and by app_factory)
# ------------------------------------------------------------------


def get_store() -> TradeStore:
    """Return the singleton TradeStore."""
    global _STORE
    if _STORE is None:
        _STORE = TradeStore()
    return _STORE


def get_engine() -> Optional[TradingEngine]:
    """Return the singleton TradingEngine (may be None if disabled)."""
    global _ENGINE
    return _ENGINE


def init_trading_engine() -> Optional[TradingEngine]:
    """Initialize the trading engine singleton from environment.

    Called once during app startup (see ``start_trading_engine``).
    Returns None if trading is not configured.
    """
    global _ENGINE, _WALLET

    if _ENGINE is not None:
        return _ENGINE

    config = _build_engine_config()
    wallet = _build_wallet()
    if wallet is None and not config.paper_mode:
        logger.info("Trading engine not initialized — no wallet configured")
        return None
    if wallet is None:
        logger.info("Paper mode: initializing engine without wallet (no live writes)")

    _WALLET = wallet
    _ENGINE = TradingEngine(wallet=wallet, config=config)
    if config.paper_mode and not config.enabled:
        logger.info("Trading engine initialized in paper mode (enabled=%s)", config.enabled)
    elif config.paper_mode:
        logger.info("Trading engine initialized PAPER (enabled=%s) — no live orders", config.enabled)
    else:
        logger.warning(
            "Trading engine initialized LIVE (paper_mode=0, enabled=%s) — real orders!",
            config.enabled,
        )
    return _ENGINE


def start_trading_engine() -> None:
    """Start the trading engine background loop.

    Called from the app startup lifecycle.
    """
    engine = init_trading_engine()
    if engine and engine.config.enabled:
        engine.start()
        logger.info("Trading engine background loop started")
    else:
        logger.info(
            "Trading engine not started (enabled=%s, engine=%s)",
            engine.config.enabled if engine else "N/A",
            "exists" if engine else "None",
        )


def stop_trading_engine() -> None:
    """Stop the trading engine background loop.

    Called from the app shutdown lifecycle.
    """
    global _ENGINE
    if _ENGINE:
        _ENGINE.stop()
        _ENGINE = None
        logger.info("Trading engine stopped and cleared")


# ------------------------------------------------------------------
# Integration helpers for the weather pipeline
# ------------------------------------------------------------------


async def process_weather_signal(signal: TradeSignal) -> Optional[dict]:
    """Process a trade signal from the weather analysis pipeline.

    Called from existing code paths when new weather data is analyzed.
    Returns the order result dict, or None if skipped/rejected.
    """
    engine = get_engine()
    if not engine or not engine.config.enabled:
        return None

    order = await engine.process_signal(signal)
    if order:
        store = get_store()
        await store.save_signal({
            "condition_id": signal.condition_id,
            "token_id": signal.token_id,
            "direction": signal.direction.value,
            "confidence": signal.confidence,
            "target_price": signal.target_price,
            "source": signal.source.value,
            "metadata": signal.metadata,
            "timestamp": signal.timestamp.isoformat(),
        })
        return {
            "local_id": order.local_id,
            "order_id": order.order_id,
            "state": order.state.value,
        }
    return None


async def process_weather_observation(
    city: str,
    icao: str,
    temperature_c: Optional[float],
    wind_speed_kmh: Optional[float],
    condition_text: str,
    **extra,
) -> list[dict]:
    """Process a raw weather observation snapshot through the trading engine.

    This is the simplest integration point — call it from the collector
    or analysis service whenever weather data is refreshed.
    """
    engine = get_engine()
    if not engine or not engine.config.enabled:
        return []

    snapshot = WeatherObservationSnapshot(
        city=city,
        icao=icao,
        temperature_c=temperature_c,
        dew_point_c=extra.get("dew_point_c"),
        humidity_pct=extra.get("humidity_pct"),
        wind_speed_kmh=wind_speed_kmh,
        wind_gust_kmh=extra.get("wind_gust_kmh"),
        pressure_hpa=extra.get("pressure_hpa"),
        condition_text=condition_text,
        raw=extra,
    )
    orders = await engine.process_observation(snapshot)
    return [
        {
            "local_id": o.local_id,
            "order_id": o.order_id,
            "state": o.state.value,
        }
        for o in orders
    ]


# ------------------------------------------------------------------
# Engine status (for /api/trading/status endpoint)
# ------------------------------------------------------------------


def trading_status() -> dict[str, Any]:
    """Return trading engine status with P&L and risk info."""
    engine = get_engine()
    if not engine:
        return {
            "enabled": False,
            "running": False,
            "error": "Trading engine not initialized",
        }
    return engine.get_status()
