# PolyWeather — Full Project Context

> **Last updated:** 2026-07-03  
> **Branch:** `main`  
> **Python:** 3.14 | **Frontend:** Next.js 14 (React, TypeScript)  
> **Backend:** FastAPI + SQLite + Supabase | **Trading target:** Polymarket CLOB

---

## 1. What Is PolyWeather?

PolyWeather is a **real-time weather analysis and prediction market trading platform**. It ingests weather observations from dozens of global sources, runs a custom ensemble blending model (DEB — Dynamic Ensemble Blending) to predict daily high temperatures for 40+ cities worldwide, and feeds those predictions into position sizing and trade execution on Polymarket's temperature outcome markets.

The product has three audiences:

- **Retail users** (frontend at `polyweather.top`) — see live temperature charts, model consensus, probability distributions, DEB accuracy, and intraday meteorology commentary.
- **Telegram subscribers** — receive daily weather briefings with DEB predictions, settlement probabilities, trend analysis, and payout alerts via a Telegram bot.
- **Internal (operators)** — admin dashboard (`/ops`) for health monitoring, accuracy tracking, membership management, payment audit, and system config.

**Core differentiators vs. general-purpose weather APIs:**

- Settlement-source priority — each city has a configured settlement source (METAR, HKO, NOAA, Wunderground, CWA, IMS, NCM, AEROWEB) that defines the "ground truth" for prediction market resolution.
- Real-time observation sources — diverse network of airport METAR, AMOS, AMSC AWOS runway plates, MGM, KNMI, FMI, JMA, KMA, HKO, CWA, Singapore MSS, NWS, MADIS, and more.
- Runway/city-level granular temperature — not county-wide grid forecasts, but airport- or station-level temperature used for market settlement.
- SSE/real-time patches — Server-Sent Events deliver temperature observation patches as they arrive.
- Telegram caching — Telegram pushes read from latest cached data, not forcing external refreshes.
- Interpretation layer — intraday meteorology engine synthesizes boundary-layer structure, TAF cloud/rain signals, station-network deltas, and peak-window status into structured trading intelligence.

---

## 2. Codebase Architecture

```
PolyWeather/
├── src/                    # Python backend core
│   ├── analysis/           # Prediction models (DEB, probability, evaluation)
│   ├── async_infra/        # Async event loop, HTTP client, rate limiter, Redis
│   ├── auth/               # Supabase entitlements, Telegram pricing
│   ├── bot/                # Telegram bot orchestrator, signal dispatcher
│   ├── data_collection/    # Weather data sources (40+ source adapters)
│   │   └── sources/        # Individual source implementations
│   ├── data_mining/        # Historical data fetch utilities
│   ├── database/           # SQLite + RuntimeState persistence layer
│   ├── models/             # Pydantic model definitions
│   ├── onchain/            # On-chain contract interactions
│   ├── payments/           # Smart contract checkout, confirm loop, audit
│   ├── trading/            # Polymarket trading engine
│   │   ├── engine/         # Risk engine, signal ingestion, order mgmt, position tracking
│   │   ├── polymarket/     # CLOB client, Data API client, wallet, neg-risk adapter
│   │   └── storage/        # Trade store (local SQLite)
│   └── utils/              # Config, logging, metrics, telegram push, secrets
├── web/                    # FastAPI web service
│   ├── routers/            # API route modules (city, ops, trading, analytics, etc.)
│   └── services/           # Business logic (analysis signals, city payloads, ops_api)
├── frontend/               # Next.js 14 application
│   ├── app/                # App router pages
│   ├── components/         # React components (dashboard, account, ops, terminal)
│   └── lib/                # Client utilities, Supabase helpers, chart utils
├── tests/                  # 60+ pytest test files
├── scripts/                # Maintenance scripts (backfill, backtest, migration)
├── contracts/              # Solidity smart contracts (PolyWeatherCheckout V1/V2)
├── deploy/                 # Nginx config, Dockerfile, CI
├── docs/                   # Documentation (directory currently empty)
└── data/                   # Runtime data (SQLite DB, JSON records)
```

### Key Architectural Patterns

