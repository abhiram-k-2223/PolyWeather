# PolyWeather — Project Context & Paper-Trading Readiness

> Purpose: full-context handoff doc. Paste / attach in a fresh session to continue work.
> Date: 2026-09-26. Branch state: `main` on fork `abhiram-k-2223/PolyWeather` includes
> commit `01f64c38` ("poly_data v2 loader, Pendulum V3 book VWAP, markets.csv discovery").

---

## 1. What PolyWeather is

PolyWeather trades Polymarket temperature prediction markets using weather forecast models.
Core differentiators (do NOT reposition as a general weather API):

- Settlement-source priority, real-time observation sources
- Runway/city-level granular temperature
- SSE patches, Telegram cache reading
- Interpretation oriented toward trading / prediction markets

Product direction: public packaging, educational content, chart completeness, and paid
tiers may grow — but the focus is trading temperature markets, not selling an API.

### Repo layout

```
frontend/            Next.js + React + TS (`npm run test:business`, `npm run typecheck`)
src/
  analysis/          DEB algorithm, Platt calibration, settlement, trend engine
  trading/
    polymarket/      clob_client.py, data_api_client.py, gamma_client.py,
                     neg_risk_adapter.py, wallet.py, pendulum_book.py (new)
    engine/          trading_engine.py, signal_ingestion.py, risk_engine.py,
                     kelly_sizing.py, order_manager.py, position_tracker.py,
                     paper_trade_store.py (UNWIRED — see §4)
    storage/         trade_store.py (SQLite persistence)
  utils/             config_loader.py, telegram_push.py, ...
  onchain/           empty (whale detection lives in backtest script, not here)
  data_collection/   weather sources (~1700-line weather_sources.py + per-network sources)
web/                 FastAPI service (app_factory.py, services/trading_api.py, routes)
scripts/
  backtest_real_polymarket.py   real-price DEB backtest (Gamma discovery + price fetch)
  backtester/        offline sim framework (base.py, engine.py, run.py, report.py,
                     strategies/forecast_gap.py)
  fit_platt_calibration.py, fetch_history.py, ...
tests/               354 passing (venv: `.venv/bin/python -m pytest`)
data/                SQLite DB, backtest records/reports, snapshots (runtime artifacts)
docs/DATA_SOURCES_GUIDE.md     external-data guide (poly_data + Pendulum Flow)
```

### Key data flows

```
Weather obs/collector → analysis (DEB) → SignalIngestor → TradingEngine.process_signal()
    → RiskEngine.assess() → Kelly sizing → OrderManager.place_order() → CLOB
                                                              (NO paper branch today)
Backtest: Gamma discovery → price fetch (poly_data/CLOB/Data-API) → walk-forward DEB
    → scripts/backtester/engine.py sim → report
```

### Conventions (from AGENTS.md)

- Default to English. Verify locally before commit/push; check GitHub Actions after push.
- Python: `python -m ruff check .`, `python -m pytest`. Frontend: `npm run test:business`, `npm run typecheck`.
- Pushing `main` triggers CI (`python-quality`, `frontend-quality`, `build-and-push`, `deploy`).
- Smoke checks: `https://api.polyweather.top/healthz`, `https://polyweather.top/`.
- One Thread per task; don't bundle unrelated work.

---

## 2. Recently completed work (commit `01f64c38`, 10 files, +1620/−27)

Implemented `docs/DATA_SOURCES_GUIDE.md` §§3.1–3.6, corrected against the real
external schemas (poly_data **v2** columns, Pendulum **V3** event-table format):

