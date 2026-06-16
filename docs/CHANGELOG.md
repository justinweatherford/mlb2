# Changelog

All notable changes to this project are recorded here.
Entries are grouped by date and summarize what changed and why.

---

## 2026-06-16

### Fast Kalshi Market Data Fix v1

Root cause: sequential REST polling (422 markets × ~620ms/request ≈ 262s/cycle)
and WebSocket data writing to the wrong table (split-brain).

**`kalshi/normalizer.py`**
- Added `_WS_SOURCE_MAP` constant mapping WS message types to source labels
- Added `_bridge_ws_to_orderbook_snapshots()` — bridges WS prices into
  `kalshi_orderbook_snapshots` on every `ticker` and `orderbook_delta` message
- Extended market lookup in `normalize_and_insert()` to include `market_type`,
  `home_team`, `away_team`, `game_pk` for enrichment of snapshot rows

**`kalshi/ws_client.py`**
- Fixed `_PROD_WS` URL: `api.elections.kalshi.com` → `external-api-ws.kalshi.com`
  (documented production WS hostname per Kalshi API docs)
- Lowered `_MAX_TICKERS_PER_BATCH` from 200 to 100 (Kalshi error code 26 fires
  above the documented subscription limit)

**`kalshi/client.py`**
- Added `get_orderbooks_batch(tickers)`: batch orderbook endpoint
  `GET /markets/orderbooks?tickers=T1&tickers=T2...` (max 100 tickers/call)
- Fixed `get_market_trades()`: endpoint was `/markets/{ticker}/trades` (404);
  correct path is `/markets/trades` with `ticker` as a query param

**`kalshi/orderbook_recorder.py`**
- Updated `_extract_orderbook_levels()` to handle `orderbook_fp` batch response
  format (`yes_dollars`/`no_dollars` keys alongside existing `yes`/`no`)
- Added `poll_once_batch()`: chunks markets into groups of 100, calls
  `get_orderbooks_batch()`, writes with `source='rest_batch'`; error per batch,
  not per ticker

**`kalshi_orderbook_recorder.py`**
- Added `--batch` CLI flag to use `poll_once_batch()` instead of sequential
  `poll_once()`; existing default behavior unchanged

**`kalshi/market_trades.py`**
- Updated log display string to reflect correct endpoint path

**Tests**
- `tests/test_kalshi_ws.py`: +7 tests (WS URL constant, batch size constant,
  bridge writes, skipped messages, no-price skip, last-price-only bridge)
- `tests/test_orderbook_recorder.py`: +16 tests (orderbook_fp format, batch
  parse, poll_once_batch, get_orderbooks_batch, trades endpoint path)
- Total: 3476 passed (was 3452)

---

## 2026-06-15 — 2026-06-16

### Market Liveness Validator — Bug Fixes

**`market_liveness_validator.py`**

**Bug 1 — `stale_despite_score_change` reported 0 for all spread tickers**

Root cause 1: heuristic (`unique_mid_count <= 2 AND mid_range <= 3`) was too
strict. DET+2 had unique_mids=3 and range=44¢ but was stale for 454 minutes.

Root cause 2: `_score_at()` compared UTC snapshot timestamps against ET game
state timestamps (no timezone conversion), so string comparison
`"2026-06-16..." vs "2026-06-15..."` always put all game states before any
snapshot, returning the last state for every timestamp.

Fix 1: Added `late_moving = longest_stale_period_minutes >= 60` as an OR
condition alongside the existing frozen-book heuristic.

Fix 2: Added `_gs_ts_to_utc()` helper to convert game state `checked_at` (ET)
to UTC before comparison in `_score_at()`.

Result: 18 confirmed stale spread tickers (was 0), 76/76 tests pass.

---

### Kalshi API Architecture Audit

Research-only audit of Kalshi market data latency.

Outputs (in `outputs/kalshi_api_audit/`):
- `kalshi_api_architecture_audit.md` — root cause analysis, decision
- `kalshi_endpoint_inventory.csv` — all endpoints, correct vs current usage
- `kalshi_rate_limit_math.md` — token budget math for sequential vs batch vs WS
- `recommended_live_capture_architecture.md` — hybrid Option 3 design
- `implementation_plan_for_fast_market_data.md` — step-by-step plan

Key findings:
- Sequential REST: 422 calls × 620ms = 262s/cycle root cause confirmed
- WS split-brain: WS data in `kalshi_market_updates`, reports read
  `kalshi_orderbook_snapshots` — zero overlap
- WS URL discrepancy discovered
- Batch endpoint confirmed: `GET /markets/orderbooks` up to 100 tickers/call

---

## 2026-06-14 — 2026-06-15

### Kalshi Orderbook Recorder v1

- `kalshi_orderbook_recorder.py` + `kalshi/orderbook_recorder.py`
- Sequential REST polling of all open MLB markets
- `kalshi_orderbook_snapshots` table with enrichment columns (sport, market_type,
  home_team, away_team, game_pk, yes_bid, yes_ask, spread_cents, mid_cents, source)
- `focused_tape_watcher.py` for candidate-level depth polling at 7s interval

### Market Liveness Validator v1

- `market_liveness_validator.py`
- Per-ticker metrics: snapshot cadence, unique mids, mid range, stale periods
- Per-type CSV + recommended priority report
- Baseline result: 47% live_responsive, 50% stale; team_total best lane

---

## Earlier (Phase 1 Core Pipeline)

- Discord ingestion → game state parsing → paper position lifecycle
- Signal funnel (`signal_funnel_events` table)
- Setup-level outcome reconciliation
- Conservative execution model (taker fee, 85¢ max chase)
- FastAPI read-only layer
- React dashboard (Overview, Signals, Positions, Health)
- Team-total line parsing cleanup
- Context usage audit
