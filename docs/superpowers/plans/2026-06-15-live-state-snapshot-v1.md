## Goal
Add a safe, export-only live-state JSON snapshot so our system can publish a dashboard-friendly summary without coupling any dashboard to the bot.

## Architecture
- `mlb/live_state_snapshot.py` calls `get_live_capture_monitor()` and (optionally) `build_post_slate_report()` to assemble a single dict
- `export_live_state.py` (repo root CLI) writes that dict atomically to `kalshi_output/live_state_output/live_state_mlb_YYYY-MM-DD.json`
- `api/routers/live_state_snapshot.py` exposes the same dict at `GET /api/mlb/live-state-snapshot`
- No live logic is touched; all reads flow through existing read-only helpers

## Tech Stack
- Python stdlib: `json`, `os`, `datetime`, `tempfile` (via `os.replace`)
- FastAPI router pattern matching existing routers
- pytest + in-memory SQLite for tests

---

## Files

| File | Status | Responsibility |
|------|--------|----------------|
| `mlb/live_state_snapshot.py` | CREATE | Build snapshot dict from monitor + optional report |
| `export_live_state.py` | CREATE | CLI: atomic write to disk, print summary |
| `api/routers/live_state_snapshot.py` | CREATE | GET /api/mlb/live-state-snapshot endpoint |
| `api/main.py` | MODIFY | Import and register new router |
| `tests/test_live_state_snapshot.py` | CREATE | All tests |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | MODIFY | Add export-during-slate section |

---

## Step 1 — `mlb/live_state_snapshot.py` (TDD)

### Test first (in `tests/test_live_state_snapshot.py`):
```python
from db.schema import init_db
from mlb.live_state_snapshot import build_live_state_snapshot, SCHEMA_VERSION

def _conn():
    c = init_db(":memory:")
    c.row_factory = __import__("sqlite3").Row
    return c

class TestSnapshotStructure:
    def test_empty_slate_does_not_crash(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap, dict)

    def test_has_schema_version(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["schema_version"] == SCHEMA_VERSION

    def test_has_generated_at_utc(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "generated_at_utc" in snap
        assert snap["generated_at_utc"]

    def test_has_slate_date(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["slate_date"] == "2099-02-01"

    def test_sport_is_mlb(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["sport"] == "mlb"

    def test_mode_is_paper_validation(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["mode"] == "paper_validation"

    def test_session_ended_is_bool(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["session_ended"], bool)

    def test_has_capture_readiness(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "capture_readiness" in snap

    def test_has_next_action(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "next_action" in snap

class TestSnapshotSections:
    def test_has_live_capture_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        lc = snap["live_capture"]
        assert "games_today" in lc
        assert "latest_mlb_game_state" in lc
        assert "latest_kalshi_snapshot" in lc

    def test_has_candidates_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        c = snap["candidates"]
        assert "total" in c
        assert "by_derivative_type" in c
        assert "by_status" in c

    def test_has_paper_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        p = snap["paper"]
        assert "total" in p
        assert "by_status" in p
        assert "with_entry_price" in p
        assert "no_entry_price" in p
        assert "good_entry_label_breakdown" in p

    def test_has_market_tape_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        mt = snap["market_tape"]
        assert "snapshots_in_window" in mt
        assert "candidates_with_usable_or_strong_tape" in mt
        assert "no_tape" in mt

    def test_has_weather_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        w = snap["weather"]
        assert "weather_rows" in w
        assert "weather_rows_open_meteo" in w
        assert "weather_rows_manual" in w
        assert "games_weather_missing" in w
        assert "weather_time_actual_count" in w
        assert "weather_time_estimated_count" in w

    def test_has_report_preview_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "report_preview" in snap

class TestSnapshotTolerance:
    def test_empty_slate_candidates_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["candidates"]["total"] == 0

    def test_empty_slate_paper_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["paper"]["total"] == 0

    def test_empty_slate_weather_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["weather"]["weather_rows"] == 0

    def test_report_preview_tolerates_no_setups(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["report_preview"], dict)

class TestReadOnly:
    def test_no_candidate_generation(self):
        import inspect, mlb.live_state_snapshot as m
        src = inspect.getsource(m)
        assert "generate_candidate" not in src
        assert "fire_candidate" not in src

    def test_no_good_entry_scoring_changes(self):
        import inspect, mlb.live_state_snapshot as m
        src = inspect.getsource(m)
        assert "compute_good_entry_eval" not in src

    def test_no_weather_scoring_changes(self):
        import inspect, mlb.live_state_snapshot as m
        src = inspect.getsource(m)
        assert "compute_wre" not in src
        assert "score_weather" not in src

    def test_no_take_labels(self):
        import inspect, mlb.live_state_snapshot as m
        src = inspect.getsource(m)
        assert "TAKE" not in src

    def test_no_real_order_execution(self):
        import inspect, mlb.live_state_snapshot as m
        src = inspect.getsource(m)
        assert "place_order" not in src
        assert "submit_order" not in src
```

