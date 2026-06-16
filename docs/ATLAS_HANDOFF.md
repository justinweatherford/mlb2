# ATLAS Handoff — Project State as of 2026-06-16

This document is a durable state snapshot for future Claude/Atlas sessions.
Read this before starting any new work to avoid stale roadmap confusion.

---

## What This Project Is

A research-and-paper-trading system for MLB Kalshi prediction markets.
It ingests live game state (Discord feed), polls Kalshi market prices, classifies
pricing behavior, and paper-trades with realistic fee math.

**Safety status: paper/research only. No real orders. No auto-trading.**
The system has read-only Kalshi API credentials and no order-placement code paths
are enabled.

---

## Current Strategic Direction

### Primary Immediate Priority: Fix Kalshi Market Data Latency

The market data ingestion pipeline has two critical bugs that inflate snapshot
latency from <1 second to ~265 seconds (4.4 minutes). Both bugs are now fixed
in code (Fast Kalshi Market Data Fix v1, completed 2026-06-16). The fix needs
to run on a real game day and be validated before any signal lane work resumes.

See: `outputs/kalshi_api_audit/fast_data_fix_summary.md`

### Signal Lane Work: Paused

All signal lane decisions (which market type to target first, spread/run-line
vs totals vs team totals) are deferred until:
1. WebSocket + batch REST ingestion is live and producing data
2. `market_liveness_validator.py` is rerun on new data with corrected cadence
3. Liveness results confirm which market type is actually responsive

**Do not resume signal lane work in a new session without first verifying
that `kalshi_orderbook_snapshots` contains `ws_ticker` or `rest_batch` rows.**

### Candidates Page / UI Work: Deferred

The React dashboard `/candidates` page is deferred until Kalshi ingestion is
fixed. Current candidate scoring is based on 4.4-minute snapshot cadence data,
which means scores are unreliable. Do not build or ship Candidates UI features
until liveness is proven.

### Spread/Run-Line Recovery: Research-Only

Spread markets showed 18/110 tickers confirmed stale during 2026-06-15 with
a 4.4-minute snapshot cadence. These markets require a strict liveness gate
before use. No signal lane based on spreads until post-fix liveness is validated.

### FG Total Overreaction: Likely First Foundation Lane

Full-game totals (FG total) are the most likely candidate for the first
foundation signal lane, conditional on liveness validation after the data fix.
The FG Total overreaction pattern (market overcorrects on a half-inning scoring
event) has the strongest historical basis. Confirm with liveness data first.

---

## Completed Work Summary

### Phase 1 — Core Pipeline (complete, 871 tests passing)

| Module | Status | Notes |
|--------|--------|-------|
| `context_usage_audit.py` | Done | Audits token/context cost per session |
| Team-total line parsing cleanup | Done | Handles ambiguous team-total vs totals parsing |
| Setup-level outcome reconciliation | Done | Reconciles game outcome with paper positions |
| Conservative execution model | Done | Taker-fee realistic mode, no chase above 85¢ |
| Signal funnel tracking | Done | `signal_funnel_events` table, full trace from candidate to entry |
| Spread/run-line recovery research | Done | `spread_recovery_research.py` — research only, not live signal |
| Market liveness validator | Done | `market_liveness_validator.py`, output in `outputs/market_liveness/` |
| Kalshi API architecture audit | Done | `outputs/kalshi_api_audit/` — 5 files |

### Fast Kalshi Market Data Fix v1 (complete, 2026-06-16)

| Part | File | Change |
|------|------|--------|
| A — WS bridge | `kalshi/normalizer.py` | Bridges WS prices into `kalshi_orderbook_snapshots` |
| B — URL/batch fix | `kalshi/ws_client.py` | Correct prod WS URL; batch size 200→100 |
| C — Batch REST | `kalshi/client.py`, `kalshi/orderbook_recorder.py`, `kalshi_orderbook_recorder.py` | Batch endpoint, `poll_once_batch()`, `--batch` CLI flag |
| D — Trades fix | `kalshi/client.py`, `kalshi/market_trades.py` | Correct trades endpoint path |
| E — Summary | `outputs/kalshi_api_audit/fast_data_fix_summary.md` | Implementation summary |

