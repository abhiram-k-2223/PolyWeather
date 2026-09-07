#!/usr/bin/env python3
"""Fetch real forecast/observation history from Open-Meteo for DEB testing.

Uses two free APIs (no API key required):

1. Previous Runs API (previous-runs-api.open-meteo.com)
   Archived day-1-ahead hourly forecasts per NWP model (GFS, ECMWF IFS,
   ICON). We take the max over each local calendar day to get each
   model's predicted daily high, matching DEB's forecast inputs.

2. ERA5 Archive API (archive-api.open-meteo.com)
   Observed daily maximum temperature, used as settled truth
   (``actual_high``) for error weighting and backtests.

Output: data/openmeteo_history.json with the structure DEB expects:
    {"city": {"YYYY-MM-DD": {"actual_high": float,
                             "forecasts": {model: float}}}}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

DEFAULT_OUTPUT = ROOT / "data" / "openmeteo_history.json"

# city -> (lat, lon, timezone) — same markets as the backtester demo set.
CITIES: dict[str, tuple[float, float, str]] = {
    "new york": (40.7128, -74.006, "America/New_York"),
    "london": (51.4777, -0.4614, "Europe/London"),
    "tokyo": (35.6895, 139.6917, "Asia/Tokyo"),
    "sydney": (-33.8688, 151.2093, "Australia/Sydney"),
    "dubai": (25.2532, 55.3657, "Asia/Dubai"),
}

# Open-Meteo model identifiers -> display names used in the history file.
# NOTE: the Previous Runs API requires the plural ``models`` query param.
# Using singular ``model`` is silently ignored and returns Best Match for
# every request — which produced the 433/433 identical GFS=ECMWF=ICON bug.
# ``ecmwf_ifs04`` is deprecated (returns no data); use ``ecmwf_ifs025``.
MODELS: dict[str, str] = {
    "gfs_seamless": "GFS",
    "ecmwf_ifs025": "ECMWF",
    "icon_seamless": "ICON",
}

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# ERA5 lags a few days behind real time.
ERA5_LAG_DAYS = 6


def _get_json(client: httpx.Client, url: str, params: dict) -> dict:
    resp = client.get(url, params=params, timeout=60.0)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Open-Meteo error for {url}: {payload.get('reason')}")
    return payload


def fetch_model_day1_forecasts(
    client: httpx.Client, lat: float, lon: float, tz: str, past_days: int
) -> dict[str, dict[str, float]]:
    """Return {model_name: {date: predicted_daily_high}} for day-1 runs."""
    result: dict[str, dict[str, float]] = {}
    for api_model, name in MODELS.items():
        payload = _get_json(
            client,
            PREVIOUS_RUNS_URL,
            {
                "latitude": lat,
                "longitude": lon,
                # Plural "models": singular "model" is silently ignored by
                # Open-Meteo and returns Best Match for every model.
                "models": api_model,
                "hourly": "temperature_2m_previous_day1",
                "past_days": past_days,
                "forecast_days": 1,
                "timezone": tz,
            },
        )
        hourly = payload.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m_previous_day1", [])
        by_date: dict[str, float] = {}
        for ts, temp in zip(times, temps):
            if temp is None:
                continue
            date = ts[:10]
            if date not in by_date or temp > by_date[date]:
                by_date[date] = float(temp)
        result[name] = by_date
    return result


def fetch_era5_observed_highs(
    client: httpx.Client,
    lat: float,
    lon: float,
    tz: str,
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    payload = _get_json(
        client,
        ARCHIVE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": "temperature_2m_max",
            "timezone": tz,
        },
    )
    daily = payload.get("daily", {})
    return {
        date: float(temp)
        for date, temp in zip(daily.get("time", []), daily.get("temperature_2m_max", []))
        if temp is not None
    }


def history_diversity_stats(history: dict) -> dict:
    """Return share of city-days where all model forecasts are identical.

    Healthy multi-model history should have near-zero identical days;
    real NWP models (GFS/ECMWF/ICON) rarely agree to 0.1C. A high share
    indicates the fetch pipeline collapsed to a single signal (e.g. wrong
    ``model``/``models`` query param returning Best Match for every model).
    """
    total = 0
    identical = 0
    for city_history in history.values():
        if not isinstance(city_history, dict):
            continue
        for record in city_history.values():
            if not isinstance(record, dict):
                continue
            forecasts = record.get("forecasts") or {}
            values = [v for v in forecasts.values() if v is not None]
            if len(values) < 2:
                continue
            total += 1
            if len(set(round(float(v), 1) for v in values)) == 1:
                identical += 1
    return {
        "total": total,
        "identical": identical,
        "identical_share": (identical / total) if total else None,
    }


def build_history(past_days: int = 92) -> dict:
    today = datetime.now(timezone.utc).date()
    end_obs = (today - timedelta(days=ERA5_LAG_DAYS)).isoformat()
    history: dict[str, dict] = {}
    with httpx.Client() as client:
        for city, (lat, lon, tz) in CITIES.items():
            print(f"Fetching {city} ...")
            forecasts = fetch_model_day1_forecasts(client, lat, lon, tz, past_days)
            empty_models = [m for m, vals in forecasts.items() if not vals]
            if empty_models:
                print(
                    f"  WARNING {city}: no data for {empty_models} — "
                    "check Open-Meteo model identifiers"
                )
            observed = fetch_era5_observed_highs(
                client, lat, lon, tz, forecasts_start_date(forecasts), end_obs
            )
            city_history: dict[str, dict] = {}
            for date in sorted(observed):
                day_forecasts = {
                    model: round(vals[date], 1)
                    for model, vals in forecasts.items()
                    if date in vals
                }
                if not day_forecasts:
                    continue
                city_history[date] = {
                    "actual_high": round(observed[date], 1),
                    "forecasts": day_forecasts,
                }
            # Forecast-only tail: Previous Runs data extends past ERA5
            # coverage (ERA5 lags ~6 days). Keep recent days with
            # ``actual_high=None`` so live/settling markets (e.g. closed
            # Polymarket dailies awaiting observation) can still be
            # scored walk-forward — consumers must handle None actuals
            # (DEB weight fitting already skips them).
            forecast_dates = {
                d for vals in forecasts.values() for d in vals
            }
            for date in sorted(forecast_dates):
                if date in city_history or date <= end_obs:
                    continue
                day_forecasts = {
                    model: round(vals[date], 1)
                    for model, vals in forecasts.items()
                    if date in vals
                }
                if not day_forecasts:
                    continue
                city_history[date] = {
                    "actual_high": None,
                    "forecasts": day_forecasts,
                }
            history[city] = city_history
            print(f"  {city}: {len(city_history)} days with forecasts + observation")
            time.sleep(1)  # be polite to the free API
    return history


def forecasts_start_date(forecasts: dict[str, dict[str, float]]) -> str:
    all_dates = [d for vals in forecasts.values() for d in vals]
    return min(all_dates) if all_dates else "2026-01-01"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--past-days", type=int, default=92)
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    history = build_history(past_days=args.past_days)
    total = sum(len(days) for days in history.values())
    if total == 0:
        raise SystemExit("No usable records fetched; refusing to write output.")

    stats = history_diversity_stats(history)
    share = stats["identical_share"]
    print(
        f"Model diversity: {stats['identical']}/{stats['total']} city-days "
        f"identical ({share:.1%} identical)"
        if share is not None else "Model diversity: no comparable city-days"
    )
    if share is not None and share > 0.5:
        raise SystemExit(
            f"Refusing to write output: {share:.1%} of city-days have identical "
            "GFS=ECMWF=ICON forecasts — the multi-model pipeline has collapsed "
            "to a single signal. Check the 'models' query param and identifiers."
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"Wrote {total} city-days across {len(history)} cities to {out}")


if __name__ == "__main__":
    main()
