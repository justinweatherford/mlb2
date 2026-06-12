# Plan: MLB Stats API Backbone

## Goal
Add a complete MLB Stats API integration: raw HTTP client, JSONL raw logging, normalized DB tables (`mlb_games`, `mlb_game_states`, `mlb_play_events`), fetch/reconcile orchestrators, CLI scripts, FastAPI endpoints, and a React games page — enabling `python mlb_fetch.py --gamePk 823215` followed by `python reconcile_mlb.py --gamePk 823215` to store official game data and settle paper positions.

## Architecture

```
statsapi.mlb.com (public, no auth)
        │  raw HTTP via httpx
        ▼
mlb/stats_api.py            ← fetch_schedule / fetch_game_feed / fetch_linescore /
                               fetch_play_by_play / fetch_boxscore / get_game_status /
                               get_final_score / get_final_total / is_game_final
        │  raw dicts
        │
        ├──► mlb/jsonl_logger.py   ← data/raw/mlb/YYYY-MM-DD/{name}.jsonl
        │
        └──► mlb/game_store.py     ← fetch_and_store_schedule / fetch_and_store_game
                    │  upserts mlb_games, mlb_game_states, mlb_play_events
                    ▼
             mlb/reconciler.py     ← reconcile_game_final / reconcile_all_unsettled_games
                    │  reads mlb_games, settles paper_positions + pace_fade_training_rows
                    ▼
             (existing DB tables)

CLI:   mlb_fetch.py    -- --date YYYY-MM-DD | --gamePk N
       reconcile_mlb.py -- --gamePk N | --all-unsettled

API:   api/routers/mlb.py   GET /api/mlb/games[/{pk}]  GET /api/mlb/games/{pk}/plays
                             POST /api/mlb/reconcile[/{pk}]

UI:    frontend/src/pages/MLBGames.tsx   (new Games page in nav)
```

## Tech Stack
- `httpx` (already in requirements) — direct HTTP to statsapi.mlb.com
- Existing SQLite WAL-mode DB + `db/schema.py` + `db/repository.py`
- Existing FastAPI + React/react-query stack

---

## File Map

| File | Change |
|------|--------|
| `db/schema.py` | add `mlb_games`, `mlb_game_states`, `mlb_play_events` DDL + migrations |
| `mlb/stats_api.py` | **new** — raw HTTP client, 9 public functions |
| `mlb/jsonl_logger.py` | **new** — JSONL append logger |
| `mlb/game_store.py` | **new** — fetch + log + DB orchestrator |
| `mlb/reconciler.py` | **new** — final-game settlement logic |
| `mlb_fetch.py` | **new** — CLI script |
| `reconcile_mlb.py` | **new** — CLI script |
| `api/routers/mlb.py` | **new** — FastAPI router |
| `api/main.py` | add mlb router import + include |
| `frontend/src/types/api.ts` | add `MLBGame`, `MLBPlayEvent`, `ReconcileResult` |
| `frontend/src/api/client.ts` | add `mlb.*` methods |
| `frontend/src/pages/MLBGames.tsx` | **new** — React games page |
| `frontend/src/App.tsx` | add `/mlb-games` route |
| `frontend/src/components/Layout.tsx` | add Games nav item |
| `tests/test_mlb_stats_api.py` | **new** — HTTP client unit tests |
| `tests/test_mlb_game_store.py` | **new** — game store + JSONL tests |
| `tests/test_mlb_reconciler.py` | **new** — reconciler settlement tests |

---

## Task 1 — DB Schema: three new tables

### TDD cycle
```
[ ] Write tests/test_mlb_schema.py (verify tables created by init_db)
[ ] Add DDL + migration to db/schema.py
[ ] Run pytest tests/test_mlb_schema.py -x → pass
[ ] Run full suite → no regressions
[ ] Commit
```

### `db/schema.py` additions (append inside `DDL` string, after existing mlb_game_snapshots block)

```sql
-- ── MLB normalized game data ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_games (
    game_pk          INTEGER PRIMARY KEY,
    game_date        TEXT NOT NULL,
    away_team        TEXT NOT NULL,
    home_team        TEXT NOT NULL,
    away_abbr        TEXT NOT NULL,
    home_abbr        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'Scheduled',
    game_id          TEXT,
    final_away_score INTEGER,
    final_home_score INTEGER,
    final_total      INTEGER,
    is_final         INTEGER NOT NULL DEFAULT 0,
    last_checked_at  TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mlb_games_date ON mlb_games(game_date);
CREATE INDEX IF NOT EXISTS idx_mlb_games_final ON mlb_games(is_final);

CREATE TABLE IF NOT EXISTS mlb_game_states (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk          INTEGER NOT NULL,
    checked_at       TEXT NOT NULL,
    status           TEXT,
    inning           INTEGER,
    inning_half      TEXT,
    outs             INTEGER,
    away_score       INTEGER,
    home_score       INTEGER,
    balls            INTEGER,
    strikes          INTEGER,
    runner_state     TEXT,
    current_batter   TEXT,
    current_pitcher  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mlb_game_states_pk ON mlb_game_states(game_pk, checked_at);

CREATE TABLE IF NOT EXISTS mlb_play_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk          INTEGER NOT NULL,
    at_bat_index     INTEGER NOT NULL,
    play_index       INTEGER NOT NULL DEFAULT 0,
    event_time       TEXT,
    inning           INTEGER,
    inning_half      TEXT,
    description      TEXT,
    event_type       TEXT,
    is_scoring_play  INTEGER NOT NULL DEFAULT 0,
    is_home_run      INTEGER NOT NULL DEFAULT 0,
    rbi              INTEGER NOT NULL DEFAULT 0,
    outs             INTEGER NOT NULL DEFAULT 0,
    away_score       INTEGER,
    home_score       INTEGER,
    batter_name      TEXT,
    pitcher_name     TEXT,
    raw_json         TEXT,
    UNIQUE(game_pk, at_bat_index, play_index)
);
CREATE INDEX IF NOT EXISTS idx_mlb_play_events_pk ON mlb_play_events(game_pk, inning);
```

Also add to `_apply_migrations` list in `db/schema.py`:

```python
"ALTER TABLE paper_positions ADD COLUMN settlement_status TEXT",
```

This adds `settlement_status TEXT` to existing DBs. Fresh DBs get it from the DDL below (add `settlement_status TEXT` to the `paper_positions` column list in the existing `CREATE TABLE IF NOT EXISTS paper_positions` block in `db/schema.py`).

`settlement_status` values:
- `NULL` — not yet reconciled (default)
- `'settled_confirmed'` — direction was known; position closed at win/loss price
- `'needs_review'` — push or unknown contract direction; position left open for manual review

### `tests/test_mlb_schema.py`

```python
"""tests/test_mlb_schema.py — Verify new MLB tables created by init_db."""
import sqlite3
from db.schema import init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def test_mlb_games_table_created():
    conn = init_db(":memory:")
    assert "mlb_games" in _tables(conn)
    conn.close()


def test_mlb_game_states_table_created():
    conn = init_db(":memory:")
    assert "mlb_game_states" in _tables(conn)
    conn.close()


def test_mlb_play_events_table_created():
    conn = init_db(":memory:")
    assert "mlb_play_events" in _tables(conn)
    conn.close()


def test_mlb_games_columns():
    conn = init_db(":memory:")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mlb_games)").fetchall()}
    expected = {
        "game_pk", "game_date", "away_team", "home_team",
        "away_abbr", "home_abbr", "status", "game_id",
        "final_away_score", "final_home_score", "final_total",
        "is_final", "last_checked_at", "created_at",
    }
    assert expected <= cols
    conn.close()


def test_mlb_play_events_unique_constraint():
    conn = init_db(":memory:")
    conn.execute(
        "INSERT INTO mlb_play_events (game_pk, at_bat_index, play_index) VALUES (1, 0, 0)"
    )
    conn.commit()
    # Second insert should be silently ignored by INSERT OR IGNORE
    conn.execute(
        "INSERT OR IGNORE INTO mlb_play_events (game_pk, at_bat_index, play_index) VALUES (1, 0, 0)"
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM mlb_play_events").fetchone()[0]
    assert count == 1
    conn.close()


def test_paper_positions_has_settlement_status_column():
    conn = init_db(":memory:")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
    assert "settlement_status" in cols
    conn.close()


def test_settlement_status_defaults_to_null():
    conn = init_db(":memory:")
    conn.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "pace_fade_under_candidate", 0.7, 1, "open",
         0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    )
    conn.commit()
    row = conn.execute("SELECT settlement_status FROM paper_positions").fetchone()
    assert row["settlement_status"] is None
    conn.close()
```