### Implementation (`mlb/live_state_snapshot.py`):
```python
"""
mlb/live_state_snapshot.py — Live State Snapshot Export v1.

Builds a dashboard-friendly JSON snapshot for one slate date using
existing read-only helpers. Export-only: does not affect live logic,
candidate generation, scoring, or order execution.

No TAKE labels. No trades. No guardrail changes. No candidate generation.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from mlb.live_capture_monitor import get_live_capture_monitor

SCHEMA_VERSION = "mlb_live_state_v1"


def _safe_report_preview(conn: sqlite3.Connection, date_str: str) -> dict:
    try:
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, date_str)
        overview = report.get("overview", {})
        by_deriv = report.get("by_derivative", {})
        lessons = report.get("lessons", [])
        top_derivatives = [
            {
                "derivative_type": dt,
                "count": b.get("count", 0),
                "wins": b.get("wins", 0),
                "losses": b.get("losses", 0),
                "net_pnl_cents": b.get("net_pnl_cents", 0),
            }
            for dt, b in list(by_deriv.items())[:3]
        ]
        return {
            "total_net_pnl_cents": overview.get("total_net_pnl_cents"),
            "top_derivatives": top_derivatives,
            "lessons_count": len(lessons),
        }
    except Exception:
        return {}


def build_live_state_snapshot(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    Build a structured live-state snapshot for date_str.
    Read-only. No candidate generation. No scoring changes. No TAKE labels. No orders.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    monitor = get_live_capture_monitor(conn, date_str)
    report_preview = _safe_report_preview(conn, date_str)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "slate_date": date_str,
        "sport": "mlb",
        "mode": "paper_validation",
        "session_ended": False,
        "monitor_write_ts": None,
        "capture_readiness": monitor.get("capture_readiness", "blocked"),
        "next_action": monitor.get("next_action", ""),
        "live_capture": {
            "games_today": monitor.get("games_today", 0),
            "game_states_today": monitor.get("game_states_today", 0),
            "latest_mlb_game_state": monitor.get("latest_mlb_game_state"),
            "latest_kalshi_snapshot": monitor.get("latest_kalshi_snapshot"),
        },
        "candidates": {
            "total": monitor.get("candidates_today", 0),
            "by_derivative_type": monitor.get("candidates_by_derivative_type", {}),
            "by_status": monitor.get("candidates_by_status", {}),
        },
        "paper": {
            "total": monitor.get("paper_setups_today", 0),
            "by_status": monitor.get("paper_setups_by_status", {}),
            "with_entry_price": monitor.get("paper_setups_with_entry_price", 0),
            "no_entry_price": monitor.get("paper_setups_no_entry_price", 0),
            "good_entry_label_breakdown": monitor.get("good_entry_label_breakdown", {}),
        },
        "market_tape": {
            "latest_snapshot_at": monitor.get("latest_kalshi_snapshot"),
            "snapshots_in_window": monitor.get("snapshots_in_window", 0),
            "candidates_with_usable_or_strong_tape": monitor.get("candidates_with_usable_tape", 0),
            "no_tape": monitor.get("candidates_with_no_tape", 0),
        },
        "weather": {
            "weather_rows": monitor.get("weather_rows", 0),
            "weather_rows_open_meteo": monitor.get("weather_rows_open_meteo", 0),
            "weather_rows_manual": monitor.get("weather_rows_manual", 0),
            "games_weather_missing": monitor.get("games_weather_missing", 0),
            "weather_time_actual_count": monitor.get("weather_time_actual_count", 0),
            "weather_time_estimated_count": monitor.get("weather_time_estimated_count", 0),
        },
        "report_preview": report_preview,
    }
```

---

## Step 2 — `export_live_state.py` + atomic export tests (TDD)

### Tests to add to `tests/test_live_state_snapshot.py`:
```python
import json, os, tempfile
from export_live_state import _default_output_path, _atomic_write

class TestAtomicExport:
    def test_atomic_write_creates_valid_json(self, tmp_path):
        path = str(tmp_path / "snap.json")
        _atomic_write(path, {"key": "val"})
        with open(path) as f:
            data = json.load(f)
        assert data["key"] == "val"

    def test_atomic_write_overwrites(self, tmp_path):
        path = str(tmp_path / "snap.json")
        _atomic_write(path, {"v": 1})
        _atomic_write(path, {"v": 2})
        with open(path) as f:
            data = json.load(f)
        assert data["v"] == 2

    def test_atomic_write_creates_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "snap.json")
        _atomic_write(path, {})
        assert os.path.exists(path)

    def test_default_output_path_contains_date(self):
        p = _default_output_path("2026-06-15")
        assert "2026-06-15" in p
        assert p.endswith(".json")

class TestCLI:
    def test_cli_exits_zero(self, tmp_path):
        import subprocess, sys
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_cli_writes_json_file(self, tmp_path):
        import subprocess, sys
        out = str(tmp_path / "snap.json")
        subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True,
        )
        with open(out) as f:
            data = json.load(f)
        assert data["schema_version"] == "mlb_live_state_v1"

    def test_cli_prints_output_path(self, tmp_path):
        import subprocess, sys
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert out in result.stdout

    def test_cli_prints_readiness(self, tmp_path):
        import subprocess, sys
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert "readiness" in result.stdout
```