| Guide § | What was built | Where |
|---|---|---|
| 3.1 poly_data price history | `load_poly_trades()` w/ ISO-ts parse (`_parse_poly_ts`), `taker`/`taker_direction`/`nonusdc_side` support, condition-ID filter; `pick_poly_data_price()` w/ token filter + opposite-side guard via `markets.csv`; fallback chain poly_data → CLOB → Data API | `scripts/backtest_real_polymarket.py:90` |
| 3.1 markets.csv discovery | `load_poly_markets()` (clobTokenIds → token1/token2), `resolve_poly_trade_token()`, `discover_weather_markets_from_poly()`; `--poly-markets` CLI, `DEFAULT_POLY_MARKETS` | same file |
| 3.2 slippage / fill quality | `compute_slippage_fraction()`, `apply_slippage_to_price()` (bps proxy); **`pendulum_book.py`**: `parse_book_levels`, `fill_buy_asks`/`fill_sell_bids` (VWAP), `fok_fillable`, `book_top_depth_usd`, `book_rows_to_snapshots`, `pendulum_export_sql`; engine `apply_book_slippage()` records `fill_source: book_vwap` | `src/trading/polymarket/pendulum_book.py`, `scripts/backtester/engine.py:56` |
| 3.3 execution risk | `RiskConfig.min_orderbook_depth_usd=0`, `slippage_bps_per_100usd=0` (opt-in, default-off); `estimate_slippage_bps()`; `assess(..., expected_slippage_bps, orderbook_depth_usd)` enforces existing `max_slippage_bps=50` | `src/trading/engine/risk_engine.py:50` |
| 3.4 liquidity screening | `compute_liquidity_from_poly_data()` (`reference_ts` = max trade ts, `total_volume_usd`); `--min-volume-usd` filter | `backtest_real_polymarket.py:170` |
| 3.5 whale flow | `detect_whale_flow()` maker aggregation (skips empty makers); `--whale-threshold`; metadata attached only on alert | same file `:222` |
| 3.6 calibration | `model_vs_market_calibration(joined, n_bins)` pure fn | same file `:894` |

Critical bug fixed along the way: real `trades.csv` uses ISO datetime strings and
`nonusdc_side` (token1/token2) with **no `asset` column** — the old loader
float-parsed timestamps, silently skipping *all* real rows and mixing sides.

Backwards-compat preserved: `slippage_bps=0` default (legacy point-price results
unchanged), risk additions kwargs-only, `run.py` keeps pre-existing `true_prob`
behavior untouched.

Validators at commit time: `ruff` clean on all touched files; 30 targeted + 354
full-suite tests passed under `.venv/bin/python` (system python lacks deps like
`loguru` — **always use `.venv/bin/python`**).

### New tests added

- `tests/test_real_polymarket_backtest.py`: CSV filter/bad rows, ISO-ts parse,
  token/cutoff + side resolution, liquidity era, whale concentration, calibration bins,
  markets.csv discovery.
- `tests/test_trading_engine_execution.py`: risk opt-in depth/slippage, backtester
  slippage default-off, engine book-VWAP path, `settle_matched_position` PnL/cooldown.

---

## 3. Paper-trading readiness verdict: NOT READY

Backtests simulate fine. But there is **no safe paper execution path** — enabling the
engine today yields live signed CLOB orders or (by default) nothing at all.

### What works (no action needed)

- **Risk engine** (`src/trading/engine/risk_engine.py:103`): confidence, position-size,
  total-exposure, order-count, daily-limit, post-loss cooldown, drawdown,
  orderbook-depth + slippage checks. Tested.
- **Order lifecycle + reconcile** (`src/trading/engine/order_manager.py:92,181`):
  fills-verified `MATCHED`; `CLOSED_UNVERIFIED` instead of fabricating wins;
  `on_order_closed` hook feeds risk accounting.
- **Kelly sizing** (`src/trading/engine/kelly_sizing.py`, quarter-Kelly via
  `trading_engine.py:402`).
- **Backtester** with bps-proxy + book-VWAP slippage (`scripts/backtester/`).
- **Kill switches**: `POLYWEATHER_TRADING_ENABLED` defaults `false`
  (`web/services/trading_api.py:37`); engine won't init without
  `POLY_TRADING_PRIVATE_KEY` (`trading_api.py:63`).

### Blockers

**B1. No paper mode in the execution path (the big one).**
`TradingEngine.process_signal()` (`src/trading/engine/trading_engine.py:225–297`)
unconditionally calls `OrderManager.place_order()` → `CLOBClient.place_order()` —
real signed orders. `OrderManager` has no dry-run/paper branch.
`PaperTradeStore` (`src/trading/engine/paper_trade_store.py`) is **dead code**:
imported by nothing, never instantiated outside tests. It is in-memory only
(no persistence) and has no fill simulation.

**B2. Nothing feeds the engine.**
`process_weather_signal` / `process_weather_observation`
(`web/services/trading_api.py:186,217`) have **zero production callers**.
App startup (`web/app_factory.py:57–61`) only calls `start_trading_engine()`,
whose background loop (`trading_engine.py:356`) only polls `signal_callback`,
which is never set. Default `POLY_MARKET_MAP` is empty → `SignalIngestor`
returns `[]` for every city regardless.