---

## Task 2 — HTTP Client + JSONL Logger

### TDD cycle
```
[ ] Write tests/test_mlb_stats_api.py (all mocked)
[ ] Run → FAIL (module not found)
[ ] Create mlb/stats_api.py + mlb/jsonl_logger.py
[ ] Run → pass
[ ] Full suite → no regressions
[ ] Commit
```

### `mlb/stats_api.py`

```python
"""
mlb/stats_api.py — Direct HTTP client for statsapi.mlb.com (no auth required).

Returns parsed dicts for downstream logging and storage.
Returns None on any network/parse error so callers never crash.
"""
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_BASE = "https://statsapi.mlb.com"
_TIMEOUT = 15.0


def _get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    url = f"{_BASE}{path}"
    try:
        resp = httpx.get(url, params=params or {}, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        log.warning("MLB API timeout: %s", url)
        return None
    except httpx.HTTPStatusError as exc:
        log.warning("MLB API HTTP %d: %s", exc.response.status_code, url)
        return None
    except Exception as exc:
        log.warning("MLB API error: %s — %s", url, exc)
        return None


def fetch_schedule(date_str: str) -> Optional[dict]:
    """GET /api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=team"""
    return _get("/api/v1/schedule", {"sportId": "1", "date": date_str, "hydrate": "team"})


def fetch_game_feed(game_pk: int) -> Optional[dict]:
    """GET /api/v1.1/game/{gamePk}/feed/live — full live data, scores, plays."""
    return _get(f"/api/v1.1/game/{game_pk}/feed/live")


def fetch_linescore(game_pk: int) -> Optional[dict]:
    """GET /api/v1/game/{gamePk}/linescore"""
    return _get(f"/api/v1/game/{game_pk}/linescore")


def fetch_play_by_play(game_pk: int) -> Optional[dict]:
    """GET /api/v1/game/{gamePk}/playByPlay"""
    return _get(f"/api/v1/game/{game_pk}/playByPlay")


def fetch_boxscore(game_pk: int) -> Optional[dict]:
    """GET /api/v1/game/{gamePk}/boxscore"""
    return _get(f"/api/v1/game/{game_pk}/boxscore")


def get_game_status(game_pk: int) -> Optional[str]:
    """Return abstractGameState string, or None if API unavailable."""
    data = fetch_game_feed(game_pk)
    if not data:
        return None
    return data.get("gameData", {}).get("status", {}).get("abstractGameState")


def get_final_score(game_pk: int) -> Optional[tuple[int, int]]:
    """Return (away_score, home_score) if abstractGameState == 'Final', else None."""
    data = fetch_game_feed(game_pk)
    if not data:
        return None
    if data.get("gameData", {}).get("status", {}).get("abstractGameState") != "Final":
        return None
    ls = data.get("liveData", {}).get("linescore", {}).get("teams", {})
    away = ls.get("away", {}).get("runs", 0) or 0
    home = ls.get("home", {}).get("runs", 0) or 0
    return (away, home)


def get_final_total(game_pk: int) -> Optional[int]:
    """Return combined runs if game is Final, else None."""
    score = get_final_score(game_pk)
    return None if score is None else score[0] + score[1]


def is_game_final(game_pk: int) -> bool:
    return get_game_status(game_pk) == "Final"
```

### `mlb/jsonl_logger.py`

```python
"""
mlb/jsonl_logger.py — Append raw MLB API responses to dated JSONL files.

Output:  data/raw/mlb/YYYY-MM-DD/{name}.jsonl
Format:  one JSON object per line: {"logged_at": "...", "data": {...}}
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_BASE = Path("data/raw/mlb")


def log_response(name: str, data: dict, date_str: Optional[str] = None) -> str:
    """Append one JSONL record. Returns the path written."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    path = _BASE / date_str / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"logged_at": datetime.now().isoformat(), "data": data}
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        log.warning("JSONL write failed: %s — %s", path, exc)
    return str(path)
```

### `tests/test_mlb_stats_api.py`

```python
"""tests/test_mlb_stats_api.py — MLB HTTP client unit tests (all mocked)."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mlb.stats_api import (
    fetch_schedule,
    fetch_game_feed,
    fetch_linescore,
    fetch_play_by_play,
    fetch_boxscore,
    get_game_status,
    get_final_score,
    get_final_total,
    is_game_final,
)
from mlb.jsonl_logger import log_response


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SCHEDULE_RESP = {
    "dates": [
        {
            "date": "2026-06-12",
            "games": [
                {
                    "gamePk": 747447,
                    "teams": {
                        "away": {"team": {"name": "New York Yankees", "abbreviation": "NYY"}},
                        "home": {"team": {"name": "Boston Red Sox", "abbreviation": "BOS"}},
                    },
                    "status": {"abstractGameState": "In Progress"},
                }
            ],
        }
    ]
}

_GAME_FEED_FINAL = {
    "gamePk": 747447,
    "gameData": {
        "teams": {
            "away": {"name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"name": "Boston Red Sox", "abbreviation": "BOS"},
        },
        "status": {"abstractGameState": "Final"},
        "datetime": {"officialDate": "2026-06-12"},
    },
    "liveData": {
        "plays": {"allPlays": []},
        "linescore": {
            "currentInning": 9,
            "inningHalf": "Bottom",
            "outs": 3,
            "balls": 0,
            "strikes": 0,
            "teams": {"away": {"runs": 5}, "home": {"runs": 3}},
            "offense": {},
            "defense": {"pitcher": {"fullName": "Clay Holmes"}},
        },
        "boxscore": {},
    },
}

_GAME_FEED_LIVE = {
    "gamePk": 747447,
    "gameData": {
        "teams": {
            "away": {"name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"name": "Boston Red Sox", "abbreviation": "BOS"},
        },
        "status": {"abstractGameState": "In Progress"},
        "datetime": {"officialDate": "2026-06-12"},
    },
    "liveData": {
        "plays": {"allPlays": []},
        "linescore": {
            "currentInning": 4,
            "inningHalf": "Top",
            "outs": 1,
            "balls": 2,
            "strikes": 1,
            "teams": {"away": {"runs": 2}, "home": {"runs": 1}},
            "offense": {"first": {"id": 123}},
            "defense": {"pitcher": {"fullName": "Chris Sale"}},
        },
    },
}


def _mock_response(data: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


# ── fetch_schedule ────────────────────────────────────────────────────────────

def test_fetch_schedule_returns_dict():
    with patch("mlb.stats_api.httpx.get", return_value=_mock_response(_SCHEDULE_RESP)):
        result = fetch_schedule("2026-06-12")
    assert result is not None
    assert "dates" in result


def test_fetch_schedule_passes_hydrate_param():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_SCHEDULE_RESP)
        fetch_schedule("2026-06-12")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["hydrate"] == "team"


def test_fetch_schedule_returns_none_on_timeout():
    with patch("mlb.stats_api.httpx.get", side_effect=httpx.TimeoutException("t/o")):
        result = fetch_schedule("2026-06-12")
    assert result is None


def test_fetch_schedule_returns_none_on_http_error():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 404
    with patch("mlb.stats_api.httpx.get",
               side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=resp)):
        result = fetch_schedule("2026-06-12")
    assert result is None


# ── fetch_game_feed ───────────────────────────────────────────────────────────

def test_fetch_game_feed_returns_dict():
    with patch("mlb.stats_api.httpx.get", return_value=_mock_response(_GAME_FEED_FINAL)):
        result = fetch_game_feed(747447)
    assert result is not None
    assert result["gamePk"] == 747447


def test_fetch_game_feed_none_on_network_error():
    with patch("mlb.stats_api.httpx.get", side_effect=Exception("network down")):
        result = fetch_game_feed(747447)
    assert result is None


# ── get_game_status / get_final_score / get_final_total / is_game_final ───────

def test_get_game_status_final():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        assert get_game_status(747447) == "Final"


def test_get_game_status_live():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_LIVE):
        assert get_game_status(747447) == "In Progress"


def test_get_game_status_none_when_api_fails():
    with patch("mlb.stats_api.fetch_game_feed", return_value=None):
        assert get_game_status(747447) is None


def test_get_final_score_returns_tuple_when_final():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        score = get_final_score(747447)
    assert score == (5, 3)


def test_get_final_score_returns_none_when_in_progress():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_LIVE):
        assert get_final_score(747447) is None


def test_get_final_total_when_final():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        assert get_final_total(747447) == 8


def test_is_game_final_true():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        assert is_game_final(747447) is True


def test_is_game_final_false_when_live():
    with patch("mlb.stats_api.fetch_game_feed", return_value=_GAME_FEED_LIVE):
        assert is_game_final(747447) is False


# ── jsonl_logger ──────────────────────────────────────────────────────────────

def test_log_response_creates_file(tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    path = log_response("schedule", {"foo": "bar"}, "2026-06-12")
    assert Path(path).exists()


def test_log_response_appends_valid_json(tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    log_response("game_feed", {"gamePk": 123}, "2026-06-12")
    log_response("game_feed", {"gamePk": 456}, "2026-06-12")
    lines = (tmp_path / "2026-06-12" / "game_feed.jsonl").read_text().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert "logged_at" in record
    assert record["data"]["gamePk"] == 123


def test_log_response_uses_today_when_no_date(tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    from datetime import datetime
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    log_response("linescore", {"x": 1})
    assert (tmp_path / today / "linescore.jsonl").exists()
```

