# Fast Kalshi Market Data Fix v1 — Implementation Summary
_Completed 2026-06-16. Read-only changes only. No orders. No trading behavior changed._

---

## What Was Done

Five independent changes across five files, plus new tests:

### Part A — WebSocket → `kalshi_orderbook_snapshots` bridge (`kalshi/normalizer.py`)

**Problem:** The WebSocket collector wrote sub-second price data to `kalshi_market_updates`, but every analysis module (liveness validator, candidate generator, spread research) reads exclusively from `kalshi_orderbook_snapshots`. The WS data was siloed and invisible to analysis.

**Fix:** Added `_bridge_ws_to_orderbook_snapshots()` in `normalizer.py`. On every `ticker` or `orderbook_delta` WS message that contains price data, a normalized row is now written to `kalshi_orderbook_snapshots` in addition to `kalshi_market_updates`.

- `ticker` messages → `source = 'ws_ticker'`
- `orderbook_delta` messages → `source = 'ws_orderbook'`
- Messages with no price data (no yes_bid, no yes_ask, no last_price) are skipped
- Enrichment fields (event_ticker, market_type, home_team, away_team, game_pk) pulled from `kalshi_markets` via the existing market lookup
- `spread_cents` and `mid_cents` computed when both sides present

**Expected result:** Median snapshot cadence drops from ~265s to <5s for active markets.

---

### Part B — WebSocket URL and batch size fix (`kalshi/ws_client.py`)

**Problem 1:** `_PROD_WS` pointed to `wss://api.elections.kalshi.com/trade-api/ws/v2` — an undocumented hostname that may not route correctly. The documented production WS URL is `wss://external-api-ws.kalshi.com/trade-api/ws/v2`.

**Fix:** Updated constant to documented URL.

**Problem 2:** `_MAX_TICKERS_PER_BATCH = 200` exceeded the Kalshi-documented limit. Subscriptions with >100 tickers trigger error code 26 and may be silently rejected.

**Fix:** Lowered to `_MAX_TICKERS_PER_BATCH = 100`.

---

### Part C — Batch REST orderbook endpoint (`kalshi/client.py`, `kalshi/orderbook_recorder.py`, `kalshi_orderbook_recorder.py`)

**Problem:** Sequential REST polling (422 markets × ~620ms/request = ~262s/cycle) was the root cause of 4.4-minute snapshot cadence.

**Fix 1 — `kalshi/client.py`:** Added `get_orderbooks_batch(tickers: list[str]) -> dict[str, dict]`:
- `GET /trade-api/v2/markets/orderbooks?tickers=T1&tickers=T2...` (repeated params)
- Raises `ValueError` if `len(tickers) > 100`
- Returns `{ticker: orderbook_fp_dict}`

**Fix 2 — `kalshi/orderbook_recorder.py`:** Updated `_extract_orderbook_levels()` to handle `{"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}}` batch response format, alongside the existing nested and flat formats.

Added `poll_once_batch()`:
- Chunks markets into groups of 100
- 422 markets → 5 REST calls per sweep (vs 422 sequential)
- Uses `source = 'rest_batch'`
- Per-batch error handling (one failed batch doesn't abort the cycle)

**Fix 3 — `kalshi_orderbook_recorder.py`:** Added `--batch` flag. Existing sequential mode (`poll_once`) is unchanged and remains the default.

Run batch mode:
```
python kalshi_orderbook_recorder.py --sport mlb --batch --interval-seconds 30
```

**Expected result:** REST sweep time drops from ~262s to ~1s. With 30s sleep, cadence = ~31s/tick. REST token consumption drops from ~67 tokens/sec to ~1.7 tokens/sec (99% reduction).

---

### Part D — Trades endpoint fix (`kalshi/client.py`, `kalshi/market_trades.py`)

**Problem:** `get_market_trades()` called `GET /markets/{market_ticker}/trades` which returns 404. The correct endpoint is `GET /markets/trades?ticker={market_ticker}`.

**Fix — `kalshi/client.py`:** Changed `_request("GET", f"/markets/{market_ticker}/trades")` to `_request("GET", "/markets/trades", params)` with `params["ticker"] = market_ticker`.

**Fix — `kalshi/market_trades.py`:** Updated log display string from `/markets/{ticker}/trades` to `/markets/trades?ticker={ticker}` to match the actual request.

---

## Tests Added

**`tests/test_kalshi_ws.py`** — 7 new tests:
- `test_prod_ws_url_is_external_api` — verifies URL constant
- `test_max_tickers_per_batch_is_100` — verifies batch size constant
- `test_ticker_message_writes_orderbook_snapshot` — bridge, prices, source, market_type
- `test_orderbook_delta_writes_orderbook_snapshot` — bridge for orderbook_delta channel
- `test_skipped_messages_do_not_write_snapshot` — control messages skipped
- `test_ticker_no_prices_skips_snapshot` — no-price ticker skipped
- `test_ticker_last_price_only_writes_snapshot` — last_price-only ticker bridges

**`tests/test_orderbook_recorder.py`** — 16 new tests:
- `TestExtractOrderbookLevelsBatchFp` (4 tests) — handles `orderbook_fp`, `yes_dollars`/`no_dollars`
- `TestParseSnapshotBatchFp` (2 tests) — correct price parsing for batch format
- `TestPollOnceBatch` (6 tests) — batch polling, chunking, error handling, source field
- `TestGetOrderbooksBatch` (3 tests) — ValueError >100, empty input, correct query string
- `TestGetMarketTradesEndpoint` (2 tests) — path fix, display string fix

**Test result: 3476 passed, 0 failed** (up from 3452 prior to session).

---

## Files Changed

| File | Change |
|------|--------|
| `kalshi/normalizer.py` | Added `_WS_SOURCE_MAP`, `_bridge_ws_to_orderbook_snapshots()`, bridge call in `normalize_and_insert()`, extended market lookup query |
| `kalshi/ws_client.py` | `_PROD_WS` URL corrected; `_MAX_TICKERS_PER_BATCH` 200→100 |
| `kalshi/client.py` | Added `get_orderbooks_batch()`; fixed `get_market_trades()` path |
| `kalshi/orderbook_recorder.py` | Updated `_extract_orderbook_levels()` for batch fp format; added `poll_once_batch()` |
| `kalshi_orderbook_recorder.py` | Added `--batch` CLI flag; imports `poll_once_batch` |
| `kalshi/market_trades.py` | Updated log display string to correct endpoint |
| `tests/test_kalshi_ws.py` | +7 tests |
| `tests/test_orderbook_recorder.py` | +16 tests |

**No changes to:** order placement, candidate generation, signal thresholds, paper logic, focused tape watcher, live watcher, schema.

---

## Validation Queries (run after next game day)

```sql
-- Confirm WS bridge is writing rows
SELECT source, COUNT(*) FROM kalshi_orderbook_snapshots GROUP BY source;
-- Expected: ws_ticker rows appear alongside rest_poll (and rest_batch if --batch used)

-- Check WS session health
SELECT started_at, ended_at, msg_count, status
FROM kalshi_ws_sessions ORDER BY id DESC LIMIT 5;
-- msg_count should be non-zero and growing during active games

-- Verify trades endpoint now returns data
SELECT COUNT(*) FROM kalshi_market_trades WHERE fetched_at > date('now', '-1 day');
```

---

## What Stays the Same

The REST sequential poller (`poll_once`) remains default behavior. No automatic switchover. Batch mode is opt-in via `--batch`. The WS collector (`kalshi_ws.py`) continues writing to `kalshi_market_updates` unchanged — the bridge adds a second write path, it doesn't replace the first.
