## Goal
Give tomorrow's slate a clear runbook and lightweight health check so we know what to start, in what order, and whether each component is working.

## Architecture
- `mlb/slate_health.py` — pure function `get_slate_health(conn, date_str) -> dict`; queries DB for counts/timestamps, classifies readiness
- `api/routers/slate_health.py` — `GET /api/mlb/slate-health?date=YYYY-MM-DD`
- `api/main.py` — register router
- `docs/TOMORROW_SLATE_RUNBOOK.md` — exact ordered commands + "what to expect"
- `tests/test_slate_health.py` — all tests

## Tech Stack
SQLite, FastAPI, in-memory pytest fixtures, Python dataclasses

---

## Key tables and timestamp columns

| Table                        | Timestamp col   | Purpose                    |
|------------------------------|-----------------|----------------------------|
| `kalshi_orderbook_snapshots` | `snapped_at`    | Kalshi tape freshness      |
| `candidate_events`           | `created_at`    | Candidate count today      |
| `mlb_game_states`            | `checked_at`    | MLB poller freshness       |
| `mlb_games`                  | `created_at`    | MLB game coverage          |
| `kalshi_markets`             | `created_at`    | Market discovery           |

---

## Readiness classification

```
blocked  — DB open fails OR critical table missing
stale    — no MLB game states AND no Kalshi snapshots for date
partial  — MLB data exists but no candidates, OR candidates but no Kalshi snapshots
ready    — candidates exist AND Kalshi snapshots exist for date
```

---

## Step 1 — Write all failing tests in `tests/test_slate_health.py`

Groups:
- `TestSlateHealthFields` — all expected keys present
- `TestReadinessClassification` — blocked/stale/partial/ready logic
- `TestFreshData` — fresh MLB + Kalshi → ready
- `TestStaleMlbData` — no game states → stale
- `TestStaleKalshiData` — no snapshots → partial or stale
- `TestNoTakeLabels` — no TAKE/signal fields in result
- `TestRunbookExists` — runbook file exists, required commands present

---

## Step 2 — Implement `mlb/slate_health.py`

```python
"""
mlb/slate_health.py — Read-only slate health check.

No candidate generation. No TAKE labels. No trading logic.
"""
import sqlite3
from datetime import date
from typing import Optional


def get_slate_health(
    conn: sqlite3.Connection,
    date_str: Optional[str] = None,
    db_path: str = "kalshi_mlb.db",
) -> dict:
    day = date_str or date.today().isoformat()
    prefix = day + "T"
    prefix_wild = day + "%"

    def _count(sql, *params):
        try:
            return conn.execute(sql, params).fetchone()[0] or 0
        except Exception:
            return None

    def _latest(sql, *params):
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    # --- Counts for date --------------------------------------------------
    candidates_today = _count(
        "SELECT COUNT(*) FROM candidate_events WHERE created_at LIKE ?", prefix_wild
    )
    snapshots_today = _count(
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE snapped_at LIKE ?", prefix_wild
    )
    game_states_total = _count("SELECT COUNT(*) FROM mlb_game_states")
    game_states_today = _count(
        "SELECT COUNT(*) FROM mlb_game_states WHERE checked_at LIKE ?", prefix_wild
    )
    kalshi_markets_total = _count("SELECT COUNT(*) FROM kalshi_markets")
    games_today = _count(
        "SELECT COUNT(*) FROM mlb_games WHERE game_date = ?", day
    )

    # --- Latest timestamps ------------------------------------------------
    latest_snapshot = _latest(
        "SELECT MAX(snapped_at) FROM kalshi_orderbook_snapshots"
    )
    latest_candidate = _latest(
        "SELECT MAX(created_at) FROM candidate_events WHERE created_at LIKE ?", prefix_wild
    )
    latest_game_state = _latest(
        "SELECT MAX(checked_at) FROM mlb_game_states WHERE checked_at LIKE ?", prefix_wild
    )

    # --- Tape label breakdown (from candidate created_at → nearby snaps) --
    # Lightweight: just count snapshots per date prefix by whether any exist
    snapshots_total = _count("SELECT COUNT(*) FROM kalshi_orderbook_snapshots")

    # --- Warnings ---------------------------------------------------------
    warnings = []
    if not candidates_today:
        warnings.append("No candidates for today — is live_watcher running?")
    if not snapshots_today:
        warnings.append("No Kalshi snapshots for today — is orderbook recorder running during games?")
    if not game_states_today:
        warnings.append("No MLB game states for today — is mlb_poller running?")
    if not kalshi_markets_total:
        warnings.append("No Kalshi markets in DB — run kalshi_discover.py --all")

    # --- Readiness --------------------------------------------------------
    if game_states_today is None or snapshots_today is None:
        readiness = "blocked"
    elif not game_states_total and not snapshots_total:
        readiness = "stale"
    elif not game_states_today and not snapshots_today:
        readiness = "stale"
    elif candidates_today and snapshots_today:
        readiness = "ready"
    elif game_states_today or games_today:
        readiness = "partial"
    else:
        readiness = "stale"

    return {
        "date": day,
        "db_path": db_path,
        "readiness": readiness,
        "candidates_today": candidates_today,
        "snapshots_today": snapshots_today,
        "snapshots_total": snapshots_total,
        "game_states_today": game_states_today,
        "game_states_total": game_states_total,
        "games_today": games_today,
        "kalshi_markets_total": kalshi_markets_total,
        "latest_snapshot": latest_snapshot,
        "latest_candidate": latest_candidate,
        "latest_game_state": latest_game_state,
        "warnings": warnings,
    }
```

---

## Step 3 — Implement `api/routers/slate_health.py`

```python
"""
api/routers/slate_health.py — GET /api/mlb/slate-health?date=YYYY-MM-DD

Lightweight operational health check. Read-only. No candidate changes.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, DB_PATH
from mlb.slate_health import get_slate_health

router = APIRouter()


@router.get("/mlb/slate-health")
def slate_health_endpoint(
    date_str: Optional[str] = Query(default=None, alias="date",
                                    description="YYYY-MM-DD (defaults to today)"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return get_slate_health(db, day, db_path=DB_PATH)
```

---

## Step 4 — Register router in `api/main.py`

Add to imports and include_router:
```python
from api.routers import ... slate_health
app.include_router(slate_health.router, prefix=PREFIX, tags=["slate-health"])
```

---

## Step 5 — Write `docs/TOMORROW_SLATE_RUNBOOK.md`

Full runbook with:
- ordered startup commands
- smoke test sequence  
- what to expect at each step
- known limitations (no_tape expected if recorder not live during games)
- paper/review mode disclaimer

---

## Verification

```bash
python -m pytest tests/ -q          # must be 1729+ passing
npx tsc --noEmit                    # must be clean (no frontend changes)
python -c "
import sqlite3
from api.deps import DB_PATH
from mlb.slate_health import get_slate_health
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
import json, datetime
h = get_slate_health(conn, datetime.date.today().isoformat(), DB_PATH)
print(json.dumps(h, indent=2))
conn.close()
"
```