---

## Task 3 — Game Store

### TDD cycle
```
[ ] Write tests/test_mlb_game_store.py
[ ] Run → FAIL (module not found)
[ ] Create mlb/game_store.py
[ ] Run → pass
[ ] Full suite → no regressions
[ ] Commit
```

### `mlb/game_store.py`

```python
"""
mlb/game_store.py — Fetch MLB game data from statsapi, log raw, write to DB.

Public API:
  fetch_and_store_schedule(conn, date_str) → list[int]   game_pk values stored
  fetch_and_store_game(conn, game_pk)      → bool         True if game is final
"""
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from mlb.stats_api import fetch_schedule, fetch_game_feed
from mlb.jsonl_logger import log_response

log = logging.getLogger(__name__)

# Abbreviation normalisation (same rules as stats_client._normalise)
_ABBREV_MAP = {"WSH": "WSN", "CWS": "CWS", "ATH": "ATH"}


def _norm(abbr: str) -> str:
    return _ABBREV_MAP.get(abbr.upper(), abbr.upper())


def _now() -> str:
    return datetime.now().isoformat()


def _runner_state(offense: dict) -> str:
    parts = []
    if offense.get("first"):
        parts.append("1B")
    if offense.get("second"):
        parts.append("2B")
    if offense.get("third"):
        parts.append("3B")
    return "_".join(parts)


def fetch_and_store_schedule(conn: sqlite3.Connection, date_str: str) -> list[int]:
    """
    Fetch schedule for date_str, log raw JSON, upsert mlb_games rows.
    Returns list of game_pk integers found.
    """
    data = fetch_schedule(date_str)
    if not data:
        log.warning("fetch_and_store_schedule(%s): no data from API", date_str)
        return []

    log_response("schedule", data, date_str)

    game_pks: list[int] = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            game_pk = g.get("gamePk")
            if not game_pk:
                continue

            away = g.get("teams", {}).get("away", {}).get("team", {})
            home = g.get("teams", {}).get("home", {}).get("team", {})
            away_abbr = _norm(away.get("abbreviation") or away.get("name", "???")[:3].upper())
            home_abbr = _norm(home.get("abbreviation") or home.get("name", "???")[:3].upper())
            status = g.get("status", {}).get("abstractGameState", "Scheduled")
            now = _now()

            conn.execute(
                """INSERT INTO mlb_games
                   (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
                    status, game_id, is_final, last_checked_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?)
                   ON CONFLICT(game_pk) DO UPDATE SET
                       status=excluded.status,
                       last_checked_at=excluded.last_checked_at""",
                (
                    game_pk,
                    date_str,
                    away.get("name", away_abbr),
                    home.get("name", home_abbr),
                    away_abbr,
                    home_abbr,
                    status,
                    f"{away_abbr}@{home_abbr}",
                    now,
                    now,
                ),
            )
            game_pks.append(game_pk)

    conn.commit()
    log.info("fetch_and_store_schedule(%s): stored %d games", date_str, len(game_pks))
    return game_pks


def fetch_and_store_game(conn: sqlite3.Connection, game_pk: int) -> bool:
    """
    Fetch live game feed, log raw JSON, upsert mlb_games + mlb_game_states + mlb_play_events.
    Returns True if the game is Final.
    """
    data = fetch_game_feed(game_pk)
    if not data:
        log.warning("fetch_and_store_game(%d): no data from API", game_pk)
        return False

    game_data = data.get("gameData", {})
    live_data = data.get("liveData", {})
    linescore = live_data.get("linescore", {})

    date_str = game_data.get("datetime", {}).get("officialDate") or _now()[:10]
    log_response("game_feed", data, date_str)

    # Status
    status = game_data.get("status", {}).get("abstractGameState", "Unknown")
    is_final = 1 if status == "Final" else 0

    # Teams
    teams = game_data.get("teams", {})
    away_abbr = _norm((teams.get("away") or {}).get("abbreviation", "???"))
    home_abbr = _norm((teams.get("home") or {}).get("abbreviation", "???"))
    away_name = (teams.get("away") or {}).get("name", away_abbr)
    home_name = (teams.get("home") or {}).get("name", home_abbr)

    # Score
    ls_teams = linescore.get("teams", {})
    away_score = (ls_teams.get("away") or {}).get("runs", 0) or 0
    home_score = (ls_teams.get("home") or {}).get("runs", 0) or 0

    # Game state
    inning = linescore.get("currentInning", 1)
    inning_half = linescore.get("inningHalf", "Top").lower()
    outs = linescore.get("outs", 0) or 0
    balls = linescore.get("balls", 0) or 0
    strikes = linescore.get("strikes", 0) or 0
    offense = linescore.get("offense") or {}
    runner_state = _runner_state(offense)
    batter = (offense.get("batter") or {}).get("fullName", "") or ""
    pitcher = ((linescore.get("defense") or {}).get("pitcher") or {}).get("fullName", "") or ""

    now = _now()

    conn.execute(
        """INSERT INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            status, game_id, final_away_score, final_home_score, final_total,
            is_final, last_checked_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_pk) DO UPDATE SET
               status=excluded.status,
               away_abbr=excluded.away_abbr,
               home_abbr=excluded.home_abbr,
               final_away_score=COALESCE(excluded.final_away_score, mlb_games.final_away_score),
               final_home_score=COALESCE(excluded.final_home_score, mlb_games.final_home_score),
               final_total=COALESCE(excluded.final_total, mlb_games.final_total),
               is_final=MAX(excluded.is_final, mlb_games.is_final),
               last_checked_at=excluded.last_checked_at""",
        (
            game_pk, date_str, away_name, home_name, away_abbr, home_abbr,
            status, f"{away_abbr}@{home_abbr}",
            away_score if is_final else None,
            home_score if is_final else None,
            (away_score + home_score) if is_final else None,
            is_final, now, now,
        ),
    )

    conn.execute(
        """INSERT INTO mlb_game_states
           (game_pk, checked_at, status, inning, inning_half, outs,
            away_score, home_score, balls, strikes, runner_state,
            current_batter, current_pitcher)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (game_pk, now, status, inning, inning_half, outs,
         away_score, home_score, balls, strikes, runner_state, batter, pitcher),
    )

    plays = live_data.get("plays", {}).get("allPlays", [])
    for play in plays:
        about = play.get("about") or {}
        result = play.get("result") or {}
        matchup = play.get("matchup") or {}
        play_events = play.get("playEvents") or []

        at_bat_idx = about.get("atBatIndex", 0)
        last_pitch_time = play_events[-1].get("startTime") if play_events else None

        conn.execute(
            """INSERT OR IGNORE INTO mlb_play_events
               (game_pk, at_bat_index, play_index, event_time, inning, inning_half,
                description, event_type, is_scoring_play, is_home_run, rbi, outs,
                away_score, home_score, batter_name, pitcher_name, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                game_pk,
                at_bat_idx,
                len(play_events),
                last_pitch_time,
                about.get("inning"),
                "top" if about.get("isTopInning", True) else "bottom",
                result.get("description"),
                result.get("event"),
                1 if about.get("isScoringPlay") else 0,
                1 if result.get("event") == "Home Run" else 0,
                result.get("rbi", 0) or 0,
                (play.get("count") or {}).get("outs", 0) or 0,
                result.get("awayScore"),
                result.get("homeScore"),
                (matchup.get("batter") or {}).get("fullName"),
                (matchup.get("pitcher") or {}).get("fullName"),
                json.dumps(result),
            ),
        )

    conn.commit()
    log.info(
        "fetch_and_store_game(%d): status=%s score=%d-%d plays=%d",
        game_pk, status, away_score, home_score, len(plays),
    )
    return bool(is_final)
```