### Implementation (`export_live_state.py`):
```python
"""
export_live_state.py — Export live-state JSON snapshot to disk.

Writes an atomic snapshot for today's MLB slate to:
  kalshi_output/live_state_output/live_state_mlb_YYYY-MM-DD.json

Usage:
    python export_live_state.py --date 2026-06-15
    python export_live_state.py --date 2026-06-15 --out path/to/file.json

Read-only. No trades. No orders. No candidate generation. No scoring changes.
No TAKE labels.
"""
import argparse
import json
import os
import sys
from datetime import date

from db.schema import init_db
from mlb.live_state_snapshot import build_live_state_snapshot

OUTPUT_DIR = os.path.join("kalshi_output", "live_state_output")


def _default_output_path(date_str: str) -> str:
    return os.path.join(OUTPUT_DIR, f"live_state_mlb_{date_str}.json")


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export live-state JSON snapshot. Read-only, no trades."
    )
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--out", default=None, help="Output path (default: kalshi_output/live_state_output/...)")
    args = parser.parse_args()

    date_str = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    out_path = args.out or _default_output_path(date_str)

    conn = init_db(db_path)
    snapshot = build_live_state_snapshot(conn, date_str)
    conn.close()

    _atomic_write(out_path, snapshot)

    gel = snapshot["paper"]["good_entry_label_breakdown"]
    gel_str = ", ".join(f"{k}={v}" for k, v in gel.items()) if gel else "none"

    print(f"[export_live_state] date={date_str}")
    print(f"  output:      {out_path}")
    print(f"  generated:   {snapshot['generated_at_utc']}")
    print(f"  readiness:   {snapshot['capture_readiness']}")
    print(f"  candidates:  {snapshot['candidates']['total']}")
    print(f"  paper:       {snapshot['paper']['total']}")
    print(f"  good_entry:  {gel_str}")
    print(f"  weather:     {snapshot['weather']['weather_rows']} row(s)")


if __name__ == "__main__":
    main()
```

---

## Step 3 — API router + main.py (TDD)

### Tests to add:
```python
from fastapi.testclient import TestClient
from fastapi import FastAPI
from api.routers.live_state_snapshot import router

class TestAPIRoute:
    def _client(self):
        from db.schema import init_db
        import sqlite3
        _app = FastAPI()
        _app.include_router(router, prefix="/api")

        def override_db():
            c = init_db(":memory:")
            c.row_factory = sqlite3.Row
            try:
                yield c
            finally:
                c.close()

        from api.deps import get_db
        _app.dependency_overrides[get_db] = override_db
        return TestClient(_app)

    def test_returns_200(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert r.status_code == 200

    def test_returns_schema_version(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert r.json()["schema_version"] == "mlb_live_state_v1"

    def test_returns_candidates_section(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert "candidates" in r.json()

    def test_defaults_to_today_without_date(self):
        r = self._client().get("/api/mlb/live-state-snapshot")
        assert r.status_code == 200
        from datetime import date
        assert r.json()["slate_date"] == date.today().isoformat()
```

### Implementation (`api/routers/live_state_snapshot.py`):
```python
"""
api/routers/live_state_snapshot.py — Live State Snapshot endpoint.

GET /api/mlb/live-state-snapshot?date=YYYY-MM-DD

Read-only. No candidate generation. No TAKE labels. No orders.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.live_state_snapshot import build_live_state_snapshot

router = APIRouter()


@router.get("/mlb/live-state-snapshot")
def live_state_snapshot(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return build_live_state_snapshot(db, day)
```

### `api/main.py` modification — add to import line and `app.include_router`:
- Import: add `live_state_snapshot` to the import line
- Register: `app.include_router(live_state_snapshot.router, prefix=PREFIX, tags=["live-state"])`

---

## Step 4 — Runbook update

Add before "After the Slate" section:

```markdown
### J — Export live-state snapshot (optional, during slate)

At any point during the slate, export a dashboard-friendly JSON snapshot:

```bash
python export_live_state.py --date 2026-06-15
```

Output: `kalshi_output/live_state_output/live_state_mlb_2026-06-15.json`

This snapshot is decoupled from the live pipeline and does not affect candidate
generation, scoring, or order execution. Re-run at any time to refresh.

**Via API** (if API is running):
```bash
curl "http://localhost:8000/api/mlb/live-state-snapshot?date=2026-06-15"
```
```

---

## Quality Checks
- [x] Every step has exact file paths
- [x] No "TBD" or placeholders in code
- [x] Type/method names consistent across all steps
- [x] All read-only constraints maintained
- [x] Full test coverage: structure, sections, tolerance, atomic export, CLI, API, read-only
