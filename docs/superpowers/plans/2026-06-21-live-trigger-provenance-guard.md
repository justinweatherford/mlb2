# Plan: Live Trigger Provenance Guard v1

## Goal
Fix the one real provenance bug (live_watcher has no game-date filter, so stale
`is_final=0` games from prior dates get processed), add a `trigger_game_date`
field for future-proof retrospective analysis, and correct the false-alarm
`pregame_detection` flag in the retrospective script.

## Architecture

```
live_watcher.run_one_cycle()
  └─ SELECT mlb_games WHERE is_final=0          ← BUG: no date filter
     └─ generate_candidates_for_game()
          └─ _latest_game_state(mlb_game_states)
               └─ upsert_candidate_event(candidate_events)

Fix flow:
  live_watcher ──slate_date──► generate_candidates_for_game ──game_date──► upsert_candidate_event ──trigger_game_date──► DB
```

## Tech Stack
- Python stdlib + sqlite3
- No external API calls; no model scoring changes; no paper/trade actions

---

## Root Cause (confirmed by data inspection)

### Bug 1 — `live_watcher.run_one_cycle()` has no game-date filter (CRITICAL)
```sql
-- Current (buggy):
SELECT game_pk, game_id, is_final FROM mlb_games
WHERE is_final = 0 OR (is_final = 1 AND last_checked_at >= ?)

-- Problem: 6 Jun 17 games have is_final=0 (stale DB records — game ended
-- but mlb_games was never updated). These get processed on Jun 21, generating
-- candidates for Jun 17 markets. PIT@ATH Jun 17 is a confirmed example.
```

### Non-bug — `pregame_detection` flag was a false alarm (MEASUREMENT ERROR)
- `mlb_game_states.checked_at` and `candidate_events.first_seen_at` are stored
  in **ET (local time)** without a timezone label.
- `mlb_games.game_start_time_utc` is stored in **UTC**.
- String comparison `'14:01' < '17:35'` → True → flagged as pregame.
- Reality: 14:01 ET = 18:01 UTC > 17:35 UTC game start → candidate WAS live.
- All 398 "pregame_detection" flags from Jun 21 retrospective were wrong.
- MIL@ATL game was actually live at 13:35 ET (= 17:35 UTC) and candidates
  fired at 14:01 ET (= 18:01 UTC), after first pitch.

### Data quality issue — 6 Jun 17 games have `is_final=0`
The mlb game-state poller didn't update these to final. No data fix needed —
the date filter in Bug 1's fix prevents them from being processed going forward.

---

## Files Modified / Created

| File | Change |
|---|---|
| `live_watcher.py` | Add `slate_date` param + date filter to SQL query + `--slate-date` CLI arg |
| `mlb/candidate_generator.py` | Add `slate_date` param + guard; pass `game_date` as `trigger_game_date` |
| `mlb/candidates.py` | Add `trigger_game_date` to `upsert_candidate_event` + `insert_candidate_event` |
| `db/schema.py` | Add `trigger_game_date TEXT` to DDL + `_migrations` list |
| `kalshi_post_slate_retrospective.py` | Fix `pregame_detection` → `pregame_state` (score-based, timezone-agnostic) |
| `tests/test_live_watcher_provenance.py` | 3 new targeted tests (NEW FILE) |

---

## Task 1 — `db/schema.py`: Add `trigger_game_date` column

**File:** `db/schema.py`

**Change A**: In the `candidate_events` CREATE TABLE DDL, add after `rejected_derivatives_json`:
```sql
    -- Provenance: game date the candidate was generated for (from mlb_games.game_date)
    trigger_game_date           TEXT,
```

**Change B**: In `_apply_migrations()`, append to the `_migrations` list:
```python
"ALTER TABLE candidate_events ADD COLUMN trigger_game_date TEXT",
```

No backfill needed — existing rows get NULL. Retrospective script handles NULL
gracefully (marks as `unknown_trigger_date`).

---

## Task 2 — `mlb/candidates.py`: Thread `trigger_game_date` through upsert

**File:** `mlb/candidates.py`

**Change A**: Add `trigger_game_date: Optional[str] = None` to `upsert_candidate_event`
keyword args (after `rejected_derivatives_json`).

**Change B**: In `upsert_candidate_event`, pass `trigger_game_date=trigger_game_date`
through to `insert_candidate_event(...)`.