### `tests/test_mlb_game_store.py`

```python
"""tests/test_mlb_game_store.py — fetch_and_store_* unit tests."""
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from db.schema import init_db
from mlb.game_store import fetch_and_store_schedule, fetch_and_store_game


@pytest.fixture
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


_SCHEDULE_DATA = {
    "dates": [
        {
            "date": "2026-06-12",
            "games": [
                {
                    "gamePk": 747447,
                    "teams": {
                        "away": {"team": {"name": "New York Yankees", "abbreviation": "NYY"}},
                        "home": {"team": {"name": "Boston Red Sox", "abbreviation": "BOS"}},
                    },
                    "status": {"abstractGameState": "In Progress"},
                },
                {
                    "gamePk": 747448,
                    "teams": {
                        "away": {"team": {"name": "Los Angeles Dodgers", "abbreviation": "LAD"}},
                        "home": {"team": {"name": "San Francisco Giants", "abbreviation": "SFG"}},
                    },
                    "status": {"abstractGameState": "Scheduled"},
                },
            ],
        }
    ]
}

_GAME_FEED_FINAL = {
    "gamePk": 747447,
    "gameData": {
        "teams": {
            "away": {"name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"name": "Boston Red Sox", "abbreviation": "BOS"},
        },
        "status": {"abstractGameState": "Final"},
        "datetime": {"officialDate": "2026-06-12"},
    },
    "liveData": {
        "plays": {
            "allPlays": [
                {
                    "result": {
                        "event": "Home Run",
                        "description": "Judge homers (2-run)",
                        "rbi": 2,
                        "awayScore": 2,
                        "homeScore": 0,
                    },
                    "about": {
                        "atBatIndex": 0,
                        "halfInning": "top",
                        "isTopInning": True,
                        "inning": 3,
                        "isScoringPlay": True,
                    },
                    "matchup": {
                        "batter": {"fullName": "Aaron Judge"},
                        "pitcher": {"fullName": "Chris Sale"},
                    },
                    "count": {"outs": 0},
                    "playEvents": [{"startTime": "2026-06-12T20:35:00Z"}],
                }
            ]
        },
        "linescore": {
            "currentInning": 9,
            "inningHalf": "Bottom",
            "outs": 3,
            "balls": 0,
            "strikes": 0,
            "teams": {"away": {"runs": 5}, "home": {"runs": 3}},
            "offense": {},
            "defense": {"pitcher": {"fullName": "Clay Holmes"}},
        },
    },
}

_GAME_FEED_LIVE = {
    "gamePk": 747447,
    "gameData": {
        "teams": {
            "away": {"name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"name": "Boston Red Sox", "abbreviation": "BOS"},
        },
        "status": {"abstractGameState": "In Progress"},
        "datetime": {"officialDate": "2026-06-12"},
    },
    "liveData": {
        "plays": {"allPlays": []},
        "linescore": {
            "currentInning": 4,
            "inningHalf": "Top",
            "outs": 1,
            "balls": 2,
            "strikes": 1,
            "teams": {"away": {"runs": 2}, "home": {"runs": 1}},
            "offense": {"first": {"id": 123}},
            "defense": {"pitcher": {"fullName": "Chris Sale"}},
        },
    },
}


# ── fetch_and_store_schedule ──────────────────────────────────────────────────

def test_schedule_stores_two_games(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_schedule", return_value=_SCHEDULE_DATA):
        pks = fetch_and_store_schedule(db, "2026-06-12")
    assert set(pks) == {747447, 747448}
    row = db.execute("SELECT * FROM mlb_games WHERE game_pk=747447").fetchone()
    assert row["away_abbr"] == "NYY"
    assert row["game_id"] == "NYY@BOS"


def test_schedule_returns_empty_on_api_failure(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_schedule", return_value=None):
        pks = fetch_and_store_schedule(db, "2026-06-12")
    assert pks == []


def test_schedule_upsert_is_idempotent(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_schedule", return_value=_SCHEDULE_DATA):
        fetch_and_store_schedule(db, "2026-06-12")
        fetch_and_store_schedule(db, "2026-06-12")
    count = db.execute("SELECT COUNT(*) FROM mlb_games").fetchone()[0]
    assert count == 2


def test_schedule_logs_jsonl(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_schedule", return_value=_SCHEDULE_DATA):
        fetch_and_store_schedule(db, "2026-06-12")
    assert (tmp_path / "2026-06-12" / "schedule.jsonl").exists()


# ── fetch_and_store_game — final ──────────────────────────────────────────────

def test_game_store_returns_true_when_final(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        is_final = fetch_and_store_game(db, 747447)
    assert is_final is True


def test_game_store_final_scores_stored(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        fetch_and_store_game(db, 747447)
    row = db.execute("SELECT * FROM mlb_games WHERE game_pk=747447").fetchone()
    assert row["final_away_score"] == 5
    assert row["final_home_score"] == 3
    assert row["final_total"] == 8
    assert row["is_final"] == 1


def test_game_store_play_events_stored(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        fetch_and_store_game(db, 747447)
    plays = db.execute(
        "SELECT * FROM mlb_play_events WHERE game_pk=747447"
    ).fetchall()
    assert len(plays) == 1
    assert plays[0]["event_type"] == "Home Run"
    assert plays[0]["is_home_run"] == 1
    assert plays[0]["is_scoring_play"] == 1
    assert plays[0]["rbi"] == 2
    assert plays[0]["batter_name"] == "Aaron Judge"


def test_game_store_play_events_idempotent(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_FINAL):
        fetch_and_store_game(db, 747447)
        fetch_and_store_game(db, 747447)
    count = db.execute(
        "SELECT COUNT(*) FROM mlb_play_events WHERE game_pk=747447"
    ).fetchone()[0]
    assert count == 1


# ── fetch_and_store_game — live (not final) ───────────────────────────────────

def test_game_store_returns_false_when_live(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_LIVE):
        is_final = fetch_and_store_game(db, 747447)
    assert is_final is False


def test_game_store_live_no_final_scores(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_LIVE):
        fetch_and_store_game(db, 747447)
    row = db.execute("SELECT * FROM mlb_games WHERE game_pk=747447").fetchone()
    assert row["is_final"] == 0
    assert row["final_total"] is None


def test_game_store_runner_state_stored(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=_GAME_FEED_LIVE):
        fetch_and_store_game(db, 747447)
    state = db.execute(
        "SELECT * FROM mlb_game_states WHERE game_pk=747447"
    ).fetchone()
    assert state["runner_state"] == "1B"
    assert state["balls"] == 2
    assert state["strikes"] == 1


def test_game_store_returns_false_on_api_failure(db, tmp_path, monkeypatch):
    import mlb.jsonl_logger as jl
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    with patch("mlb.game_store.fetch_game_feed", return_value=None):
        is_final = fetch_and_store_game(db, 747447)
    assert is_final is False
```

---

## Task 4 — Reconciler

### TDD cycle
```
[ ] Write tests/test_mlb_reconciler.py
[ ] Run → FAIL (module not found)
[ ] Create mlb/reconciler.py
[ ] Run → pass
[ ] Full suite → no regressions
[ ] Commit
```