**B3. Nothing settles positions.**
`settle_matched_position` (`trading_engine.py:202`) has **zero production callers**
(only a docstring mention + test). Positions would open and never close, so realized
P&L, loss cooldown, and drawdown would never engage on real outcomes.

**B4. Signals are placeholders, not DEB-driven.**
`SignalIngestor` (`src/trading/engine/signal_ingestion.py:217–288`) uses fixed
thresholds: temp > 35 °C → BUY @ hardcoded 0.65, wind gust > 60, storm keywords.
No live market-price fetch, no edge-vs-price comparison, DEB probability never
enters signal metadata — so Kelly sizing degrades to a confidence fallback
(`trading_engine.py:402–424`). Note `probabilities > 50±15` path exists
(`signal_ingestion.py:290`) but still prices off `prob/100*0.9`, not the book.

**B5. Supporting gaps.**
`city_to_market_map` populated only via `POLY_MARKET_MAP` env JSON
(`trading_api.py:42`) — no docs/ops runbook for maintaining it as daily markets
roll. `TradeStore` persistence is wired into `OrderManager` optionally but the web
service never passes it (`trading_api.py:147` constructs engine without storage).
No paper-P&L surface on `/api/trading/status`.

---

## 4. Implementation plan (in order — each step independently testable)

### Step 1 — `paper_mode` execution branch (unblocks everything else)

- Add `paper_mode: bool = False` to `EngineConfig` (`trading_engine.py:38`).
- Add paper branch in `OrderManager.place_order()`: when paper, skip
  `self._clob.place_order`, immediately mark `OPEN` (or simulate fill via
  `pendulum_book.fill_buy_asks` / book top), persist to store.
- Instantiate `PaperTradeStore` inside `TradingEngine` (or accept via ctor) and
  route `log_trade` / `record_settlement` / `cancel_trade` through it in paper mode.
- Add `POLY_PAPER_MODE` env passthrough in `web/services/trading_api.py`
  (`_build_engine_config`), default `true` when enabled — paper must be the default,
  live must require explicit opt-in.
- Tests: paper order never touches CLOB (mock asserts `place_order` not called),
  store records OPEN → SETTLED_WON/LOST with correct simulated P&L math.
- Acceptance: with `POLYWEATHER_TRADING_ENABLED=1 POLY_PAPER_MODE=1` and no private
  key, engine processes a synthetic signal end-to-end with zero network writes.

### Step 2 — Feed signals (market map + collector hook)

- Document `POLY_MARKET_MAP` format + refresh procedure (daily temp markets expire;
  map goes stale). Prefer: small resolver that maps ICAO → today's condition via
  Gamma `resolve_city_markets()` (`src/trading/polymarket/gamma_client.py:282`)
  or `discover_weather_markets_from_poly()`, instead of hand-maintained JSON.
- Wire one caller: collector/analysis service → `process_weather_observation()`
  (or set `signal_callback`). Start with 2–3 cities.
- Tests: unmapped ICAO → no signals; mapped → signals flow to `process_signal`.
- Acceptance: engine `signals_processed` counter increments on live weather refresh,
  `orders_failed` stays 0 for mapping reasons.

### Step 3 — Settlement loop

- Background or cron job: for each open paper position, check resolution via Gamma /
  Data API on market close → call `settle_matched_position(token_id, payout)` →
  `PaperTradeStore.record_settlement` → `RiskEngine.record_trade(realized)`.
- Surface paper P&L in `trading_status()` (`trading_api.py:262`) — add
  `paper: {open, settled, win_rate, total_pnl_usdc}` from `PaperTradeStore.get_stats()`.
- Tests: settle win/loss math, cooldown triggers after settled loss, status payload shape.
- Acceptance: full signal → fill → settle cycle visible via `/api/trading/status`
  with correct P&L.

### Step 4 — DEB-driven signals (replace placeholders)

- Replace one threshold (temperature anomaly) with edge logic:
  `edge = DEB_probability − market_price`; BUY only if `edge > threshold`
  (backtester already uses this pattern — `strategies/forecast_gap.py`, default 8%).
- Fetch live market price (CLOB `get_best_price` / midpoint,
  `gamma_client.py:229,260`) at signal time; attach `model_probability`,
  `market_price`, `gap` to signal metadata (Kelly + `PaperTradeStore` already expect
  these fields).