**Change C**: Add `trigger_game_date: Optional[str] = None` to `insert_candidate_event`
keyword args and include it in the INSERT statement.

---

## Task 3 — `mlb/candidate_generator.py`: Add slate_date guard + pass trigger_game_date

**File:** `mlb/candidate_generator.py`

**Change A**: `generate_candidates_for_game()` signature:
```python
def generate_candidates_for_game(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    slate_date: Optional[str] = None,   # ADD THIS
) -> GameDiag:
```

**Change B**: After the existing `if game_row and game_row["is_final"]: return diag`,
add:
```python
game_date = game_row["game_date"] if game_row else None
# Belt-and-suspenders: skip games from other dates even if is_final=0
if slate_date and game_date and game_date != slate_date:
    diag.skip_reasons["wrong_game_date"] = diag.skip_reasons.get("wrong_game_date", 0) + 1
    return diag
```

**Change C**: Pass `trigger_game_date=game_date` in all three `upsert_candidate_event`
calls (`_try_full_game_total_watch`, `_try_f5_fade_watch`, `_try_trailing_team_total_watch`).

---

## Task 4 — `live_watcher.py`: Add slate_date param + date filter

**File:** `live_watcher.py`

**Change A**: `run_one_cycle()` signature:
```python
def run_one_cycle(conn, verbose: bool = False, slate_date: Optional[str] = None) -> dict:
```

**Change B**: At the top of `run_one_cycle()`, resolve `slate_date`:
```python
if slate_date is None:
    slate_date = datetime.now().strftime("%Y-%m-%d")
```

**Change C**: Replace the SQL query:
```python
# OLD:
games = conn.execute(
    """
    SELECT game_pk, game_id, is_final FROM mlb_games
    WHERE is_final = 0
       OR (is_final = 1 AND last_checked_at >= ?)
    """,
    (cutoff,),
).fetchall()

# NEW:
games = conn.execute(
    """
    SELECT game_pk, game_id, is_final FROM mlb_games
    WHERE game_date = ?
      AND (is_final = 0 OR (is_final = 1 AND last_checked_at >= ?))
    """,
    (slate_date, cutoff),
).fetchall()
```

**Change D**: Pass `slate_date` to `generate_candidates_for_game`:
```python
diag = generate_candidates_for_game(conn, game["game_pk"], game["game_id"], slate_date=slate_date)
```

**Change E**: Add `--slate-date` to `main()`:
```python
parser.add_argument(
    "--slate-date", default=None, metavar="YYYY-MM-DD",
    help="Only process games for this date (default: today). "
         "Prevents stale is_final=0 games from prior dates from being included.",
)
```

**Change F**: Pass to `run_one_cycle`:
```python
result = run_one_cycle(conn, verbose=args.verbose, slate_date=args.slate_date)
```

---

## Task 5 — `kalshi_post_slate_retrospective.py`: Fix `pregame_detection`

**File:** `kalshi_post_slate_retrospective.py`

**Problem:** The current check compares ET timestamps to UTC timestamps as strings.
This gives a false positive for all valid live candidates (4-hour timezone offset
makes the comparison always show "pregame" even when the game had started).

**Fix:** Replace the timestamp comparison with a score-based check that is
timezone-agnostic:

```python
# OLD (in _data_quality_flags):
start_utc = game.get("game_start_time_utc")
first_seen = c.get("first_seen_at") or ""
if start_utc and first_seen and first_seen.replace("T", " ") < start_utc:
    flags.append("pregame_detection")

# NEW:
# Timezone-agnostic: if score was still 0-0 at trigger, game may not have started scoring yet
score_away = c.get("score_away") or 0
score_home = c.get("score_home") or 0
inning = c.get("inning_at_trigger") or 0
if score_away == 0 and score_home == 0 and inning <= 1:
    flags.append("pregame_state")  # no scoring yet at detection — may be pre-pitch warmup

# NEW: wrong game date (requires trigger_game_date column — may be NULL for old rows)
trigger_gd = c.get("trigger_game_date")
if trigger_gd and trigger_gd != slate_date:
    flags.append("wrong_game_date")
```

**Also update `_FLAG_MEANINGS`:**
```python
"pregame_state":   "Score was 0-0 at inning ≤1 — candidate fired before any scoring",
"wrong_game_date": "Candidate's trigger_game_date != slate_date (cross-date contamination)",
```

Remove `"pregame_detection"` from `_FLAG_MEANINGS`.

---