### Contract direction semantics

Before settling a position, the reconciler must determine whether YES means "over the line" or "under the line" for that specific market. This is not globally constant — Kalshi phrases some markets as "Over X runs?" (YES=over) and some as "Under X runs?" (YES=under). Hardcoding either direction silently misclassifies positions.

**Direction values:**
- `over_yes` — YES wins when `final_total > market_line`; NO wins when `final_total < market_line`
- `under_yes` — YES wins when `final_total < market_line`; NO wins when `final_total > market_line`
- `unknown` — cannot determine; do not settle

**Inference priority:**
1. Search `kalshi_markets` for a market matching `game_id` + `line_value`. Parse `title` + `rules_primary` for "over" / "under" keyword.
2. Fall back to `signal_type` keyword heuristic (`"under"` in signal_type → `under_yes`; `"over"` → `over_yes`).
3. If neither resolves: `unknown`.

**Push handling:**
When `final_total == market_line`, the exact Kalshi resolution rule may differ (many contracts resolve NO / push / partial). Without confirmed rule text, do NOT auto-settle — mark `settlement_status = 'needs_review'`.

**Outcome values from `_determine_outcome()`:**
- `'win'` — close at 100 cents; `settlement_status = 'settled_confirmed'`
- `'loss'` — close at 0 cents; `settlement_status = 'settled_confirmed'`
- `'push_or_unknown'` — leave position open; set `settlement_status = 'needs_review'` and record reason in `exit_reason`

### `mlb/reconciler.py`

```python
"""
mlb/reconciler.py — Settle paper positions and training rows after games go final.

Contract direction (over_yes / under_yes / unknown) is inferred per position
from the linked Kalshi market text before any settlement is attempted.
Positions with unknown direction or a push are marked needs_review and left open.
"""
import logging
import re
import sqlite3

from db.repository import close_paper_position, update_training_row_outcome
from mlb.game_store import fetch_and_store_game

log = logging.getLogger(__name__)

_SETTLE_WIN_CENTS  = 100
_SETTLE_LOSE_CENTS = 0
_SETTLE_FEE_CENTS  = 0

# Patterns to detect market phrasing from title / rules text
_OVER_RE  = re.compile(r'\bover\b',  re.I)
_UNDER_RE = re.compile(r'\bunder\b', re.I)

# market_types that represent total-runs contests (direction inference applies)
_TOTAL_MARKET_TYPES = ('full_game_total', 'team_total', 'f5_total')


def _infer_direction(pos: sqlite3.Row, conn: sqlite3.Connection) -> str:
    """
    Return 'over_yes', 'under_yes', or 'unknown' for a position.

    Step 1 — look up matching Kalshi market; parse title + rules for direction keyword.
    Step 2 — fall back to signal_type keyword heuristic.
    Step 3 — return 'unknown' if unresolved.
    """
    game_id    = pos["game_id"]
    market_line = pos["market_line"]

    row = conn.execute(
        """SELECT title, rules_primary FROM kalshi_markets
           WHERE game_id=? AND line_value=?
             AND market_type IN ('full_game_total','team_total','f5_total')
           LIMIT 1""",
        (game_id, market_line),
    ).fetchone()

    if row:
        text = " ".join(filter(None, [row["title"] or "", row["rules_primary"] or ""]))
        if _OVER_RE.search(text) and not _UNDER_RE.search(text):
            return "over_yes"
        if _UNDER_RE.search(text) and not _OVER_RE.search(text):
            return "under_yes"
        # Both keywords present (e.g. "over/under") — fall through to heuristic

    signal = (pos["signal_type"] or "").lower()
    if "under" in signal:
        return "under_yes"
    if "over" in signal:
        return "over_yes"

    return "unknown"


def _determine_outcome(
    direction: str, side: str, final_total: int, market_line: float
) -> str:
    """
    Return 'win', 'loss', or 'push_or_unknown'.

    push_or_unknown is returned when:
      - direction == 'unknown' (contract semantics unclear)
      - final_total == market_line (push; Kalshi resolution rule may vary)
    """
    if direction == "unknown":
        return "push_or_unknown"

    if final_total == market_line:
        return "push_or_unknown"

    if direction == "over_yes":
        over_wins = final_total > market_line
        return "win" if (side == "YES") == over_wins else "loss"

    # under_yes
    under_wins = final_total < market_line
    return "win" if (side == "YES") == under_wins else "loss"


def reconcile_game_final(conn: sqlite3.Connection, game_pk: int) -> dict:
    """
    Refresh game state and settle open positions/training rows if game is Final.
    Returns: {game_pk, is_final, final_total?, settled, needs_review}.
    """
    is_final = fetch_and_store_game(conn, game_pk)

    if not is_final:
        return {"game_pk": game_pk, "is_final": False, "settled": 0, "needs_review": 0}

    game_row = conn.execute(
        "SELECT * FROM mlb_games WHERE game_pk=?", (game_pk,)
    ).fetchone()

    if not game_row:
        log.warning("reconcile_game_final(%d): missing from DB after fetch", game_pk)
        return {"game_pk": game_pk, "is_final": True, "settled": 0, "needs_review": 0}

    final_total = game_row["final_total"]
    game_id     = game_row["game_id"]
    settled = needs_review = 0

    if game_id and final_total is not None:
        settled, needs_review = _settle_paper_positions(conn, game_id, final_total)
        _settle_training_rows(conn, game_id, final_total)

    log.info(
        "reconcile_game_final(%d): final_total=%s settled=%d needs_review=%d",
        game_pk, final_total, settled, needs_review,
    )
    return {
        "game_pk": game_pk,
        "is_final": True,
        "final_total": final_total,
        "settled": settled,
        "needs_review": needs_review,
    }


def reconcile_all_unsettled_games(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT game_pk FROM mlb_games WHERE is_final=0").fetchall()
    return [reconcile_game_final(conn, r["game_pk"]) for r in rows]


def _settle_paper_positions(
    conn: sqlite3.Connection, game_id: str, final_total: int
) -> tuple[int, int]:
    """
    Settle open positions for game_id. Returns (settled_count, needs_review_count).
    """
    positions = conn.execute(
        "SELECT * FROM paper_positions WHERE game_id=? AND status='open'",
        (game_id,),
    ).fetchall()

    settled = needs_review = 0

    for pos in positions:
        direction = _infer_direction(pos, conn)
        outcome   = _determine_outcome(direction, pos["side"], final_total, pos["market_line"])

        if outcome == "push_or_unknown":
            conn.execute(
                """UPDATE paper_positions
                   SET settlement_status=?, exit_reason=?, updated_at=datetime('now')
                   WHERE id=?""",
                (
                    "needs_review",
                    f"push_or_unknown:direction={direction},total={final_total},line={pos['market_line']}",
                    pos["id"],
                ),
            )
            conn.commit()
            needs_review += 1
            log.info(
                "needs_review pos %d direction=%s total=%d line=%.1f",
                pos["id"], direction, final_total, pos["market_line"],
            )
            continue

        exit_price = _SETTLE_WIN_CENTS if outcome == "win" else _SETTLE_LOSE_CENTS
        close_paper_position(
            conn,
            position_id=pos["id"],
            exit_price_cents=exit_price,
            exit_fee_cents=_SETTLE_FEE_CENTS,
            exit_reason=f"mlb_reconcile:direction={direction},total={final_total}",
            held_to_settlement=True,
        )
        conn.execute(
            "UPDATE paper_positions SET settlement_status='settled_confirmed' WHERE id=?",
            (pos["id"],),
        )
        conn.commit()
        settled += 1
        log.info(
            "settled pos %d direction=%s side=%s total=%d outcome=%s",
            pos["id"], direction, pos["side"], final_total, outcome,
        )

    return settled, needs_review


def _settle_training_rows(
    conn: sqlite3.Connection, game_id: str, final_total: int
) -> None:
    """
    Update unresolved pace_fade_training_rows. Skip push (exact line match).
    Training rows are always under-bets by construction, so final_total < line = under won.
    """
    rows = conn.execute(
        "SELECT * FROM pace_fade_training_rows WHERE game_id=? AND under_won IS NULL",
        (game_id,),
    ).fetchall()

    for row in rows:
        market_line = row["line"]
        if final_total == market_line:
            log.info("training row %d: push at line=%.1f — leaving unresolved", row["id"], market_line)
            continue
        under_won  = final_total < market_line
        entry_cents = row["estimated_under_entry"]
        net_pnl     = (100 - entry_cents) if under_won else -entry_cents
        update_training_row_outcome(
            conn,
            row_id=row["id"],
            final_total=final_total,
            under_won=under_won,
            net_pnl_if_under=net_pnl,
            label_source="mlb_reconcile",
            label_confidence=1.0,
        )
```