---

## Key Findings

### Kalshi Market Data Latency Root Causes

1. **Sequential REST polling was the primary bottleneck.**
   `poll_once()` in `kalshi/orderbook_recorder.py` called `GET /markets/{ticker}/orderbook`
   one ticker at a time — 422 markets × ~620ms/request ≈ 262 seconds per cycle.
   Fix: use batch endpoint `GET /markets/orderbooks?tickers=T1&tickers=T2...`
   (100 tickers per call → 5 calls for 422 markets → ~1 second per sweep).

2. **WebSocket data wrote to the wrong table (split-brain).**
   The WS collector (`kalshi_ws.py`) already received sub-second price updates
   but wrote them to `kalshi_market_updates`. Every analysis module reads
   `kalshi_orderbook_snapshots`. WS data was invisible to all reports.
   Fix: bridge function in `kalshi/normalizer.py` now writes WS prices to both tables.

3. **Wrong WebSocket production URL.**
   `_PROD_WS` pointed to `wss://api.elections.kalshi.com/trade-api/ws/v2`.
   Documented production URL: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`.
   This may have been causing silent WS connection failures.

4. **WS batch subscription limit exceeded.**
   `_MAX_TICKERS_PER_BATCH = 200` exceeded the Kalshi-documented per-subscription
   limit. Subscriptions with >100 tickers trigger error code 26 and may be silently
   rejected. Fix: lowered to 100.

5. **Correct trades endpoint.**
   Wrong: `GET /trade-api/v2/markets/{ticker}/trades` (404 — path does not exist)
   Correct: `GET /trade-api/v2/markets/trades?ticker={market_ticker}`

6. **Recommended architecture: hybrid REST batch + WebSocket.**
   - WebSocket `ticker` channel → `kalshi_orderbook_snapshots` (source=`ws_ticker`): sub-second cadence for active markets
   - REST batch every 30s → same table (source=`rest_batch`): heartbeat for stale detection, cold-start fill
   - Focused tape watcher unchanged: candidate-level depth polling

---

## Current Next Actions (in order)

### 1. Run the fixed stack on a live game day

```bash
# Terminal 1 — WebSocket live feed
python kalshi_ws.py

# Terminal 2 — REST batch heartbeat (new --batch mode)
python kalshi_orderbook_recorder.py --sport mlb --batch --interval-seconds 30

# Terminal 3 — Focused tape watcher (unchanged)
python focused_tape_watcher.py
```

### 2. Verify fast data ingestion

```sql
-- Run after the first game starts
SELECT source, COUNT(*) FROM kalshi_orderbook_snapshots GROUP BY source;
-- Expected: ws_ticker rows appearing alongside rest_poll / rest_batch

-- Check WS session health
SELECT started_at, ended_at, msg_count, status
FROM kalshi_ws_sessions ORDER BY id DESC LIMIT 5;
```

### 3. Rerun market liveness validator

```bash
python market_liveness_validator.py --date <new-date>
# Output written to outputs/market_liveness/<date>/
```

Look for:
- Median cadence < 30s (was 265s with sequential REST)
- `score_event%` rising from 8–11% to 40–70%+
- `stale` ticker count decreasing (inflated by sampling gaps before)

### 4. Decide foundation signal lane

Based on corrected liveness data, pick the first signal lane to develop.
Current expectation: FG totals (best cadence, clearest overreaction pattern).
Do NOT decide this before rerunning liveness validation.

---

## Important Safety Rules

These rules apply in every session. Do not override them without explicit user instruction.

1. **No real order creation.** The system has no code to place Kalshi orders.
   Do not add order placement, even behind flags.

2. **No auto-trading.** All position entry is paper-only and requires manual review.

3. **No candidate scoring changes** without explicit user instruction and liveness
   validation data. Scores based on 4.4-minute cadence are unreliable.

4. **No signal lane changes** in a session unless the user explicitly requests it
   and liveness validation has been rerun on new data.

5. **Paper-only validation.** Any new signal pattern must run as paper positions
   for at least one full game week before any live consideration.

---

## Architecture Quick Reference

### Data Flow (after Fast Data Fix v1)

```
Kalshi Exchange
  │
  ├── WebSocket push (ticker, orderbook_delta)
  │       → kalshi/ws_client.py
  │       → kalshi/normalizer.py
  │           → kalshi_market_updates (existing)
  │           → kalshi_orderbook_snapshots (NEW: source=ws_ticker/ws_orderbook)
  │
  └── REST batch (every 30s with --batch flag)
          → kalshi/client.py:get_orderbooks_batch()
          → kalshi/orderbook_recorder.py:poll_once_batch()
          → kalshi_orderbook_snapshots (source=rest_batch)

