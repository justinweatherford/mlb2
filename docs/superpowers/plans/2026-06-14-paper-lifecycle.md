## Goal
Create a `paper_setups` table and `mlb/paper_lifecycle.py` that auto-classify candidates as paper_open/blocked_observation/no_entry_price/not_trackable and capture entry price from Kalshi orderbook tape.

## What already exists (do NOT recreate)
- `candidate_events` — rich rows with price fields (entry_yes_bid/ask, spread_cents, baseline prices)
- `paper_positions` — OLD signal-based system, not used for candidates
- `manual_trade_journal` — human-entered trades linked to candidate_event_id; already has P&L + settlement
- `mlb/setup_outcomes.py` — `aggregate_setups()` + `_resolve_outcome()` for hit/miss/push from final scores; used for settlement
- `mlb/performance.py` — grouping by derivative_type + read_type (already works via manual_trade_journal)
- SlateReview "Setups" tab — outcome tracking already exists
- `kalshi/market_tape_correlation.py` — `get_market_tape_context_batch()` provides entry price context

## What is NEW
- `paper_setups` table — one row per unique setup (game_id|ticker|derivative_type|read_type)
- `mlb/paper_lifecycle.py` — classify, create, settle, query
- `api/routers/paper_lifecycle.py` — GET + POST sync endpoints
- TimelineTable "Paper" column in SlateReview

## Architecture
```
live_watcher inserts candidate_events
↓
POST /api/mlb/paper-setups/sync?date=YYYY-MM-DD
↓
mlb/paper_lifecycle.sync_paper_setups_for_date(conn, date)
  → reads candidates
  → calls get_market_tape_context_batch() for entry price
  → classifies: paper_open | blocked_observation | no_entry_price | not_trackable
  → creates paper_setups rows (skip if already exists for setup_key)
↓
POST /api/mlb/paper-setups/settle?date=YYYY-MM-DD  (run after games final)
↓
mlb/paper_lifecycle.settle_paper_setups_for_date(conn, date)
  → calls aggregate_setups() from setup_outcomes.py
  → matches by setup_key, updates paper_status=paper_closed, outcome, pnl
↓
GET /api/mlb/paper-setups?date=YYYY-MM-DD → list for SlateReview
GET /api/mlb/paper-performance → grouped by derivative_type/read_type
```

## paper_status values
- `paper_open` — Watch/Review candidate, entry price captured from tape, game not yet final
- `paper_closed` — game final, outcome resolved
- `blocked_observation` — candidate was blocked (not a paper trade)
- `no_entry_price` — Watch candidate but no usable tape at candidate time
- `not_trackable` — missing ticker or UNKNOWN proposed side

## Entry price logic
- YES side: YES ask = midpoint_after + spread_after//2
- NO side: NO ask = (100 - midpoint_after) + spread_after//2
- Source label: "yes_ask_from_tape" or "no_ask_from_tape"

## P&L per contract (no stake amount in paper_setups)
- won: gross = 100 - entry; fee = 3¢; net = gross - 3
- lost: gross = -entry; fee = 0; net = gross
- pushed: gross = 0; fee = 0; net = 0
- unknown/not_settleable: all null

## Files

| File | Action |
|------|--------|
| `db/schema.py` | Add `paper_setups` DDL |
| `mlb/paper_lifecycle.py` | Create |
| `api/routers/paper_lifecycle.py` | Create |
| `api/main.py` | Register router |
| `frontend/src/types/api.ts` | Add PaperSetup interface |
| `frontend/src/api/client.ts` | Add paperLifecycle + paperPerformance calls |
| `frontend/src/pages/SlateReview.tsx` | Add Paper column to TimelineTable |
| `tests/test_paper_lifecycle.py` | Create (all tests) |

## Test groups
- `TestClassifyStatus` — all 4 status branches + YES/NO side
- `TestCreateOrSkip` — creates once, skips on second call
- `TestEntryPrice` — YES uses ask, NO uses ask, spread stored
- `TestNoTape` — no_entry_price when tape is no_tape/unavailable
- `TestBlocked` — blocked → blocked_observation
- `TestNotTrackable` — missing ticker, UNKNOWN side
- `TestSettlement` — won/lost/pushed/unknown, P&L computed, paper_closed
- `TestSettlementUnsafe` — unsupported market_type → not_settleable or unknown
- `TestSync` — batch creates paper_setups for date, skips duplicates
- `TestPaperPerformance` — grouped by derivative_type/read_type
- `TestNoTakeLabels` — no TAKE/signal/order fields anywhere
- `TestNoRealOrders` — module has no order placement code