### `tests/test_mlb_reconciler.py`

```python
"""tests/test_mlb_reconciler.py — Reconciler settlement unit tests."""
import sqlite3
from unittest.mock import patch

import pytest

from db.schema import init_db
from mlb.reconciler import (
    reconcile_game_final,
    reconcile_all_unsettled_games,
    _infer_direction,
    _determine_outcome,
)


@pytest.fixture
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _insert_game(conn, game_pk, game_id, final_total, is_final=1):
    conn.execute(
        """INSERT INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            status, game_id, final_away_score, final_home_score, final_total,
            is_final, last_checked_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            game_pk, "2026-06-12", "NYY", "BOS", "NYY", "BOS",
            "Final" if is_final else "In Progress",
            game_id,
            final_total - 3 if final_total else None,
            3 if final_total else None,
            final_total,
            is_final,
            "2026-06-12T20:00:00",
            "2026-06-12T19:00:00",
        ),
    )
    conn.commit()


def _insert_position(conn, game_id, market_line, side, signal_type="pace_fade_under_candidate"):
    cur = conn.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "2026-06-12T19:00:00", game_id, market_line, side,
            45, 46, 2, 47,
            "test", signal_type, 0.7, 1, "open",
            0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00",
        ),
    )
    conn.commit()
    return cur.lastrowid


def _insert_kalshi_market(conn, game_id, line_value, title, market_type="full_game_total"):
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, game_id, line_value,
            match_confidence, raw_json, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            f"TEST-{game_id}-{line_value}", "EVT", market_type,
            title, game_id, line_value,
            "exact", "{}", "2026-06-12T00:00:00", "2026-06-12T00:00:00",
        ),
    )
    conn.commit()


# ── _determine_outcome unit tests ─────────────────────────────────────────────

def test_over_yes_wins_when_total_above_line():
    assert _determine_outcome("over_yes", "YES", 9, 8.5) == "win"


def test_over_yes_loses_when_total_below_line():
    assert _determine_outcome("over_yes", "YES", 8, 8.5) == "loss"


def test_over_yes_no_wins_when_total_below_line():
    """NO on an over_yes market wins when total is under the line."""
    assert _determine_outcome("over_yes", "NO", 8, 8.5) == "win"


def test_under_yes_wins_when_total_below_line():
    assert _determine_outcome("under_yes", "YES", 8, 8.5) == "win"


def test_under_yes_loses_when_total_above_line():
    assert _determine_outcome("under_yes", "YES", 9, 8.5) == "loss"


def test_under_yes_no_wins_when_total_above_line():
    assert _determine_outcome("under_yes", "NO", 9, 8.5) == "win"


def test_exact_line_is_push_or_unknown_regardless_of_direction():
    assert _determine_outcome("over_yes", "YES", 8, 8.0) == "push_or_unknown"
    assert _determine_outcome("under_yes", "YES", 8, 8.0) == "push_or_unknown"
    assert _determine_outcome("over_yes", "NO",  8, 8.0) == "push_or_unknown"


def test_unknown_direction_is_push_or_unknown():
    assert _determine_outcome("unknown", "YES", 9, 8.5) == "push_or_unknown"
    assert _determine_outcome("unknown", "NO",  8, 8.5) == "push_or_unknown"


# ── _infer_direction unit tests ───────────────────────────────────────────────

def test_infer_direction_from_kalshi_market_over(db):
    _insert_kalshi_market(db, "NYY@BOS", 8.5, "Will total runs be Over 8.5?")
    pos = db.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING *""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "some_signal", 0.7, 1, "open",
         0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    ).fetchone()
    assert _infer_direction(pos, db) == "over_yes"


def test_infer_direction_from_kalshi_market_under(db):
    _insert_kalshi_market(db, "NYY@BOS", 8.5, "Will total runs be Under 8.5?")
    pos = db.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING *""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "some_signal", 0.7, 1, "open",
         0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    ).fetchone()
    assert _infer_direction(pos, db) == "under_yes"


def test_infer_direction_fallback_to_signal_type(db):
    # No kalshi_market row — should fall back to signal_type heuristic
    pos = db.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING *""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "pace_fade_under_candidate", 0.7, 1, "open",
         0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    ).fetchone()
    assert _infer_direction(pos, db) == "under_yes"


def test_infer_direction_unknown_when_no_kalshi_market_no_signal_hint(db):
    pos = db.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING *""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "some_generic_signal", 0.7, 1, "open",
         0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    ).fetchone()
    assert _infer_direction(pos, db) == "unknown"


# ── Not-final game ────────────────────────────────────────────────────────────

def test_not_final_no_settlement(db):
    with patch("mlb.reconciler.fetch_and_store_game", return_value=False):
        result = reconcile_game_final(db, 747447)
    assert result["is_final"] is False
    assert result["settled"] == 0
    assert result["needs_review"] == 0


def test_not_final_open_positions_stay_open(db):
    _insert_position(db, "NYY@BOS", 8.5, "YES")
    with patch("mlb.reconciler.fetch_and_store_game", return_value=False):
        reconcile_game_final(db, 747447)
    row = db.execute("SELECT status FROM paper_positions").fetchone()
    assert row["status"] == "open"


# ── over_yes: YES wins when total > line ──────────────────────────────────────

def test_over_yes_position_settled_win(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=9)
    _insert_kalshi_market(db, "NYY@BOS", 8.5, "Over 8.5 runs tonight?")
    pos_id = _insert_position(db, "NYY@BOS", 8.5, "YES", signal_type="some_signal")

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        result = reconcile_game_final(db, 747447)

    assert result["settled"] == 1
    pos = db.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert pos["status"] == "settled"
    assert pos["exit_price_cents"] == 100
    assert pos["settlement_status"] == "settled_confirmed"


def test_over_yes_position_settled_loss(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=8)
    _insert_kalshi_market(db, "NYY@BOS", 8.5, "Over 8.5 runs tonight?")
    pos_id = _insert_position(db, "NYY@BOS", 8.5, "YES", signal_type="some_signal")

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        reconcile_game_final(db, 747447)

    pos = db.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert pos["exit_price_cents"] == 0
    assert pos["settlement_status"] == "settled_confirmed"


# ── under_yes: NO wins when total > line ─────────────────────────────────────

def test_under_yes_no_side_wins_when_total_above_line(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=9)
    _insert_kalshi_market(db, "NYY@BOS", 8.5, "Under 8.5 runs tonight?")
    pos_id = _insert_position(db, "NYY@BOS", 8.5, "NO", signal_type="some_signal")

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        reconcile_game_final(db, 747447)

    pos = db.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert pos["exit_price_cents"] == 100


# ── Push / exact line → needs_review ─────────────────────────────────────────

def test_exact_line_is_needs_review_not_loss(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=8)
    _insert_kalshi_market(db, "NYY@BOS", 8.0, "Over 8 runs tonight?")
    pos_id = _insert_position(db, "NYY@BOS", 8.0, "YES", signal_type="some_signal")

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        result = reconcile_game_final(db, 747447)

    assert result["needs_review"] == 1
    assert result["settled"] == 0
    pos = db.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert pos["status"] == "open"           # NOT closed
    assert pos["settlement_status"] == "needs_review"
    assert "push_or_unknown" in pos["exit_reason"]


# ── Unknown direction → needs_review ─────────────────────────────────────────

def test_unknown_direction_is_needs_review(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=9)
    # No kalshi_market row, signal_type has no direction hint
    pos_id = _insert_position(db, "NYY@BOS", 8.5, "YES", signal_type="some_generic_signal")

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        result = reconcile_game_final(db, 747447)

    assert result["needs_review"] == 1
    assert result["settled"] == 0
    pos = db.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert pos["status"] == "open"
    assert pos["settlement_status"] == "needs_review"


# ── Result dict structure ─────────────────────────────────────────────────────

def test_reconcile_result_has_needs_review_key(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=8)
    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        result = reconcile_game_final(db, 747447)
    assert "needs_review" in result
    assert result["game_pk"] == 747447
    assert result["is_final"] is True
    assert result["final_total"] == 8


# ── reconcile_all_unsettled_games ─────────────────────────────────────────────

def test_reconcile_all_only_targets_unsettled(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=8, is_final=0)
    _insert_game(db, 747448, "LAD@SFG", final_total=6, is_final=1)

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        results = reconcile_all_unsettled_games(db)

    assert len(results) == 1
    assert results[0]["game_pk"] == 747447


# ── Training row settlement ───────────────────────────────────────────────────

def test_training_row_settled_when_game_final(db):
    _insert_game(db, 747447, "NYY@BOS", final_total=8)
    db.execute(
        """INSERT INTO pace_fade_training_rows
           (game_pk, game_id, signal_timestamp, inning_half, inning_number,
            current_total, line, estimated_under_entry, line_cushion,
            pace_fade_score, early_explosion_score, line_cushion_score,
            under_entry_value_score, classification, run_env_tag, hr_env_tag,
            context_source, context_confidence, risk_flags_json, missing_context_json,
            label_source, label_confidence, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "747447", "NYY@BOS", "2026-06-12T19:30:00", "top", 3,
            5, 8.5, 45, 0.5,
            0.7, 0.6, 0.5, 0.4, "pace_fade_under_candidate",
            "MID", "MID", "placeholder", 0.0, "[]", "[]",
            "unresolved", 0.0,
            "2026-06-12T19:30:00", "2026-06-12T19:30:00",
        ),
    )
    db.commit()

    with patch("mlb.reconciler.fetch_and_store_game", return_value=True):
        reconcile_game_final(db, 747447)

    row = db.execute("SELECT * FROM pace_fade_training_rows").fetchone()
    assert row["under_won"] == 1   # 8 < 8.5
    assert row["final_total"] == 8
    assert row["label_source"] == "mlb_reconcile"
```