## Task 6 — `tests/test_live_watcher_provenance.py`: 3 targeted tests

**File:** `tests/test_live_watcher_provenance.py` (NEW)

```python
"""
tests/test_live_watcher_provenance.py

Targeted tests for the date-filter provenance guard in live_watcher.
Verifies that:
  1. Games from prior dates (is_final=0) are excluded when slate_date is set.
  2. Games from today (is_final=0) are included.
  3. generate_candidates_for_game skips wrong-date games when slate_date passed.
"""
import sqlite3
from datetime import datetime, timedelta
import pytest
from db.schema import init_db
from live_watcher import run_one_cycle
from mlb.candidate_generator import generate_candidates_for_game, GameDiag


def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(conn, game_pk, game_id, game_date, is_final=0):
    conn.execute(
        """INSERT INTO mlb_games (game_pk, game_id, game_date, away_team, home_team,
           away_abbr, home_abbr, status, is_final, last_checked_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_pk, game_id, game_date, "Away", "Home", "AWY", "HME",
         "Final" if is_final else "Live", is_final,
         datetime.now().isoformat()),
    )
    conn.commit()


TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def test_date_filter_excludes_prior_date_games():
    """Prior-date is_final=0 games must not be processed when slate_date=today."""
    conn = _mem()
    _insert_game(conn, 99901, "PIT@ATH", YESTERDAY, is_final=0)
    result = run_one_cycle(conn, verbose=False, slate_date=TODAY)
    assert result["games_scanned"] == 0, (
        "Prior-date is_final=0 game should be excluded by slate_date filter"
    )


def test_date_filter_includes_today_games():
    """Today's is_final=0 games should be included."""
    conn = _mem()
    _insert_game(conn, 99902, "BOS@NYY", TODAY, is_final=0)
    result = run_one_cycle(conn, verbose=False, slate_date=TODAY)
    assert result["games_scanned"] == 1, (
        "Today's non-final game should be included by slate_date filter"
    )


def test_generate_skips_wrong_date_game():
    """generate_candidates_for_game with slate_date should skip wrong-date game."""
    conn = _mem()
    _insert_game(conn, 99903, "SD@TEX", YESTERDAY, is_final=0)
    diag = generate_candidates_for_game(conn, 99903, "SD@TEX", slate_date=TODAY)
    assert isinstance(diag, GameDiag)
    assert "wrong_game_date" in diag.skip_reasons, (
        "Wrong-date game should be skipped with 'wrong_game_date' reason"
    )
    assert len(diag.ids) == 0, "No candidates should be generated for wrong-date game"
```

---

## Verification

After implementation, run:
```
python -m pytest tests/test_live_watcher_provenance.py tests/test_live_watcher_diagnostics.py tests/test_candidates.py tests/test_candidate_dedup.py -v
```

Confirm:
- All 3 new tests pass
- Existing tests still pass (no regressions)
- No DB writes to paper_positions or paper_setups
- No model scoring changes
- No eligible_for_paper=1 changes

Then run:
```
python kalshi_post_slate_retrospective.py --slate-date 2026-06-21
```
Confirm:
- `pregame_detection` flag count drops to 0 (or near 0)
- `pregame_state` flag count is a small number (only 0-0 score candidates)
- `wrong_game_date` flag count = 1 (just PIT@ATH Jun 17)
- Shadow P/L summary unchanged for settled rows

---

## Safety Checklist
- [ ] No trades enabled
- [ ] No paper entries created
- [ ] No model scoring changes
- [ ] No EV claims added
- [ ] Team Lag remains observe-only (no status change)
- [ ] No data deleted
- [ ] `eligible_for_paper` remains 0 for all candidates
- [ ] Existing tests pass

---

## What Is NOT in This Plan

- **UI badge update** (Live/Pregame/Historical/Invalid Provenance): deferred.
  The date filter fix prevents contamination; visual labeling is cosmetic and
  belongs in a separate frontend task.
- **Fixing the 6 stale `is_final=0` Jun 17 records**: not needed. The date
  filter prevents them from ever being processed again.
- **Full provenance field set** (trigger_source, trigger_source_timestamp, etc.):
  only `trigger_game_date` is added — the minimum needed for retrospective
  accuracy. Other fields can be added if a specific use case emerges.

---

## Execution Options

1. **Inline execution** — 5 focused changes, ~4 files, single session (recommended)
2. **Subagent** — overkill for this scope

Recommended: **Inline**.