- **Singleton state storage** — `RuntimeStateDB` backed by SQLite, with migration from JSON file storage (`STATE_STORAGE_FILE` → `STATE_STORAGE_SQLITE`). All repositories (DailyRecord, TruthRecord, TrainingFeature, OpenMeteoCache, IntradayPathSnapshot, etc.) use this.
- **Observation collector** — background worker loop (`ObservationCollectorWorker`) that periodically refreshes each city's weather sources with configurable intervals, rate limiting, and singleflight.
- **Analysis service** — on-demand city analysis via `_analyze()` in `web/analysis_service.py`, with an LRU cache (`_cache` dict) supporting panel/full/market/nearby detail modes and time-to-live per city.
- **Trading engine** — `TradingEngine` runs as an optional background coroutine loop (disabled by default, enabled via `POLYWEATHER_TRADING_ENABLED`). Integrates with the analysis pipeline via `ingest_from_analysis()`.
- **DEB prediction pipeline** — from raw model forecasts → model family deduplication → dynamic weight calculation → bias correction (recent-bias or bucket-calibrated, with guarded selection) → intraday adjustment → hourly path correction → probability distribution.

---

## 3. What Has Been Built (and Why)

### 3.1 Prediction Model: DEB (Dynamic Ensemble Blending)

**Files:** `src/analysis/deb_algorithm.py`, `src/analysis/deb_evaluation.py`, `src/analysis/settlement_rounding.py`