---

## Task 5 — CLI Scripts

### `mlb_fetch.py` (project root)

```python
#!/usr/bin/env python
"""
mlb_fetch.py — Fetch and store MLB schedule or live game data.

Usage:
    python mlb_fetch.py --date 2026-06-12
    python mlb_fetch.py --gamePk 823215
"""
import argparse
import logging
import sys

from config import load_config
from db.schema import init_db
from mlb.game_store import fetch_and_store_schedule, fetch_and_store_game

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch MLB game data from statsapi")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", metavar="YYYY-MM-DD", help="Fetch schedule for date")
    group.add_argument("--gamePk", type=int, metavar="GAMEPK", help="Fetch a specific game")
    args = parser.parse_args()

    cfg = load_config()
    conn = init_db(cfg.db_path)
    try:
        if args.date:
            pks = fetch_and_store_schedule(conn, args.date)
            log.info("Stored %d game(s) for %s", len(pks), args.date)
        else:
            is_final = fetch_and_store_game(conn, args.gamePk)
            log.info("gamePk=%d stored (is_final=%s)", args.gamePk, is_final)
    except Exception as exc:
        log.error("Fatal: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

### `reconcile_mlb.py` (project root)

```python
#!/usr/bin/env python
"""
reconcile_mlb.py — Reconcile final MLB game results to paper positions.

Usage:
    python reconcile_mlb.py --gamePk 823215
    python reconcile_mlb.py --all-unsettled
"""
import argparse
import json
import logging
import sys

