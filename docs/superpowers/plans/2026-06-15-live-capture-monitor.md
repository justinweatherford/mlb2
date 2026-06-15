## Goal
Add a lightweight Live Capture Monitor so during tomorrow's slate we can instantly answer "Is the data pipeline capturing useful learning data right now?"

## Architecture
```
mlb/live_capture_monitor.py   ← pure query helper, no side effects
api/routers/live_capture_monitor.py  ← GET /api/mlb/live-capture-monitor?date=
api/main.py                   ← register router (one line import + include)
live_capture_monitor.py       ← root-level CLI, calls helper directly
tests/test_live_capture_monitor.py  ← TDD tests
docs/TOMORROW_SLATE_RUNBOOK.md      ← add live monitoring section
```

## Tech Stack
- Python 3 / SQLite (no new deps)
- FastAPI (existing)
- Reuses `mlb.slate_health.slate_window_bounds()` for UTC slate window

---

## Files

| File | Action | Responsibility |
|---|---|---|
| `mlb/live_capture_monitor.py` | CREATE | All query logic; returns structured dict |
| `api/routers/live_capture_monitor.py` | CREATE | Thin FastAPI router wrapping the helper |
| `api/main.py` | MODIFY | Import + register router |
| `live_capture_monitor.py` | CREATE | Root CLI printing terminal summary |
| `tests/test_live_capture_monitor.py` | CREATE | Full TDD test suite |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | MODIFY | Add §During Games section |

---

## Step 1 — `mlb/live_capture_monitor.py`

### Function signature
```python
def get_live_capture_monitor(conn, date_str=None) -> dict
```

### Queries (all wrapped in try/except → None on error)

**Kalshi tape:**
- `latest_kalshi_snapshot` = `MAX(snapped_at)` from `kalshi_orderbook_snapshots`
- `snapshots_in_window` = count in `[window_lo, window_hi]`
- `snapshots_today` = count with `snapped_at LIKE date%`

**MLB polling:**
- `latest_mlb_game_state` = `MAX(checked_at)` from `mlb_game_states WHERE checked_at LIKE date%`
- `game_states_today` = count `mlb_game_states WHERE checked_at LIKE date%`
- `games_today` = count `mlb_games WHERE game_date = date`

**Candidates:**
- `candidates_today` = count `candidate_events WHERE created_at LIKE date%`
- `candidates_by_derivative_type` = GROUP BY `derivative_type`
- `candidates_by_status` = GROUP BY `status`

**Paper setups:**
- `paper_setups_today` = count `paper_setups WHERE created_at LIKE date%`
- `paper_setups_by_status` = GROUP BY `paper_status`
- `paper_setups_with_entry_price` = count WHERE `entry_price_cents IS NOT NULL AND created_at LIKE date%`
- `paper_setups_no_entry_price` = count WHERE `entry_price_cents IS NULL AND created_at LIKE date%`

**Good entry eval:**
- `good_entry_label_breakdown` = GROUP BY `good_entry_label WHERE created_at LIKE date%`

**Tape quality via paper setup proxy:**
- `candidates_with_usable_tape` = count `paper_setups WHERE paper_status = 'paper_open' AND created_at LIKE date%`
- `candidates_with_no_tape` = count `paper_setups WHERE paper_status = 'no_entry_price' AND created_at LIKE date%`

### Readiness label logic (first match wins)
```
1. games_today == 0                           → waiting_for_games
2. games_today > 0 AND snapshots_in_window == 0 → stale_recorder
3. games_today > 0 AND game_states_today == 0  → stale_mlb
4. candidates_today == 0                       → no_candidates_yet
5. candidates_today > 0 AND paper_setups_today == 0 → paper_not_synced
6. candidates_today > 0 AND paper_setups_with_entry_price == 0
   AND snapshots_in_window == 0               → candidates_without_tape
7. otherwise                                   → ready
```
- On DB error (any None from queries): → `blocked`

