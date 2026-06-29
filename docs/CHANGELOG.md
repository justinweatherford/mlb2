# Changelog

All notable changes to this project are recorded here.
Entries are grouped by date and summarize what changed and why.

---

## 2026-06-21 (continued)

### Slate Monitor UI v1

Light read-only observer page for same-day slate validation.

**`api/routers/slate_monitor.py`** (new)
- `GET /api/mlb/slate-monitor?date=` — reads 3 output directories via stdlib `csv`
- Returns: snapshot health summary + per-type breakdown, 7 brain candidate lists
  (filtered by `game_date`), EV overlay rows (filtered by `game_date`), error map
- No DB writes. No candidate generation. No paper entries. No order actions.
- `_build_health_summary()`: counts fresh/recent/stale/empty/missing, computes
  priority fresh%, builds by-type breakdown for 5 priority market types
- Empty/missing files return friendly error strings (not exceptions)

**`api/main.py`**
- Added `slate_monitor` router import + `include_router` call

**`frontend/src/types/api.ts`**
- Added `SlateMonitorHealthByType`, `SlateMonitorHealthSummary`, `SlateMonitorResponse`

**`frontend/src/api/client.ts`**
- Added `slateMonitor(date?)` method: `GET /api/mlb/slate-monitor`

**`frontend/src/pages/SlateMonitor.tsx`** (new)
- Route `/slate-monitor`; 60s auto-refresh; date picker + team/game search
- **Status banner**: date, HEALTHY/DEGRADED/WARNING badge, last fetch time, stale warning
- **Collector health panel**: fresh% display, 5-column count grid, per-priority-type table
  with fresh%, stale, empty, missing columns; warning if fresh% < 80%
- **Brain candidates panel**: 7 tabs (Side Leans / Side Fades / 4+ Runs / 5+ Avoid /
  F5 Scoring / Live Watch / Full Avoid); each tab shows game_id, team, H/A, score,
  BO bucket, BD bucket, top reasons; empty state with run-command hint
- **EV overlay panel**: status filter pills, badge per `tradeability_label` row,
  badge per `snapshot_recency_label`, spread color-coded (amber ≥10, red ≥20),
  reason column; note that model scores are not calibrated probabilities
- **Empty states**: friendly messages + run-command hints when CSVs are not generated yet

**`frontend/src/components/Layout.tsx`**
- Added `EyeIcon` SVG; added "Slate Monitor" nav entry at `/slate-monitor`

**`frontend/src/App.tsx`**
- Added `<Route path="/slate-monitor" element={<SlateMonitor />} />`

**`docs/superpowers/plans/2026-06-21-slate-monitor-ui.md`** (new)
- Implementation plan for this feature

---

## 2026-06-21

### Full Slate Orderbook Collection v1

Coverage audit (`kalshi_snapshot_coverage_audit.py`) confirmed that Jun 15 was
the only usable date in the current DB (74% good pregame coverage). Jun 16-17
had a 12-13 hour daily collection gap (04:xx–16:xx UTC) that destroyed pregame
windows for most games. Root cause: collector was not running during the
morning/afternoon window when games are priced pregame.

**`kalshi/orderbook_recorder.py`**
- Added `import re`
- Added `_TICKER_DATE_RE`, `_TICKER_MONTH_MAP` constants for ticker date parsing
- Added `_ticker_game_date(ticker) -> str | None`: extracts `YYYY-MM-DD` from
  ticker format `KXMLB[TYPE]-[YY][MON][DD][HHMM][TEAMS]-[N]`
- Added `_get_markets_for_slate_date(conn, slate_date, market_types)`: filters
  open markets to only those for the specified game date; avoids polling 3,393
  historical markets when only today's 400-600 are needed
- Added `slate_date: Optional[str]` param to `poll_once()` and
  `poll_once_batch()`; routes to `_get_markets_for_slate_date` when provided,
  falls back to `_get_markets_to_poll` (all open markets) when None

**`kalshi_orderbook_recorder.py`**
- Added `--slate-date YYYY-MM-DD` CLI argument (optional; default None)
- Logs filter status at startup (active or not)
- Passes `slate_date` to both `poll_once_batch()` and `poll_once()`
- Old behavior (no date filter) fully preserved when `--slate-date` is omitted

**`dev.bat` (slate mode)**
- Recorder duration: 600 → **915 minutes** (covers 12:00–03:00 UTC window)
- Recorder command now passes `--slate-date %DATE%`

**`kalshi_snapshot_collection_health.py`** (new)
- Standalone read-only health check script
- Loads all markets for the slate date (via `_ticker_game_date` regex)
- Joins against latest snapshot per ticker from `kalshi_orderbook_snapshots`
- Per-market coverage labels: `fresh` (<15min), `recent` (<60min), `stale`
  (>60min), `stale_empty_book` (bid=1/ask=99), `no_snapshots`
- Priority types flagged: `moneyline`, `full_game_total`, `team_total`,
  `f5_total`, `f5_winner`
- Summary: overall status (HEALTHY/DEGRADED/WARNING), by-type breakdown,
  earliest/latest snapshot times, stale/missing detail, fresh sample
- Outputs (overwritten on each run, always reflects live state):
  - `outputs/kalshi_snapshot_collection_health/latest_collection_health.csv`
  - `outputs/kalshi_snapshot_collection_health/latest_collection_health.md`
- Console action guidance when collector is down or coverage is low

**`RUN_FULL_SLATE_ORDERBOOK.bat`** (new)
- Dedicated collector-only launcher (no API, frontend, live watcher, or paper sync)
- Pre-flight: inline Python checks that `kalshi_markets` has open markets for
  the slate date before starting; blocks with clear error if discovery not run
- Launches "MLB2 Orderbook [DATE]" window: 915 min, 30s interval, batch mode,
  `--slate-date` filter, JSONL archive to `data/kalshi_orderbook_DATE.jsonl`
- Launches "MLB2 Health Check [DATE]" window: runs health script, auto-refreshes
  every 5 minutes via `for /l` loop
- Usage: `RUN_FULL_SLATE_ORDERBOOK.bat [YYYY-MM-DD]` (no arg = today)

**Slate-date filter verification**
- `_ticker_game_date` tested on 6 representative tickers including edge cases
- Jun 15 filter: 499 markets (not 3,393 all-time); Jun 17: 658 markets
- Filtered sets contain only the requested date; no cross-date contamination
- Old `--slate-date`-omitted path confirmed unchanged

**Next step**: run `RUN_FULL_SLATE_ORDERBOOK.bat` on the next live slate,
starting by 12:00 UTC. Confirm `fresh_pct ≥ 80%` in health check by 14:00 UTC.
Then run `kalshi_ev_overlay_preview.py` to validate EV estimates with real
pregame prices.

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
