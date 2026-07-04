"""Trade signal ingestion — converts weather observations into trading signals.

Reads analysis results from the weather pipeline (same data that powers
the web frontend) and generates structured ``TradeSignal`` objects that
the trading engine can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SignalDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalSource(Enum):
    """Which weather data stream generated this signal."""

    TEMPERATURE_ANOMALY = "temperature_anomaly"
    PRECIPITATION = "precipitation"
    WIND_GUST = "wind_gust"
    DEW_POINT = "dew_point"
    HEAT_INDEX = "heat_index"
    WIND_CHILL = "wind_chill"
    STORM_PROBABILITY = "storm_probability"
    SETTLEMENT_TRUTH = "settlement_truth"
    COMPOSITE = "composite"


@dataclass
class TradeSignal:
    """A normalized trade signal derived from weather data.

    Attributes:
        condition_id: Polymarket condition ID this signal targets.
        token_id: Specific outcome token ID.
        direction: BUY, SELL, or HOLD (no action).
        confidence: 0.0–1.0 confidence in the signal.
        target_price: Desired execution price (USDC per share, 0–1).
        source: Which weather metric generated this signal.
        metadata: Arbitrary extra data for downstream logging/analysis.
        timestamp: When the signal was generated.
        expires_at: Optional expiry for time-sensitive signals.
        size: Desired position size in USDC (None = engine decides).
    """

    condition_id: str
    token_id: str
    direction: SignalDirection
    confidence: float
    target_price: float
    source: SignalSource
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    size: Optional[float] = None


@dataclass
class WeatherObservationSnapshot:
    """A snapshot of weather observations for a city at a point in time."""

    city: str
    icao: str
    temperature_c: Optional[float]
    dew_point_c: Optional[float]
    humidity_pct: Optional[float]
    wind_speed_kmh: Optional[float]
    wind_gust_kmh: Optional[float]
    pressure_hpa: Optional[float]
    condition_text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)


class SignalIngestor:
    """Converts weather data snapshots into trade signals.

    The ingestor maps weather conditions to Polymarket markets.
    It parses city-level observations from the existing analysis
    pipeline and emits structured ``TradeSignal`` objects when
    conditions cross defined thresholds.
    """

    def __init__(
        self,
        city_to_market_map: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Args:
            city_to_market_map: Mapping of city ICAO codes to Polymarket
                condition IDs. Loaded from config if not provided.
        """
        self._city_to_market: dict[str, str] = city_to_market_map or {}
        self._signals: list[TradeSignal] = []

    def register_market(self, icao: str, condition_id: str, token_id: str) -> None:
        """Register a Polymarket market linked to a weather station."""
        self._city_to_market[icao] = condition_id
        # We store a mapping from condition -> token_id in metadata
        logger.info(
            "Registered market: %s -> condition=%s token=%s",
            icao,
            condition_id,
            token_id,
        )

    # ------------------------------------------------------------------
    # Ingestion methods
    # ------------------------------------------------------------------

    def ingest_observation(
        self, snapshot: WeatherObservationSnapshot
    ) -> list[TradeSignal]:
        """Process a weather observation snapshot and return any signals.

        Checks temperature, wind, and precipitation thresholds and
        generates BUY/SELL signals for the associated Polymarket market.
        """
        signals: list[TradeSignal] = []

        condition_id = self._city_to_market.get(snapshot.icao)
        if not condition_id:
            logger.debug("No market registered for %s (%s)", snapshot.city, snapshot.icao)
            return signals

        # -- temperature anomaly (e.g. > 5°C above city norm) ----------
        signals.extend(self._check_temperature(snapshot, condition_id))

        # -- wind gust threshold (e.g. > 50 km/h) ---------------------
        signals.extend(self._check_wind(snapshot, condition_id))

        # -- precipitation / storm ------------------------------------
        signals.extend(self._check_precipitation(snapshot, condition_id))

        self._signals.extend(signals)
        logger.info(
            "Ingested %d signals from %s (%s)",
            len(signals),
            snapshot.city,
            snapshot.icao,
        )
        return signals

    def ingest_from_analysis(
        self, analysis_result: dict[str, Any]
    ) -> list[TradeSignal]:
        """Ingest signals from a full city analysis result dict.

        This is the primary integration point with the existing
        ``analysis_service.py`` pipeline.
        """
        signals: list[TradeSignal] = []

        if not analysis_result:
            return signals

        city = analysis_result.get("city", "")
        icao = analysis_result.get("icao", "")
        condition_id = self._city_to_market.get(icao)
        if not condition_id:
            return signals

        # Extract consensus probability from analysis
        probability = analysis_result.get("probability")
        if probability is not None:
            signal = self._probability_to_signal(
                icao=icao,
                condition_id=condition_id,
                probability=float(probability),
                city=city,
                metadata={"analysis_timestamp": analysis_result.get("timestamp", "")},
            )
            if signal:
                signals.append(signal)

        # Check extreme weather flags
        alerts = analysis_result.get("alerts", [])
        for alert in alerts:
            s = self._alert_to_signal(icao, condition_id, alert, city)
            if s:
                signals.append(s)

        self._signals.extend(signals)
        return signals

    # ------------------------------------------------------------------
    # Internal threshold checks
    # ------------------------------------------------------------------

    def _check_temperature(
        self, snapshot: WeatherObservationSnapshot, condition_id: str
    ) -> list[TradeSignal]:
        """Check for temperature anomaly signals."""
        signals: list[TradeSignal] = []
        temp = snapshot.temperature_c
        if temp is None:
            return signals

        # Example: if temp > 35°C, signal "extreme heat" (probability up)
        if temp > 35.0:
            signals.append(TradeSignal(
                condition_id=condition_id,
                token_id="",  # resolved by engine
                direction=SignalDirection.BUY,
                confidence=min(0.9, (temp - 35.0) / 10.0),
                target_price=0.65,
                source=SignalSource.TEMPERATURE_ANOMALY,
                metadata={"temperature_c": temp, "city": snapshot.city},
            ))
        elif temp < -10.0:
            signals.append(TradeSignal(
                condition_id=condition_id,
                token_id="",
                direction=SignalDirection.BUY,
                confidence=min(0.9, (-10.0 - temp) / 10.0),
                target_price=0.65,
                source=SignalSource.TEMPERATURE_ANOMALY,
                metadata={"temperature_c": temp, "city": snapshot.city},
            ))
        return signals

    def _check_wind(
        self, snapshot: WeatherObservationSnapshot, condition_id: str
    ) -> list[TradeSignal]:
        """Check for wind gust threshold signals."""
        signals: list[TradeSignal] = []
        gust = snapshot.wind_gust_kmh or snapshot.wind_speed_kmh
        if gust is None:
            return signals
        if gust > 60.0:
            signals.append(TradeSignal(
                condition_id=condition_id,
                token_id="",
                direction=SignalDirection.BUY,
                confidence=min(0.8, gust / 100.0),
                target_price=0.55,
                source=SignalSource.WIND_GUST,
                metadata={"wind_gust_kmh": gust, "city": snapshot.city},
            ))
        return signals

    def _check_precipitation(
        self, snapshot: WeatherObservationSnapshot, condition_id: str
    ) -> list[TradeSignal]:
        """Check for precipitation/storm signals."""
        signals: list[TradeSignal] = []
        condition = snapshot.condition_text.lower()
        storm_keywords = ["thunderstorm", "heavy rain", "snow", "blizzard"]
        for kw in storm_keywords:
            if kw in condition:
                signals.append(TradeSignal(
                    condition_id=condition_id,
                    token_id="",
                    direction=SignalDirection.BUY,
                    confidence=0.7,
                    target_price=0.60,
                    source=SignalSource.STORM_PROBABILITY,
                    metadata={"condition_text": condition, "city": snapshot.city},
                ))
                break
        return signals

    def _probability_to_signal(
        self,
        icao: str,
        condition_id: str,
        probability: float,
        city: str,
        metadata: dict,
    ) -> Optional[TradeSignal]:
        """Convert an analysis probability (0–100) to a BUY/SELL signal.

        The logic:
          - probability sufficiently > 50 (strong YES) → BUY YES tokens
          - probability sufficiently < 50 (strong NO) → BUY NO tokens (SELL YES)
          - otherwise → HOLD
        """
        THRESHOLD = 15.0  # must be this many points from 50 to trigger
        if probability > 50.0 + THRESHOLD:
            return TradeSignal(
                condition_id=condition_id,
                token_id="",
                direction=SignalDirection.BUY,
                confidence=min(0.95, (probability - 50.0) / 50.0),
                target_price=probability / 100.0 * 0.9,
                source=SignalSource.COMPOSITE,
                metadata={"probability": probability, **metadata},
            )
        elif probability < 50.0 - THRESHOLD:
            return TradeSignal(
                condition_id=condition_id,
                token_id="",
                direction=SignalDirection.SELL,
                confidence=min(0.95, (50.0 - probability) / 50.0),
                target_price=(100.0 - probability) / 100.0 * 0.9,
                source=SignalSource.COMPOSITE,
                metadata={"probability": probability, **metadata},
            )
        return None

    def _alert_to_signal(
        self, icao: str, condition_id: str, alert: dict, city: str
    ) -> Optional[TradeSignal]:
        """Convert a weather alert to a trade signal."""
        alert_type = alert.get("type", "")
        severity = alert.get("severity", "moderate")

        confidence_map = {"extreme": 0.9, "severe": 0.8, "moderate": 0.6}
        conf = confidence_map.get(severity, 0.5)

        return TradeSignal(
            condition_id=condition_id,
            token_id="",
            direction=SignalDirection.BUY,
            confidence=conf,
            target_price=0.7,
            source=SignalSource.COMPOSITE,
            metadata={"alert_type": alert_type, "severity": severity, "city": city},
        )