### next_action strings
```python
NEXT_ACTIONS = {
    "waiting_for_games":       "Waiting for games to start. Recorder is fresh.",
    "stale_recorder":          "Recorder appears stale. Check Kalshi Orderbook Recorder window.",
    "stale_mlb":               "MLB Poller appears stale. Check MLB Poller window.",
    "no_candidates_yet":       "Games are live but no candidates yet. Live Watcher should fire soon.",
    "paper_not_synced":        "Paper setups have not synced yet. Run paper_sync.py or sync endpoint.",
    "candidates_without_tape": "Candidates are firing but no nearby market tape is being attached.",
    "ready":                   "Live capture looks healthy.",
    "blocked":                 "DB error. Check database connection.",
}
```

### Return dict keys
```
date, capture_readiness, next_action,
latest_kalshi_snapshot, snapshots_in_window, snapshots_today,
latest_mlb_game_state, game_states_today, games_today,
candidates_today, candidates_by_derivative_type, candidates_by_status,
paper_setups_today, paper_setups_by_status,
paper_setups_with_entry_price, paper_setups_no_entry_price,
candidates_with_usable_tape, candidates_with_no_tape,
good_entry_label_breakdown
```

---

## Step 2 — `api/routers/live_capture_monitor.py`

```python
router = APIRouter()

@router.get("/mlb/live-capture-monitor")
def live_capture_monitor_endpoint(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return get_live_capture_monitor(db, day)
```

---

## Step 3 — `api/main.py` (one-line change each)

Add to imports:
```python
from api.routers import ..., live_capture_monitor as live_capture_monitor_router
```
Add router registration after slate_health:
```python
app.include_router(live_capture_monitor_router.router, prefix=PREFIX, tags=["live-capture"])
```

---

## Step 4 — `live_capture_monitor.py` (root CLI)

```python
# python live_capture_monitor.py --date 2026-06-15
argparse → date → init_db → get_live_capture_monitor(conn, date) → print summary
```

Print format:
```
[live_capture_monitor] date=2026-06-15
Status:      ready
Next action: Live capture looks healthy.

Kalshi tape:
  Snapshots in window:  12345
  Latest snapshot:      2026-06-15T22:45:00

MLB polling:
  Game states today:    892
  Games today:          15

Candidates:
  Total:                42
  By derivative:        team_total=18, fg_total=12, f5_total=8, fg_spread=4
  By status:            observed_only=35, blocked=7

Paper setups:
  Total:                12
  With entry price:     9
  No entry price:       3
  By status:            paper_open=9, no_entry_price=3

Good entry labels:
  strong_value=2, possible_value=4, watch_only=3, no_entry_price=3
```

---

## Step 5 — `tests/test_live_capture_monitor.py`

Groups:
- `TestEmptySlate` — empty DB → waiting_for_games, no crash
- `TestStaleRecorder` — games but no snapshots → stale_recorder
- `TestStaleMlb` — games + snapshots but no game_states → stale_mlb
- `TestNoCandidates` — all up but no candidates → no_candidates_yet
- `TestPaperNotSynced` — candidates but no paper setups → paper_not_synced
- `TestCandidatesWithoutTape` — candidates+paper setups but no entry price + no snapshots → candidates_without_tape
- `TestReady` — everything flowing → ready
- `TestBreakdowns` — candidates_by_derivative_type, paper_setups_by_status, good_entry_label_breakdown
- `TestOutputContract` — all required keys present
- `TestNoTakeLabels` — no TAKE in any label/next_action
- `TestNoOrderExecution` — source scan
- `TestCLI` — CLI prints status and next_action (subprocess or import)

---

## Step 6 — `docs/TOMORROW_SLATE_RUNBOOK.md`

Insert new section after "G — Verify slate health":

```markdown
### H — Live Capture Monitor (during games)

Every 20–30 minutes during the slate, run:

    python live_capture_monitor.py --date 2026-06-15

Good signs: snapshots_in_window increasing, candidates increasing,
paper setups appearing, entry_price count growing, good_entry_label breakdown showing.

Bad signs: stale_recorder label, all candidates are no_entry_price,
paper_not_synced after candidates appear, good_entry_label always null.
```

---

## Quality Checks
- [x] No final-result fields read
- [x] No TAKE labels
- [x] No order placement
- [x] No candidate generation changes
- [x] All queries wrapped in try/except → safe on empty DB
- [x] Uses existing `slate_window_bounds()` for UTC safety
