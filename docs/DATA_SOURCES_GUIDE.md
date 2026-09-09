# Using Poly Data & Pendulum Flow with PolyWeather

## Overview

PolyWeather trades Polymarket temperature prediction markets using weather forecast models. Two external data sources can significantly improve backtesting, execution, and market intelligence:

- **poly_data** ([github.com/warproxxx/poly_data](https://github.com/warproxxx/poly_data)) — On-chain trade history for all Polymarket markets
- **Pendulum Flow Archive** ([archive.pendulumflow.com](https://archive.pendulumflow.com/)) — Full orderbook snapshots at microsecond resolution

---

## 1. What poly_data Provides

The poly_data pipeline streams `OrderFilled` events from the Polymarket CTF Exchange V2 contract on Polygon and joins them with market metadata. The output is:

- `data/markets.csv` — All ~1.5M Polymarket markets. Every CLOB market field is preserved; two derived columns are prepended: `id` (= on-chain `condition_id`) and `clobTokenIds` (JSON array — first element = `token1`, second = `token2`).
- `processed/trades.csv` — Every fill (v2 schema, verified against a live `trades.csv` header):

| Field | Use |
|-------|-----|
| `timestamp` | ISO datetime (`2026-09-04T07:54:07.000000`, naive = UTC) — parse with `datetime.fromisoformat`, NOT as float |
| `market_id` | Maps to weather market `condition_id` |
| `maker` / `taker` | Wallet addresses — filter on `maker` (contract emits from the maker's perspective) |
| `nonusdc_side` | `token1` / `token2` — resolve to a CTF token ID via `markets.csv` `clobTokenIds` |
| `maker_direction` / `taker_direction` | BUY or SELL |
| `price` | USDC per outcome token (0–1) |
| `usd_amount` / `token_amount` | Trade size in USD / outcome tokens |
| `transactionHash` | Polygon tx hash |

There is NO per-row asset/token column. Side-correct pricing requires `load_poly_markets()` + `resolve_poly_trade_token()`. In PolyWeather: `scripts/backtest_real_polymarket.py` (`load_poly_trades`, `load_poly_markets`, `pick_poly_data_price(..., market_tokens=...)`, CLI `--poly-trades/--poly-markets`).

---

## 2. What Pendulum Flow Provides

Full orderbook snapshots (bids and asks with sizes) for every Polymarket market, recorded hourly. V3 data is "military grade" — multiple independent recorders, microsecond resolution, deduplicated.

### Relevant format for PolyWeather

V3 schema (verified against `2026-09-09T06.parquet` — the `ts/token_id/side/price/size` layout in earlier drafts does NOT exist). Each hourly parquet file (~900MB — never bulk-download; always filter to one market in DuckDB) carries event rows:

| event_type | Share of hour-06 | Columns used |
|------------|------------------|--------------|
| `price_change` | ~84.5M | Per-level updates (not consumed) |
| `best_bid_ask` | ~9M | `timestamp`, `asset_id`, `best_bid`, `best_ask` (touch stream) |
| `book` | ~164K | `timestamp`, `market`, `asset_id`, `bids[]`, `asks[]` (full depth) |
| `last_trade_price` | ~49K | `timestamp`, `price`, `size`, `side` (prints) |
| `new_market` / `market_resolved` / `tick_size_change` | ~3K | `question`, `slug`, `outcomes`, `assets_ids` (pairing metadata) |

Key facts:
- `market` (BLOB) is shared by the YES/NO pair; `asset_id` (BLOB) is the 32-byte big-endian uint256 = CTF token ID (`BigInt(hex)` — see `asset_hex_to_token_id`).
- `bids`/`asks` are `STRUCT(price, size)[]` (up to ~99 levels/side observed; can be empty). `best_bid`/`best_ask` are NULL on `book` rows and vice versa.
- In PolyWeather: `src/trading/polymarket/pendulum_book.py` (`book_rows_to_snapshots`, `fill_buy_asks`/`fill_sell_bids` VWAP, `fok_fill_rates`, `export_market_books`).

---

## 3. Improvements for PolyWeather

### 3.1 Backtesting: Replace CLOB Price History

**Current**: `scripts/backtest_real_polymarket.py` fetches CLOB `/prices-history` at 1h resolution, then falls back to Data API `/trades` for closed markets. This gives point prices — no depth, no fill quality.

**With poly_data**:
- Load `processed/trades.csv` and filter by weather market `condition_id`
- Every fill is available — no CLOB purging problem for closed markets
- Exact fill prices at exact timestamps
- Can analyze all weather markets at once, not one-by-one API calls

**Implementation** (streaming, OOM-safe — never preload the full file):
```python
poly_trades = load_poly_trades("data/poly_trades.csv", wanted_condition_ids)
market_tokens = load_poly_markets("data/poly_markets.csv")
price = pick_poly_data_price(trades, token_id, cutoff_ts=cut,
                             market_tokens=market_tokens.get(condition_id))
```

### 3.2 Backtesting: Slippage & Fill Quality

**Current**: Backtest assumes point-price fills — buy at the last observed price, no slippage.

**With Pendulum Flow**:
- Export one market's `book` rows with DuckDB (hour files are ~900MB — always filter by `market`), convert to snapshots, then VWAP-fill (see `pendulum_book.py`)
- Model realistic fills: "if I buy $50 of YES tokens, what price do I actually get at?"
- Calculate slippage at different order sizes (`fok_fill_rates` for 5–100 shares)
- Test whether DEB signal confidence should adjust position size

**Implementation** (real V3 — `WHERE event_type='book' AND market=`, not `token_id = ?`):
```sql
COPY (
  SELECT epoch_ms(timestamp) AS ts_ms, hex(market) AS market,
         hex(asset_id) AS asset, bids, asks
  FROM '2026-09-09T06.parquet'
  WHERE event_type = 'book' AND market = unhex('<marketHex>')
  ORDER BY timestamp
) TO 'rows.json' (FORMAT JSON);
```
```bash
python scripts/export_pendulum_books.py rows.json snapshots.jsonl \
  --yes <yesAssetHex> --no <noAssetHex> [--max-levels 10]
```
In PolyWeather the backtester prefers a real ladder when the record carries `metadata["asks"]` (`apply_book_slippage` → `fill_source="book_vwap"`) and otherwise falls back to the bps proxy (`slippage_bps` per $100, sqrt when `orderbook_depth_usd` is set). Find a market's hex + asset hexes via its `new_market` row (`assets_ids`, `question`, `slug`), or Gamma decimal IDs → hex.

### 3.3 Execution Risk Analysis

**Current**: `src/trading/engine/risk_engine.py` sets max position sizes but doesn't model market impact.

**With Pendulum Flow**:
- Before placing a trade, check the live orderbook depth at `archive.pendulumflow.com`
- If the book is thin (e.g., < $20 at the best price), widen slippage tolerance or reduce size
- Alert when weather markets have low liquidity (common for niche temperature buckets)

### 3.4 Market Discovery & Liquidity Screening

**Current**: Weather markets are discovered via Gamma API search. No pre-filtering for liquidity.

**With poly_data**:
- `markets.csv` contains all ~1.5M markets — filter for weather-tagged ones
- Join with `trades.csv` to rank markets by recent USD volume
- Focus DEB analysis and trading on liquid temperature buckets only

### 3.5 Whale & Institutional Flow Detection

**Current**: No visibility into who is trading weather markets.

**With poly_data**:
- Aggregate `trades.csv` by `maker` address for weather market IDs
- Identify if a single wallet is dominating one side of a temperature bucket
- Detect institutional buying before forecast model updates (potential information leakage)

### 3.6 Model Calibration

**Current**: DEB produces a Gaussian-CDF probability per bucket. Calibration uses Platt scaling on historical predictions.

**With poly_data**:
- Compare DEB model probabilities against actual market prices over time
- Track if market prices are consistently over/under the model's implied probability
- Use the discrepancy as a signal (market may be mispricing due to weather model lag)

---

## 4. Data Pipeline

```
poly_data (processed/trades.csv)     Pendulum Flow (hourly parquet)
          |                                    |
          v                                    v
   Weather market filter              Orderbook depth lookup
          |                                    |
          v                                    v
   Historical price series       Slippage model + fill simulation
          |                                    |
          +------------------------------------+
                           |
                           v
              Enhanced backtest engine
                           |
                           v
                  DEB signal evaluation
                           |
                           v
               Position sizing + risk
```

---

## 5. Setup

### poly_data

```bash
cd /path/to/poly_data
# Set HYPERSYNC_API in .env (free tier from envio.dev)
uv sync
uv run poly-data  # full backfill, resumable
```

Output: `processed/trades.csv` — copy or symlink to PolyWeather `data/`.

### Pendulum Flow

Hour files are ~900MB — do NOT bulk-download 24h. Query remotely or fetch one hour, always filtering to a single market in DuckDB (no auth required):

```bash
# Option A: query the archive directly (DuckDB httpfs, partial reads)
duckdb -c "COPY (SELECT epoch_ms(timestamp) AS ts_ms, hex(market) AS market,
  hex(asset_id) AS asset, bids, asks FROM
  'https://archive.pendulumflow.com/v3/2026-09-09/06/2026-09-09T06.parquet'
  WHERE event_type = 'book' AND market = unhex('<marketHex>')
  ORDER BY timestamp) TO 'rows.json' (FORMAT JSON);"

# Option B: download one hour first (~900MB), then same SELECT locally
curl -O "https://archive.pendulumflow.com/v3/2026-09-09/06/2026-09-09T06.parquet"
```

### Integration

```bash
# In PolyWeather project root
ln -s /path/to/poly_data/processed/trades.csv data/poly_trades.csv
ln -s /path/to/poly_data/data/markets.csv data/poly_markets.csv
```

---

## 6. Priority

| Task | Impact | Effort | Source |
|------|--------|--------|--------|
| Replace CLOB price history with poly_data trades | High | Low | poly_data |
| Add slippage modeling to backtest | High | Medium | Pendulum Flow |
| Weather market liquidity screening | Medium | Low | poly_data |
| Whale flow detection in weather markets | Medium | Low | poly_data |
| Orderbook depth check before execution | High | Medium | Pendulum Flow |
| Model calibration against market prices | Medium | High | poly_data + Pendulum Flow |