kalshi_orderbook_snapshots
  → market_liveness_validator.py
  → spread_recovery_research.py
  → live_watcher.py → candidate_events
  → focused_tape_watcher.py (adds depth snapshots for candidates)
```

### Key Tables

| Table | Written by | Read by |
|-------|-----------|---------|
| `kalshi_orderbook_snapshots` | REST poll, REST batch, WS bridge | All analysis |
| `kalshi_market_updates` | WS collector | (legacy, not used by analysis) |
| `kalshi_markets` | Market discoverer, WS ticker sync | Liveness validator, candidate gen |
| `paper_positions` | Live watcher | Dashboard, reports |
| `signal_funnel_events` | Live watcher | Dashboard |
| `kalshi_ws_sessions` | WS collector | Health monitoring |

### Source Column Values in `kalshi_orderbook_snapshots`

| Source | Written by | When |
|--------|-----------|------|
| `rest_poll` | `poll_once()` | Sequential polling (legacy default) |
| `rest_batch` | `poll_once_batch()` | Batch mode (`--batch` flag) |
| `ws_ticker` | WS bridge | On every WS `ticker` message with prices |
| `ws_orderbook` | WS bridge | On every WS `orderbook_delta` message |

### Test Suite

```bash
python -m pytest tests/ -q
# Expected: 3476 passed (as of 2026-06-16)
```

---

## Market Liveness Baseline (2026-06-15, pre-fix)

| Market Type | Tickers | Median Cadence | Responsive % |
|-------------|---------|----------------|-------------|
| team_total | 126 | 267.6s | 57% |
| f5_spread | 36 | 262.6s | ~49% |
| f5_total | 63 | 262.6s | ~49% |
| full_game_total | 105 | 264.2s | ~49% |
| spread_run_line | 74 | 267.6s | 8.1% score-event sensitivity |
| moneyline | 18 | 600.5s | lowest |

These numbers are based on 4.4-minute snapshot cadence. They will change after
the data fix. Do not use them for signal lane decisions.

---

## File Map for Key Modules

| Purpose | File |
|---------|------|
| WS live feed runner | `kalshi_ws.py` |
| REST orderbook recorder | `kalshi_orderbook_recorder.py` |
| Focused candidate tape | `focused_tape_watcher.py` |
| Market liveness report | `market_liveness_validator.py` |
| WS message normalizer + bridge | `kalshi/normalizer.py` |
| REST API client | `kalshi/client.py` |
| WS client + reconnect | `kalshi/ws_client.py` |
| Orderbook snapshot writer | `kalshi/orderbook_recorder.py` |
| DB schema + migrations | `db/schema.py` |
| Live signal watcher | `live_watcher.py` |
| Paper position logic | `paper_trader.py` |
| Architecture audit outputs | `outputs/kalshi_api_audit/` |
| Liveness report outputs | `outputs/market_liveness/` |
| Game day runbook | `docs/TOMORROW_SLATE_RUNBOOK.md` |