- Optionally use `model_vs_market_calibration()` output to gate buckets where the
  model is historically miscalibrated.
- Tests: no-edge → HOLD / size 0; edge → BUY with metadata; Kelly size matches
  `compute_kelly_size_from_signal`.
- Acceptance: backtest edge distribution reproduced live on paper for ≥ 1 week
  before considering live.

### Step 5 — Persistence + ops hardening

- Pass `TradeStore` into engine in `trading_api.py:147`; persist paper trades to
  SQLite (extend `PaperTradeStore` with DB backend or mirror into `TradeStore`).
- Persist `RiskEngine` counters (daily trades, cooldown, peak portfolio) across
  restarts — currently in-memory, reset on deploy.
- Add ops runbook: env vars, market-map refresh, how to verify no live orders
  (CLOB reads only), alerting on `orders_failed` spike.
- Acceptance: restart web service mid-paper-session → positions, P&L, and risk
  state survive.

### Step 6 — Go-live gate (explicit decision, not default)

- Require `POLY_PAPER_MODE=0` + private key + `POLYWEATHER_TRADING_ENABLED=1`
  simultaneously; log a loud startup banner in live mode.
- Start live with Step-4 signals only, `POLY_MAX_POSITION_SIZE_USDC` small,
  `POLY_MIN_CONFIDENCE` high (≥ 0.75), 1–2 cities.
- Acceptance criteria: ≥ 2 weeks paper with positive edge net of simulated
  slippage (book-VWAP), all Steps 1–5 green, conscious human sign-off.

---

## 5. Environment & commands cheat sheet

```bash
cd /home/abhiram/projects/PolyWeather
git status --short                        # confirm clean before work
.venv/bin/python -m ruff check .          # lint (ALWAYS use .venv python;
.venv/bin/python -m pytest -q             # full suite (~354 tests)  system python lacks deps)
cd frontend && npm run test:business && npm run typecheck
```

Relevant env vars (see `web/services/trading_api.py:37–107`):

| Var | Default | Meaning |
|---|---|---|
| `POLYWEATHER_TRADING_ENABLED` | `false` | master kill switch |
| `POLY_TRADING_PRIVATE_KEY` | — | wallet key; engine won't init without it |
| `POLY_MARKET_MAP` | `{}` | JSON ICAO → (condition_id, token_id) |
| `POLY_MAX_POSITION_SIZE_USDC` | `500` | per-market cap |
| `POLY_MAX_TOTAL_EXPOSURE_USDC` | `5000` | portfolio cap |
| `POLY_MAX_ORDER_COUNT` / `POLY_MAX_DAILY_TRADES` | `10` / `50` | order/trade caps |
| `POLY_MIN_CONFIDENCE` | `0.6` | signal gate |
| `POLY_COOLDOWN_SEC` / `POLY_MAX_DRAWDOWN` | `300` / `0.15` | loss cooldown / drawdown |
| `POLY_PAPER_MODE` | *(new — Step 1)* | paper execution branch |

Backtest entry points (already working, no changes needed to start):

```bash
.venv/bin/python scripts/backtest_real_polymarket.py --help
.venv/bin/python scripts/backtester/run.py --help
```

Useful file references:

- Engine: `src/trading/engine/trading_engine.py` (process_signal `:225`, loop `:356`)
- Risk: `src/trading/engine/risk_engine.py` (assess `:103`)
- Orders: `src/trading/engine/order_manager.py` (place `:92`, reconcile `:181`)
- Signals: `src/trading/engine/signal_ingestion.py` (thresholds `:217`)
- Paper store (unwired): `src/trading/engine/paper_trade_store.py`
- Web wiring: `web/services/trading_api.py`, `web/app_factory.py:57`
- Book math: `src/trading/polymarket/pendulum_book.py`
- Strategy pattern to copy: `scripts/backtester/strategies/forecast_gap.py`

---

## 6. Suggested fresh-session prompt

> PolyWeather repo at `/home/abhiram/projects/PolyWeather` (fork `abhiram-k-2223`,
> branch `main` @ `01f64c38`). Read `docs/PAPER_TRADING_READINESS.md` (this file)
> and implement **Step N** only. Constraints: use `.venv/bin/python` for all Python
> commands; run `ruff check` + targeted `pytest` before finishing; default-off /
> backwards-compatible changes; focused tests for new pure logic; `git status --short`
> to confirm scope, do not commit unless asked.
