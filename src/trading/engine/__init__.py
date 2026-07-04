"""Trading engine package.

The engine orchestrates signal ingestion, order management,
risk controls, and position tracking for automated Polymarket
trading driven by weather data.
"""

from .trading_engine import TradingEngine, EngineConfig
from .order_manager import OrderManager
from .signal_ingestion import SignalIngestor, TradeSignal
from .risk_engine import RiskEngine, RiskConfig
from .position_tracker import PositionTracker

__all__ = [
    "TradingEngine",
    "EngineConfig",
    "OrderManager",
    "SignalIngestor",
    "TradeSignal",
    "RiskEngine",
    "RiskConfig",
    "PositionTracker",
]