**Why DEB instead of logistic regression?** The core prediction task is to forecast a continuous temperature value (the day's high in °C/°F) from multiple numerical weather model outputs. This is a **regression** problem, not a classification problem. Logistic regression is designed for binary/multiclass classification. Ensemble methods — weighting models by their recent track record — are the industry standard for NWP post-processing.

**How DEB works:**

1. **Model family deduplication** (`_collapse_forecasts_for_deb`): Regional high-resolution variants (ICON-D2, HRDPS) replace their global parent (ICON, GEM) to avoid double-counting.
2. **Dynamic weight calculation** (`calculate_dynamic_weights`): For each city, the last N days of model-vs-actual errors are aggregated with exponential time decay (factor 0.85). The blended error (daily MAE + hourly MAE with 0.3/0.7 weight split) feeds inverse-error weighting.
3. **Bias correction** (3 versions, in `deb_evaluation.py`):
   - `deb_v1_raw`: Raw weighted ensemble.
   - `deb_v1_recent_bias_corrected`: City-level signed bias from recent N days, with shrinkage regularization.
   - `deb_v2_bucket_calibrated`: Grid-searches over +/-3°C in 0.1° steps to find the adjustment that maximizes bucket hit rate on recent data.
   - `deb_v3_guarded_calibrated`: Holdout-validation choice between recent-bias and bucket-calibrated, preventing overfitting.
4. **Intraday adjustment** (`web/analysis_service.py`): When local time is within peak window, the current observed temperature bias vs. model hourly curve is used to nudge the DEB prediction upward (0.15–0.80 weight depending on proximity to peak).
5. **Hourly path correction** (`src/analysis/deb_hourly_correction.py`): Historical observation-vs-base-path errors are bucketed by city + hour-of-day and by city + phase (before_peak/peak_window/after_peak), then applied to shape the intraday temperature curve.

**Settlement rounding** (`settlement_rounding.py`): WU-style rounding (0.5 always rounds up) for most cities; HKO-settled cities use floor rounding per Hong Kong Observatory protocol.

### 3.2 Probability Distribution Engine

**File:** `src/analysis/trend_engine.py` — `calculate_prob_distribution()`

**Why Gaussian instead of Platt/isotonic?** The current probability engine computes a Gaussian CDF around the ensemble median (or a blended center), using the ensemble spread (P90-P10) as the sigma. This was chosen as a **minimal-viable probability layer** that requires no historical calibration training — it works from the first day of data for any city.

**Current limitations that drive the calibration roadmap:**
- The Gaussian assumes a fixed parametric shape; real forecast errors are often skewed or multimodal.
- There is no temperature-bucket-specific calibration (Platt scaling per bucket or isotonic regression).
- The sigma estimate is heuristic (ensemble spread, adjusted by shock score and peak status).
- Brier scores around 0.100–0.150 indicate room for improvement.

### 3.3 Signal Ingestion & Trading Engine

**Files:** `src/trading/engine/signal_ingestion.py`, `trading_engine.py`, `risk_engine.py`, `order_manager.py`, `position_tracker.py`

The trading engine is a **complete but greenfield** module — the architecture is in place but it has not yet been battle-tested on live Polymarket markets.

**Signal generation:**
- `SignalIngestor` converts weather observations and analysis results into `TradeSignal` objects.
- Temperature anomaly thresholds (>35°C or <-10°C) → BUY signals.
- Wind gust >60 km/h → BUY signals.
- Thunderstorm/heavy rain/snow keywords → BUY signals.
- Analysis probability -> signal direction (above 65 → BUY, below 35 → SELL).

**Position sizing (current):** `_compute_position_size()` in `trading_engine.py` is a simple `base * confidence` linear formula. This is where **Quarter Kelly** will replace the current heuristic.

**Risk engine:**
- `RiskConfig` — max position size ($500), max total exposure ($5,000), max 10 orders, max 50 trades/day, min 0.6 confidence, 300s cooldown after loss, 15% max drawdown, 50 bps max slippage.
- `RiskEngine.assess()` — runs all checks sequentially.

**Polymarket integration:**
- `CLOBClient` — async REST client with EIP-712 auth, HMAC signing, rate limiting, retries.
- `WalletManager` — manages Polygon wallet, builds auth messages, signs typed data (EIP-712).
- `NegRiskAdapter` — handles neg-risk market orders via ERC-1155 permits.

### 3.4 Data Collection Layer (40+ Source Adapters)

**Files:** `src/data_collection/sources/*.py`

Sources grouped by geography and data type:

| Source | Coverage | API Type |
|--------|----------|----------|
| **METAR** | Global (via aviationweather.gov) | REST JSON |
| **AMOS** | South Korea (airport obs) | REST JSON |
| **AMSC AWOS** | China (Shanghai, Beijing, etc.) | REST + sessionId |
| **NWS Open-Meteo** | Global (Open-Meteo API) | REST JSON |
| **NOAA WRH** | US (weather.gov timeseries) | REST JSON |
| **MGM** | Turkey | REST JSON |
| **KNMI** | Netherlands | Open Data API |
| **FMI** | Finland | WFS |
| **JMA** | Japan | REST JSON |
| **KMA** | South Korea | GeoJSON |
| **HKO** | Hong Kong | RYES daily endpoint |
| **CWA** | Taiwan | Open Data API |
| **Singapore MSS** | Singapore | REST JSON |
| **IMS** | Israel | REST JSON |
| **NCM** | Saudi Arabia | REST JSON |
| **MADIS** | US (high-freq) | NetCDF |
| **Wunderground** | Global | Historical PWS |
| **AeroWeb** | Europe | REST JSON |
| **COWIN** | US (wind) | REST JSON |
| **Multi-Model** | Global (ECMWF, GFS, ICON, GEM, etc.) | Open-Meteo multi-model |

Each source adapter implements:
- City-specific fetching with ICAO/station-code lookup
- Temperature conversion (°C ↔ °F)
- Plausibility checks (`min_plausible_metar_temp_c`)
- Cache integration via `open_meteo_cache_store`
- Rate limit compliance

### 3.5 Intraday Meteorology & Interpretation Layer

**File:** `web/analysis_service.py` — `_build_intraday_meteorology()`

This is PolyWeather's "secret sauce" — a structured interpretation engine that synthesizes multiple signals into a trading/meteorology read:

1. **Intraday pace** (deviation monitor): How current temp tracks vs. the DEB hourly curve.
2. **Boundary-layer setup** (vertical profile): CAPE, CIN, lifted index, boundary-layer height from hourly forecast data — determines if the atmosphere supports continued mixing/warming or suppresses it.
3. **TAF cloud/rain disruption**: TAF reports parsed for medium/high suppression or disruption that caps the afternoon peak.
4. **Station-network comparison**: Airport vs. nearby official station network deltas.
5. **Peak window status**: Before/in/past, with first/last peak hour from multi-model consensus.

Output: structured `headline`, `confidence`, `base_case_bucket`, `upside/downside_bucket`, `confirmation_rules`, `invalidation_rules`, and `signal_contributions` — all available to the frontend and the trading engine.

### 3.6 Ops Dashboard & Accuracy Tracking

**Files:** `web/services/ops_api.py`, `web/routers/ops.py`

The ops panel provides:

- **Training accuracy** (`get_ops_training_accuracy`): Per-city DEB hit rate, MAE, and recent-14d/7d window metrics, plus backtested version comparisons (raw vs. recent-bias vs. bucket-calibrated vs. guarded).
- **DEB version backtest** (`backtest_deb_versions` in `deb_evaluation.py`): Walk-forward cross-validation comparing all four DEB versions, producing MAE, RMSE, bias, and bucket hit rate per version.
- **Source health** (`get_ops_source_health`): Per-city freshness, latency, and status for all data sources.
- **Observation collector status** (`get_ops_observation_collector_status`): Per-source/city collector health, cooldown, due times.
- **Health check** (`get_ops_health_check`): Live ping to all 15+ external APIs (Supabase, Open-Meteo, METAR, KNMI, MADIS, Telegram, JMA, MGM, FMI, KMA, HKO, Singapore MSS, CWA, AMOS, AMSC AWOS, NOAA WRH).
- **Payment risk, membership management, Telegram audit, billing risk** — full operations suite.

### 3.7 Backtesting Infrastructure

**Scripts:**
- `scripts/backtest_deb_versions.py` — Run versioned DEB backtests from SQLite daily records.
- `scripts/backtest_metar_calibrated_path.py` — Backtest DEB baseline vs. METAR/observation-calibrated intraday path (564-line script with full data pipeline: loading hourly forecasts, observations, constructing calibrated paths, and computing summary statistics).

The `backtest_metar_calibrated_path.py` script implements:
- **Calibrated future path** (`calibrated_future_path`): Weights recent observation deviations (last 3 obs, linearly weighted), applies them to the DEB hourly curve, then decays the adjustment toward evening (exponent 1.35).
- **Two evaluation modes:** Strict (reconstructed legacy stores) and snapshots (intraday_path_snapshots_store).
- **Summary output:** DEB MAE vs. Calibrated MAE, bucket hit rates, improved/worsened/unchanged counts.

### 3.8 Frontend & User Experience

**Tech stack:** Next.js 14 (App Router), TypeScript, Tailwind CSS, Supabase auth, server-sent events.

**Key pages:**
- `/terminal` — Scan Terminal: Real-time temperature dashboard with city cards, hourly charts, probability buckets, DEB predictions, market scan filters.
- `/ops/*` — Operations panel: Analytics, config, feedback, health, memberships, overview, payments, subscriptions, system, Telegram audit, training accuracy, truth history, users, view-logs.
- `/account` — Account center: Subscription management, points, wallet bind.
- `/docs` — Documentation pages rendered from MDX content.

### 3.9 Payments & Subscriptions

**Contracts:** `contracts/PolyWeatherCheckout.sol` (V1), `PolyWeatherCheckoutV2.sol`  
**Files:** `src/payments/confirm_loop.py`, `contract_checkout.py`, `contract_audit.py`

Polygon-based USDC payment flow:
1. User initiates payment via smart contract checkout.
2. `ConfirmLoop` polls for on-chain confirmation.
3. `ContractAudit` verifies transaction integrity.
4. Supabase subscription records updated on confirmation.
5. Points system for loyalty rewards, referral bonuses.

### 3.10 Telegram Bot

**Files:** `src/bot/orchestrator.py`, `signal_dispatcher.py`

The Telegram bot delivers:
- Daily weather briefings with DEB predictions, probability distributions, and trend analysis.
- Settlement alerts when actual highs are confirmed.
- Growth milestone rewards and weekly reward loops.
- Multi-language support (Chinese/English) via `telegram_i18n.py`.

---

## 4. The Four Priority Areas: Strategic Roadmap

Based on the codebase analysis, the following four priority areas were identified. They form a sequential dependency chain — each builds on the previous.

### Priority 1: Prediction Model Roadmap (DEB Enhancements)

**Goal:** Increase DEB bucket hit rate from the current ~50-60% range toward 70%+.

**Strategy — layered ensemble, not model replacement:**
- **DEB stays as the raw ensemble core.** It is a proven, interpretable regression method.
- **Add seasonal weighting:** Current decay factor is uniform; a separate day-of-year error profile would help cities with strong seasonality (e.g., Moscow: winter errors differ from summer).
- **Improve hourly error blend:** The 0.3/0.7 daily/hourly weight split is a heuristic; cross-validated weights per city would be better.
- **Multi-horizon DEB:** Separate weight sets for different forecast lead times (D+0 vs D+1+).
- **Wind/precipitation as auxiliary features:** Currently DEB uses only temperature forecasts. Adding wind direction, cloud cover, and precipitation probability as regression features could reduce error.
- **Bias correction v4 — per-bucket adaptive:** The current bucket calibration grid-searches a uniform adjustment. Per-temperature-bucket corrections could handle asymmetric error distributions.

### Priority 2: Probability Model Calibration (Platt Scaling)

**Problem:** The current Gaussian CDF-based probability distribution produces Brier scores around 0.100–0.150. For prediction markets, **calibrated probabilities** are what matter — a 70% prediction should mean the event happens 70% of the time.

**Why Platt scaling (logistic calibration) over isotonic regression:**

| Criterion | Platt Scaling | Isotonic Regression |
|-----------|--------------|-------------------|
| Data efficiency | Works well with 200-500 samples | Needs 1000+ samples |
| Extrapolation | Reasonable outside training range | Clamps to nearest training value |
| Smoothness | Sigmoid → smooth, monotonic | Stepwise, can overfit small bins |
| Output range | (0, 1) — no degenerate 0/1 | May predict exactly 0 or 1 |
| Multiclass extension | One-vs-rest or temperature bucket bins | Pairwise coupling (more complex) |

Given PolyWeather has ~40 cities × ~100-200 settled days = 4,000-8,000 total training samples per temperature bucket, **Platt scaling is the correct choice** for its data efficiency and stable extrapolation.

**Implementation plan:**
1. **Feature extraction** per bucket: raw mu, raw sigma (Gaussian CDF output), ensemble spread, peak status, season, hour of day, model count.
2. **Training:** Scikit-learn `LogisticRegression(C=1.0)` on binary outcome (bucket hit yes/no) per temperature bucket, with train/validation split.
3. **Calibrated probability output:** `P(bucket=target | mu, sigma, features)` via Platt sigmoid.
4. **Shadow probability:** Run both old Gaussian and new Platt-calibrated probabilities in parallel for N days, compare Brier scores, then switch.

**Why not isotonic:** At current sample volumes, isotonic regression would overfit the per-bucket bins, especially for extreme temperature values where data is sparse.

### Priority 3: Quarter Kelly Position Sizing

**Problem:** Current position sizing is `base * confidence` (linear heuristic). This is not bankroll-optimal and can lead to overbetting or underbetting relative to the edge.

**Why Quarter Kelly:**

| Criterion | Quarter Kelly | Full Kelly | Fixed Fraction | Martingale |
|-----------|--------------|------------|----------------|------------|
| Growth optimality | ~85% of optimal | Optimal | Suboptimal | Negative |
| Drawdown control | Max ~15% | Can hit 50%+ | Depends | Ruin risk |
| Paranoia about edge misspec | Tolerates 25% error | Fragile | N/A | N/A |
| Ease of implementation | Simple formula | Simple formula | Simplest | Simple |

**Kelly criterion:** `f* = (p * b - q) / b` where:
- `p` = probability of winning (from Platt-calibrated probability)
- `q` = 1 - p
- `b` = net odds received on the bet (for binary outcomes: b = 1/(current_price) - 1 for YES side)
- `f*` = fraction of bankroll to bet

**Quarter Kelly** = `0.25 * f*`. This cuts the aggressive full-Kelly stake to 25%, reducing volatility while preserving ~85% of the long-term growth rate.

**Why not full Kelly:** Prediction market probabilities (especially weather) have **edge misspecification risk** — our calibrated probability might be wrong. Quarter Kelly provides a margin of safety. It also limits drawdown to ~15%, which matches the existing `RiskConfig.max_drawdown_pct = 0.15`.

**Mapping to system:**
- Bankroll = total available USDC on the CLOB.
- Each market's position = `QuarterKelly * bankroll`.
- `RiskEngine.assess()` adds a Kelly-derived max position override.
- The existing `max_position_size_usdc` becomes a hard cap above the Kelly-computed size.

### Priority 4: Backtester Upgrade

**Problem:** The current backtester (`backtest_metar_calibrated_path.py`) is a standalone script with hardcoded logic. It does not have a proper framework for:

- Multi-strategy comparison
- Walk-forward validation
- Transaction cost modeling
- Slippage simulation
- Portfolio-level P&L
- Risk metrics (Sharpe, Sortino, max drawdown, Calmar)

**Upgrade plan:**

1. **Backtesting framework** (reusable, not city-specific):
   - Strategy interface: `class Strategy: def run(history, date) → Signal[]`
   - Portfolio sim: tracks cash, positions, P&L with configurable fees and slippage.
   - Walk-forward engine: sliding window training → out-of-sample evaluation.
   - Metric report: Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor, Calmar ratio.

2. **Strategies to test:**
   - `DEBOnlyStrategy` — trade based on DEB raw prediction.
   - `CalibratedDebStrategy` — trade based on DEB + bias correction.
   - `PlattProbabilityStrategy` — trade based on Platt-calibrated probabilities.
   - `KellyStrategy` — position size via Quarter Kelly, exit at settlement.

3. **Data pipeline:**
   - All data lives in SQLite (`daily_records_store`, `truth_records_store`, `training_feature_records_store`, `intraday_path_snapshots_store`).
   - Backtester reads from these stores, not from external APIs.
   - City/date filtering → multi-city portfolio simulation.

---

## 5. Component Selection Rationale (Summary)

| Component | Selected | Rejected Alternatives | Rationale |
|-----------|----------|----------------------|-----------|
| **Raw prediction model** | DEB (Dynamic Ensemble Blending) | Logistic regression, XGBoost, LSTM | Temperature prediction is regression, not classification. DEB is interpretable, lightweight, and exploits the unique multi-model forecast data. |
| **Probability calibration** | Platt scaling | Isotonic regression, beta calibration | Data efficiency (200-500 samples vs 1000+), smooth extrapolation, multiclass via one-vs-rest per bucket. |
| **Probability distribution shape** | Gaussian CDF + Platt | Raw ensemble histogram, Gaussian processes | Gaussian is a reasonable first-order approximation; Platt fixes calibration on top. |
| **Position sizing** | Quarter Kelly | Full Kelly, fixed fraction, Martingale | Balance of growth optimality (~85% of full Kelly) and drawdown control (~15%). Margin of safety for edge misspecification. |
| **Bias correction v3** | Guarded (holdout-validated) bucket calibration | Uniform bias, no correction | Holdout prevents overfitting; guards against switching to bucket-calibration when it doesn't generalize. |
| **State storage** | SQLite (runtime_state.py) | JSON file, PostgreSQL | Single-file deployment, zero infrastructure, fast random access, ACID transactions. |
| **Async infra** | asyncio + ThreadPoolExecutor | Celery, Redis Queue | Lightweight, co-located with FastAPI, sufficient for the IO-bound weather fetching workload. |
| **Auth** | Supabase | Auth0, Firebase, custom JWT | Free tier for early stage, built-in social login and RLS, good Supabase SDK for Next.js. |
| **Payments** | Polygon USDC smart contract | Stripe, PayPal | On-chain settlement matches the Polymarket user base; USDC is the native denomination of prediction markets. |

---

## 6. Key Metrics & Current State

### Prediction Accuracy (approximate, from ops dashboard)

| Metric | Current Value | Target |
|--------|---------------|--------|
| DEB bucket hit rate (all cities) | ~50-60% | 67%+ (high trust tier) |
| DEB MAE (all cities) | ~1.5-2.5° | <1.5° |
| μ (probability center) MAE | ~1.8° | <1.5° |
| Brier score (probability distribution) | ~0.100-0.150 | <0.080 |
| Cities in "high" trust tier | ~10-15 (of 40+) | 25+ |
| Cities in "insufficient" trust tier | ~5-10 | 0 |

### Trading Engine Readiness

| Component | Status | Notes |
|-----------|--------|-------|
| CLOB client | ✅ Complete | Async, retry, rate-limited |
| Wallet manager | ✅ Complete | EIP-712, Polygon mainnet/Amoy |
| Order manager | ✅ Complete | Place, cancel, reconcile |
| Position tracker | ✅ Complete | P&L, exposure, sync |
| Risk engine | ✅ Complete | Hard limits, cooldown, drawdown |
| Signal ingestion | ⚠️ Basic | Threshold-based; to be upgraded with Platt probabilities |
| Position sizing | ❌ Heuristic | To be replaced with Quarter Kelly |
| Live trading | ❌ Disabled | `POLYWEATHER_TRADING_ENABLED=false` by default |

### Data Pipeline Health

| Source | Status | Active Cities |
|--------|--------|--------------|
| Open-Meteo | ✅ Healthy | All 40+ |
| METAR (aviationweather) | ✅ Healthy | All with ICAO |
| AMOS (Korea) | ✅ Healthy | Seoul, Busan |
| AMSC AWOS (China) | ⚠️ Session-dependant | Shanghai, Beijing, etc. |
| MGM (Turkey) | ✅ Healthy | Ankara, Istanbul |
| HKO (Hong Kong) | ✅ Healthy | Hong Kong, Shenzhen |
| NOAA WRH (US) | ✅ Healthy | Istanbul (NOAA-settled), US cities |
| Wunderground | ⚠️ Historical crawler removed | WU-settled cities |

---

## 7. Configuration & Deployment

### Environment Variables (key ones)

| Variable | Purpose | Default |
|----------|---------|---------|
| `POLYWEATHER_TRADING_ENABLED` | Master switch for trading engine | `false` |
| `POLYWEATHER_STATE_STORAGE_MODE` | Storage backend | `sqlite` |
| `POLY_MARKET_MAP` | ICAO → (condition_id, token_id) JSON | `{}` |
| `POLY_TRADING_PRIVATE_KEY` | Trading wallet private key | — |
| `POLY_MAX_POSITION_SIZE_USDC` | Per-market position cap | 500 |
| `POLY_MAX_TOTAL_EXPOSURE_USDC` | Total portfolio exposure cap | 5000 |
| `POLY_MIN_CONFIDENCE` | Minimum signal confidence | 0.6 |
| `POLYWEATHER_AMSC_SESSION_ID` | AMSC AWOS auth session | — |
| `SUPABASE_URL` | Supabase project URL | — |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase admin key | — |

### CI Pipeline (`.github/workflows/ci.yml`)

On push to `main`:
1. `python-quality` — ruff lint + pyright typecheck
2. `frontend-quality` — Next.js lint + typecheck
3. `build-and-push` — Docker build + push to registry
4. `deploy` — Deploy to production server

### Production Endpoints

- `https://api.polyweather.top/healthz` — Health check
- `https://polyweather.top/` — Main frontend
- `https://t.me/PolyWeatherBot` — Telegram bot

---

## 8. Next Steps / Immediate Action Items

1. **Priority 1 — DEB enhancements:** Implement seasonal weight profiles, multi-horizon weights, and per-bucket adaptive bias correction.
2. **Priority 2 — Platt calibration:** Extract training features per bucket, train `LogisticRegression`, run shadow probabilities, compare Brier scores, then switch.
3. **Priority 3 — Quarter Kelly:** Replace `_compute_position_size()` with `QuarterKelly * bankroll`, add bankroll tracking to `RiskEngine`, enforce Kelly-derived caps.
4. **Priority 4 — Backtester upgrade:** Build reusable framework with `Strategy` interface, portfolio sim, walk-forward engine, and risk metrics.
5. **Validation:** Run `backtest_deb_versions.py` after each DEB change, compare version metrics, commit only if guarded calibration beats raw.
6. **Frontend:** Add probability calibration chart to ops training page; add Kelly position size display to trading status.

---

*This CONTEXT.md is a living document. Update it as the architecture evolves.*