from config import load_config
from db.schema import init_db
from mlb.reconciler import reconcile_game_final, reconcile_all_unsettled_games

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile MLB final results")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--gamePk", type=int, metavar="GAMEPK")
    group.add_argument("--all-unsettled", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    conn = init_db(cfg.db_path)
    try:
        if args.gamePk:
            result = reconcile_game_final(conn, args.gamePk)
            print(json.dumps(result, indent=2))
        else:
            results = reconcile_all_unsettled_games(conn)
            settled_total = sum(r.get("settled", 0) for r in results)
            log.info("Reconciled %d game(s), %d position(s) settled", len(results), settled_total)
            print(json.dumps(results, indent=2))
    except Exception as exc:
        log.error("Fatal: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

---

## Task 6 — FastAPI Router

### `api/routers/mlb.py`

```python
"""api/routers/mlb.py — MLB game data and reconciliation endpoints."""
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_db
from mlb.game_store import fetch_and_store_game, fetch_and_store_schedule
from mlb.reconciler import reconcile_game_final, reconcile_all_unsettled_games

router = APIRouter()


class GameOut(BaseModel):
    game_pk: int
    game_date: str
    away_team: str
    home_team: str
    away_abbr: str
    home_abbr: str
    status: str
    game_id: Optional[str] = None
    final_away_score: Optional[int] = None
    final_home_score: Optional[int] = None
    final_total: Optional[int] = None
    is_final: bool
    last_checked_at: str

    model_config = {"from_attributes": True}


class PlayEventOut(BaseModel):
    id: int
    game_pk: int
    at_bat_index: int
    play_index: int
    event_time: Optional[str] = None
    inning: Optional[int] = None
    inning_half: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    is_scoring_play: bool
    is_home_run: bool
    rbi: int
    outs: int
    away_score: Optional[int] = None
    home_score: Optional[int] = None
    batter_name: Optional[str] = None
    pitcher_name: Optional[str] = None

    model_config = {"from_attributes": True}


class ReconcileResult(BaseModel):
    game_pk: int
    is_final: bool
    final_total: Optional[int] = None
    settled: int


def _game_out(row) -> GameOut:
    d = dict(row)
    d["is_final"] = bool(d.get("is_final", 0))
    return GameOut.model_validate(d)


def _play_out(row) -> PlayEventOut:
    d = dict(row)
    d["is_scoring_play"] = bool(d.get("is_scoring_play", 0))
    d["is_home_run"] = bool(d.get("is_home_run", 0))
    return PlayEventOut.model_validate(d)


@router.get("/mlb/games", response_model=list[GameOut])
def list_mlb_games(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD filter"),
    is_final: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    where, params = [], []
    if date:
        where.append("game_date=?")
        params.append(date)
    if is_final is not None:
        where.append("is_final=?")
        params.append(1 if is_final else 0)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT * FROM mlb_games{clause} ORDER BY game_date DESC, game_pk DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return [_game_out(r) for r in rows]


@router.get("/mlb/games/{game_pk}", response_model=GameOut)
def get_mlb_game(game_pk: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM mlb_games WHERE game_pk=?", (game_pk,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Game not found")
    return _game_out(row)


@router.get("/mlb/games/{game_pk}/plays", response_model=list[PlayEventOut])
def get_game_plays(
    game_pk: int,
    scoring_only: bool = Query(default=False),
    db: sqlite3.Connection = Depends(get_db),
):
    extra = " AND is_scoring_play=1" if scoring_only else ""
    rows = db.execute(
        f"SELECT * FROM mlb_play_events WHERE game_pk=?{extra}"
        " ORDER BY inning, at_bat_index, play_index",
        (game_pk,),
    ).fetchall()
    return [_play_out(r) for r in rows]


@router.post("/mlb/reconcile", response_model=list[ReconcileResult])
def reconcile_all(db: sqlite3.Connection = Depends(get_db)):
    results = reconcile_all_unsettled_games(db)
    return [ReconcileResult(**r) for r in results]


@router.post("/mlb/reconcile/{game_pk}", response_model=ReconcileResult)
def reconcile_game(game_pk: int, db: sqlite3.Connection = Depends(get_db)):
    result = reconcile_game_final(db, game_pk)
    return ReconcileResult(**result)
```

### `api/main.py` — add mlb router

Add to the import line and `include_router` call:

```python
# Import line change:
from api.routers import candidates, health, ingest, kalshi_markets, mlb, positions, signals, summary

# Add after existing include_router calls:
app.include_router(mlb.router, prefix=PREFIX, tags=["mlb"])
```

---

## Task 7 — React Page + Wiring

### `frontend/src/types/api.ts` — add at bottom

```typescript
export interface MLBGame {
  game_pk: number
  game_date: string
  away_team: string
  home_team: string
  away_abbr: string
  home_abbr: string
  status: string
  game_id: string | null
  final_away_score: number | null
  final_home_score: number | null
  final_total: number | null
  is_final: boolean
  last_checked_at: string
}

export interface MLBPlayEvent {
  id: number
  game_pk: number
  at_bat_index: number
  play_index: number
  event_time: string | null
  inning: number | null
  inning_half: string | null
  description: string | null
  event_type: string | null
  is_scoring_play: boolean
  is_home_run: boolean
  rbi: number
  outs: number
  away_score: number | null
  home_score: number | null
  batter_name: string | null
  pitcher_name: string | null
}

export interface ReconcileResult {
  game_pk: number
  is_final: boolean
  final_total: number | null
  settled: number
}
```

### `frontend/src/api/client.ts` — add to `api` object

```typescript
// Add imports at top:
import type { MLBGame, MLBPlayEvent, ReconcileResult } from '../types/api'

// Add to api object:
mlbGames: (params?: { date?: string; is_final?: boolean; limit?: number }) =>
  apiFetch<MLBGame[]>('/api/mlb/games', params as Params),

mlbGamePlays: (game_pk: number, scoring_only?: boolean) =>
  apiFetch<MLBPlayEvent[]>(`/api/mlb/games/${game_pk}/plays`, { scoring_only }),

mlbReconcile: (game_pk?: number) =>
  game_pk
    ? apiPost<ReconcileResult>(`/api/mlb/reconcile/${game_pk}`, {})
    : apiPost<ReconcileResult[]>('/api/mlb/reconcile', {}),
```

### `frontend/src/pages/MLBGames.tsx`

```tsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { MLBGame } from '../types/api'
import { Badge } from '../components/Badge'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatDateTime } from '../lib/format'

function statusVariant(status: string): 'green' | 'yellow' | 'slate' {
  if (status === 'Final') return 'green'
  if (status === 'In Progress' || status === 'Live') return 'yellow'
  return 'slate'
}

export function MLBGames() {
  const [dateFilter, setDateFilter] = useState('')
  const queryClient = useQueryClient()

  const { data: games, isLoading, isError } = useQuery({
    queryKey: ['mlb-games', dateFilter],
    queryFn: () => api.mlbGames({ date: dateFilter || undefined, limit: 100 }),
  })

  const reconcileMutation = useMutation({
    mutationFn: (game_pk: number) => api.mlbReconcile(game_pk),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mlb-games'] }),
  })

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">MLB Games</h1>
          <p className="text-sm text-slate-500 mt-0.5">Official schedule and final results</p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="date"
            value={dateFilter}
            onChange={e => setDateFilter(e.target.value)}
            className="px-3 py-1.5 rounded-md text-sm bg-[#0f1829] border border-[#1a2540] text-slate-300 focus:outline-none focus:border-blue-600"
          />
          {dateFilter && (
            <button
              onClick={() => setDateFilter('')}
              className="text-xs text-slate-500 hover:text-slate-300"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {isLoading && <LoadingState />}
      {isError && <ErrorState message="Failed to load MLB games" />}

      {games && games.length === 0 && (
        <EmptyState message="No games found. Run: python mlb_fetch.py --date YYYY-MM-DD" />
      )}

      {games && games.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-[#1a2540]">
          <table className="w-full text-sm">
            <thead className="bg-[#0a0f1e] text-slate-500 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-4 py-2 text-left">gamePk</th>
                <th className="px-4 py-2 text-left">Matchup</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-right">Score</th>
                <th className="px-4 py-2 text-right">Total</th>
                <th className="px-4 py-2 text-left">Last Checked</th>
                <th className="px-4 py-2 text-left"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1a2540]">
              {games.map((g: MLBGame) => (
                <tr key={g.game_pk} className="hover:bg-[#0f1829] transition-colors">
                  <td className="px-4 py-2 font-mono text-slate-400">{g.game_pk}</td>
                  <td className="px-4 py-2 text-slate-200 font-medium">
                    {g.away_abbr} @ {g.home_abbr}
                  </td>
                  <td className="px-4 py-2">
                    <Badge variant={statusVariant(g.status)}>{g.status}</Badge>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-slate-300">
                    {g.final_away_score !== null && g.final_home_score !== null
                      ? `${g.final_away_score}–${g.final_home_score}`
                      : '—'}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {g.final_total !== null
                      ? <span className="text-blue-400 font-semibold">{g.final_total}</span>
                      : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-4 py-2 text-slate-500 text-xs">
                    {formatDateTime(g.last_checked_at)}
                  </td>
                  <td className="px-4 py-2">
                    {!g.is_final && (
                      <button
                        onClick={() => reconcileMutation.mutate(g.game_pk)}
                        disabled={reconcileMutation.isPending}
                        className="px-2 py-1 text-xs rounded bg-blue-600/20 border border-blue-700/40 text-blue-400 hover:bg-blue-600/30 disabled:opacity-50"
                      >
                        Reconcile
                      </button>
                    )}
                    {g.is_final && (
                      <span className="text-xs text-green-600">Settled</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reconcileMutation.data && (
        <div className="text-xs text-slate-400 bg-[#0f1829] border border-[#1a2540] rounded p-3">
          Last reconcile: game_pk={reconcileMutation.data.game_pk ?? '(all)'}
          {' '}settled={Array.isArray(reconcileMutation.data)
            ? (reconcileMutation.data as { settled: number }[]).reduce((s, r) => s + r.settled, 0)
            : (reconcileMutation.data as { settled: number }).settled}
        </div>
      )}
    </div>
  )
}
```

### `frontend/src/App.tsx` — add route

```tsx
// Add import:
import { MLBGames } from './pages/MLBGames'

// Add route inside Route element={<Layout />}:
<Route path="/mlb-games" element={<MLBGames />} />
```

### `frontend/src/components/Layout.tsx` — add nav item

Add to `NAV` array (after the `/kalshi` entry):

```tsx
{ path: '/mlb-games', label: 'MLB Games', Icon: ChartBarIcon },
```

(Reuse `ChartBarIcon` which already exists in the file.)

---

## Quality Checks

- [ ] Every test file in `tests/` — no placeholder tests
- [ ] `mlb/stats_api.py` — all 9 public functions present, all return `Optional[...]`
- [ ] `mlb/game_store.py` — `fetch_and_store_game` uses INSERT OR IGNORE for play events (idempotent)
- [ ] `mlb/reconciler.py` — direction inferred per-position (`over_yes`/`under_yes`/`unknown`); push and unknown → `needs_review`, position left open; no auto-loss
- [ ] `paper_positions` has `settlement_status TEXT` column (migration in `_apply_migrations` + added to DDL)
- [ ] `ReconcileResult` (API + CLI) includes `needs_review` count
- [ ] `db/schema.py` — `mlb_play_events` has `UNIQUE(game_pk, at_bat_index, play_index)`
- [ ] `api/main.py` — mlb router included
- [ ] No real trades, no orders, no Kalshi write calls anywhere in this plan
- [ ] Existing 406 tests continue passing

---

## Execution Modes

**Subagent-Driven** (recommended — 7 tasks, significant surface area):
Spawn one agent per task group: (Task 1+2), (Task 3+4), (Task 5+6+7). Each agent gets the relevant task section and the codebase context above.

**Inline Execution** (faster if context is stable):
Execute tasks 1–7 in order. Run `pytest --ignore=test_results.txt -q` after each commit to gate on green.

Suggested commit sequence:
```
feat(db): add mlb_games, mlb_game_states, mlb_play_events tables
feat(mlb): stats_api HTTP client + jsonl_logger (10 tests)
feat(mlb): game_store fetch+log+DB orchestrator (12 tests)
feat(mlb): reconciler — settle paper positions after final (10 tests)
feat(cli): mlb_fetch.py + reconcile_mlb.py
feat(api): /api/mlb/games + /api/mlb/reconcile router
feat(ui): MLBGames page + nav entry
```
