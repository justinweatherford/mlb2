## Goal
Fix UTC midnight date-boundary false negatives in slate health, and add a one-command post-slate sync/settle workflow.

## Architecture
- `mlb/slate_health.py` — adds `slate_window_bounds()` + uses window-based snapshot counting instead of strict LIKE prefix
- `api/routers/paper_lifecycle.py` — adds `sync-and-settle` combined endpoint
- `paper_sync.py` (new, repo root) — CLI wrapper: sync then settle, prints counts
- `docs/TOMORROW_SLATE_RUNBOOK.md` — updates smoke checklist + adds post-slate section

## Tech Stack
- SQLite datetime comparison via string ordering (ISO format)
- FastAPI router extension
- argparse CLI
- pytest in-memory SQLite

---

## Files changed/created

| File | Change |
|------|--------|
| `mlb/slate_health.py` | Add `slate_window_bounds()`, `snapshots_in_window`, fix warnings + readiness |
| `api/routers/paper_lifecycle.py` | Add `POST /api/mlb/paper-setups/sync-and-settle` |
| `paper_sync.py` | NEW — CLI that syncs then settles, prints all status counts |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | Add post-slate section, update smoke checklist |
| `tests/test_slate_health.py` | Add UTC boundary + window tests |
| `tests/test_paper_lifecycle.py` | Add sync-and-settle tests |
| `tests/test_paper_sync_cli.py` | NEW — CLI content/behavior tests |

---

## Step 1 — Fix `slate_window_bounds()` and `snapshots_in_window` in slate health

**File:** `mlb/slate_health.py`

Key change: replace `snapshots_today` (LIKE prefix) with `snapshots_in_window` (window-based).
Keep `snapshots_today` in the result for transparency but use `snapshots_in_window` for readiness.

```python
from datetime import date, timedelta

def slate_window_bounds(day_str: str) -> tuple[str, str]:
    """
    Return (lo, hi) covering the full MLB slate window for day_str.
    lo = day_str at 00:00:00 (catches pre-game snapshots)
    hi = (day_str + 1 day) at 12:00:00 (catches post-midnight UTC snapshots)
    Games in US timezones can run until ~03:00 UTC next day; 12:00 UTC covers all.
    """
    d = date.fromisoformat(day_str)
    lo = d.isoformat() + "T00:00:00"
    hi = (d + timedelta(days=1)).isoformat() + "T12:00:00"
    return lo, hi
```

Readiness and warnings use `snapshots_in_window` instead of `snapshots_today`.

Warning text changes:
- Old: `"No Kalshi snapshots for today — is orderbook recorder running during games?"`
- New: `"No Kalshi snapshots in slate window — is orderbook recorder running during games? (snapshots may have timestamps on next UTC day if games cross midnight)"`

Readiness fix:
- `ready` when `candidates_today AND snapshots_in_window`
- `stale` when `not game_states_today AND not snapshots_in_window`

New return fields: `snapshots_in_window`, `slate_window_lo`, `slate_window_hi`

---

## Step 2 — Add `sync-and-settle` endpoint

**File:** `api/routers/paper_lifecycle.py`

```python
@router.post("/mlb/paper-setups/sync-and-settle")
def sync_and_settle_paper_setups(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    sync_result = sync_paper_setups_for_date(db, day)
    settle_result = settle_paper_setups_for_date(db, day)
    return {
        "date": day,
        "sync": sync_result,
        "settle": settle_result,
    }
```

---

## Step 3 — Add `paper_sync.py` CLI

**File:** `paper_sync.py` (repo root, new file)

```python
"""
paper_sync.py — CLI to sync and settle paper setups after a slate.

Usage:
    python paper_sync.py --date 2026-06-15
    python paper_sync.py                    # defaults to today

No auto-trading. No TAKE labels. No order placement.
"""
import argparse
import json
import sqlite3
from datetime import date

from api.deps import DB_PATH
from db.schema import init_db
from mlb.paper_lifecycle import (
    sync_paper_setups_for_date,
    settle_paper_setups_for_date,
    query_paper_performance,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync and settle paper setups after a slate.")
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    conn = init_db(DB_PATH)
    conn.row_factory = sqlite3.Row

    print(f"[paper_sync] date={day}")
    print()

    sync_r = sync_paper_setups_for_date(conn, day)
    print(f"  SYNC  processed={sync_r['processed']} created={sync_r['created']} skipped={sync_r['skipped']}")

    settle_r = settle_paper_setups_for_date(conn, day)
    print(f"  SETTLE checked={settle_r['checked']} settled={settle_r['settled']}")

    perf = query_paper_performance(conn, date_from=day, date_to=day)
    print()
    print("  STATUS BREAKDOWN:")
    counts = {}
    for g in perf["groups"]:
        s = g["paper_status"]
        counts[s] = counts.get(s, 0) + g["total"]
    for status in ["paper_open", "paper_closed", "no_entry_price", "blocked_observation",
                   "not_trackable"]:
        n = counts.get(status, 0)
        print(f"    {status:<25} {n}")
    other = {k: v for k, v in counts.items() if k not in
             ["paper_open", "paper_closed", "no_entry_price", "blocked_observation", "not_trackable"]}
    for k, v in other.items():
        print(f"    {k:<25} {v}")

    conn.close()
    print()
    print("[paper_sync] done.")


if __name__ == "__main__":
    main()
```

---

## Step 4 — Update runbook

**File:** `docs/TOMORROW_SLATE_RUNBOOK.md`

Changes:
1. Update smoke checklist: `snapshots_in_window > 0` (not `snapshots_today`)
2. Add note about UTC midnight: snapshots captured after midnight UTC still count for the slate date
3. Add **Post-Slate section** before Review Mode Notes:
   ```
   ## After the Slate
   
   After games end, run:
   
   python paper_sync.py --date 2026-06-15
   
   Or use the API:
   POST http://localhost:8000/api/mlb/paper-setups/sync-and-settle?date=2026-06-15
   
   This syncs eligible candidates into paper_setups, resolves outcomes for finished games,
   and prints counts by status.
   ```

---

## Step 5 — Tests: slate health UTC boundary

**File:** `tests/test_slate_health.py`

New test classes:
- `TestSlateWindowBounds` — lo/hi range covers midnight, next-day covers games to 12:00 UTC
- `TestSnapshotsInWindow` — snapshot at T+next-day-00:30 is counted in `snapshots_in_window` but not `snapshots_today`
- `TestReadinessWindowFix` — readiness is `ready` when only post-midnight snapshots exist
- `TestWarningTextAccuracy` — warning mentions "slate window" not "for today"

---

## Step 6 — Tests: sync-and-settle endpoint

**File:** `tests/test_paper_lifecycle.py`

New test class `TestSyncAndSettle`:
- calls `sync_paper_setups_for_date` then `settle_paper_setups_for_date`
- result has `sync` and `settle` sub-dicts
- settled=0 when game not final, settled=1 when game final
- no real orders, no TAKE labels

---

## Step 7 — Tests: paper_sync CLI

**File:** `tests/test_paper_sync_cli.py` (new)

Content checks:
- file exists at repo root
- `--date` argument present
- `sync_paper_setups_for_date` imported
- `settle_paper_setups_for_date` imported
- No TAKE labels
- No order placement keywords
