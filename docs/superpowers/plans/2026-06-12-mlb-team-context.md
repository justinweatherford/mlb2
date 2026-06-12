# Plan: MLB Team Context / Baseball Intelligence Layer
**Date:** 2026-06-12

## Goal
Compute explainable 0-100 team ratings (offense, defense, F5, bullpen risk, late-game) from stored MLB game data and expose them via a REST API endpoint and a React page.

## Architecture
| File | Responsibility |
|---|---|
| `db/schema.py` | Add `mlb_team_context` table to DDL string |
| `mlb/team_context.py` | Compute metrics from `mlb_games` + `mlb_play_events`; upsert to DB |
| `api/schemas.py` | New `TeamContextOut` Pydantic model |
| `api/routers/mlb.py` | New router: GET list, GET single, POST refresh |
| `api/main.py` | Register new router |
| `frontend/src/types/api.ts` | New `TeamContext` TS interface |
| `frontend/src/api/client.ts` | `mlbTeamContext` + `mlbTeamContextRefresh` methods |
| `frontend/src/pages/MLBTeamContext.tsx` | New React page |
| `frontend/src/App.tsx` | `/mlb-context` route |
| `frontend/src/components/Layout.tsx` | Nav entry |
| `tests/test_mlb_team_context.py` | 14 tests |

## Tech Stack
- SQLite WAL mode, `sqlite3.Row`, `ON CONFLICT ... DO UPDATE`
- FastAPI `APIRouter`, `Depends(get_db)`, `ListResponse[T]` envelope
- React + TanStack Query (`useQuery`, `useMutation`)

## Rating Reference
```
LEAGUE_AVG_RPG  = 4.5   # runs/game, full game, per team
LEAGUE_AVG_F5   = 2.2   # runs in innings 1-5, per team
LEAGUE_AVG_LATE = 2.3   # runs in innings 6+, per team
SCALE_RPG       = 10.0  # rating points per 1 RPG delta from avg
SCALE_F5        = 12.0  # more sensitive for inning-level splits
```

---

## Task 1 — DB Schema: `mlb_team_context` table

**Files modified:** `db/schema.py`

### Failing test (write first)
```python
# tests/test_mlb_team_context.py  (create this file)
import pytest
from db.schema import init_db

@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()

def test_mlb_team_context_table_exists(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mlb_team_context'"
    ).fetchone()
    assert row is not None
```

Run: `python -m pytest tests/test_mlb_team_context.py::test_mlb_team_context_table_exists -x` → fails.

### Implementation
Append to the DDL string in `db/schema.py`, before the closing `"""`:

```python
# ── MLB team context ratings ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_team_context (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_abbr                       TEXT NOT NULL,
    team_name                       TEXT,
    season                          TEXT NOT NULL DEFAULT '2026',
    games_played                    INTEGER NOT NULL DEFAULT 0,

    -- Season-to-date raw metrics
    runs_per_game                   REAL,
    runs_allowed_per_game           REAL,
    home_runs_per_game              REAL,
    away_runs_per_game              REAL,

    -- Last-7 game metrics
    recent_runs_per_game_7          REAL,
    recent_runs_allowed_per_game_7  REAL,

    -- F5 (innings 1-5) metrics — NULL when no play-by-play data is stored
    f5_runs_per_game                REAL,
    f5_runs_allowed_per_game        REAL,

    -- Late game (innings 6+) metrics
    late_runs_per_game              REAL,
    late_runs_allowed_per_game      REAL,

    -- Derived ratings (0-100, ~50 = league average)
    offense_rating                  REAL,
    defense_pitching_rating         REAL,
    f5_offense_rating               REAL,
    f5_pitching_risk_rating         REAL,
    bullpen_risk_rating             REAL,
    late_game_risk_rating           REAL,
    comeback_scoring_rating         REAL,
    overall_context_score           REAL,

    -- Metadata
    sample_size                     INTEGER NOT NULL DEFAULT 0,
    f5_sample_size                  INTEGER NOT NULL DEFAULT 0,
    last_updated                    TEXT NOT NULL,

    UNIQUE(team_abbr, season)
);
CREATE INDEX IF NOT EXISTS idx_mlb_team_context_season ON mlb_team_context(season);
```

Run test: `python -m pytest tests/test_mlb_team_context.py::test_mlb_team_context_table_exists -x` → passes.

### Commit
```
git add db/schema.py tests/test_mlb_team_context.py
git commit -m "feat: add mlb_team_context table to schema (Task 5a)"
```

---

## Task 2 — `mlb/team_context.py`: metric computation + upsert

**Files created:** `mlb/team_context.py`

### Failing tests (add to `tests/test_mlb_team_context.py`)

```python
# --- add these imports at the top of test file ---
from mlb.team_context import compute_team_context, refresh_team_context, get_all_team_contexts, get_team_context


def _insert_game(conn, game_pk, away_abbr, home_abbr, away_score, home_score,
                 season="2026", date_suffix="04-01"):
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,1,?,?,?,datetime('now'),datetime('now'))
        """,
        (game_pk, f"{season}-{date_suffix}",
         f"{away_abbr} Team", f"{home_abbr} Team",
         away_abbr, home_abbr, f"{away_abbr}@{home_abbr}",
         "Final", away_score, home_score, away_score + home_score),
    )
    conn.commit()


def _insert_plays_f5(conn, game_pk, away_f5, home_f5):
    """Insert the last at-bat of inning 5 so F5 scores can be computed."""
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, inning, inning_half, away_score, home_score)
        VALUES (?,1,0,5,'bottom',?,?)
        """,
        (game_pk, away_f5, home_f5),
    )
    conn.commit()


def test_compute_season_stats(conn):
    _insert_game(conn, 1, "NYY", "BOS", 5, 3)
    _insert_game(conn, 2, "NYY", "TB",  4, 2)
    _insert_game(conn, 3, "HOU", "NYY", 3, 6)  # NYY home: scored 6, allowed 3

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert ctx["games_played"] == 3
    assert abs(ctx["runs_per_game"] - 5.0) < 0.01      # (5+4+6)/3
    assert abs(ctx["runs_allowed_per_game"] - 2.667) < 0.01  # (3+2+3)/3


def test_compute_no_games_returns_none(conn):
    assert compute_team_context("XYZ", "2026", conn) is None


def test_non_final_games_excluded(conn):
    conn.execute(
        """INSERT INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            game_id, status, is_final, last_checked_at, created_at)
           VALUES (99,'2026-04-01','NYY Team','BOS Team','NYY','BOS',
                   'NYY@BOS','In Progress',0,datetime('now'),datetime('now'))"""
    )
    conn.commit()
    assert compute_team_context("NYY", "2026", conn) is None


def test_last_7_games_recent_window(conn):
    # First 3 games: scored 2. Last 7 games: scored 7.
    for i, scored in enumerate([2, 2, 2, 7, 7, 7, 7, 7, 7, 7]):
        suffix = f"04-{i+1:02d}"
        _insert_game(conn, 100+i, "NYY", "OPP", scored, 3, date_suffix=suffix)

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert abs(ctx["recent_runs_per_game_7"] - 7.0) < 0.01
    assert abs(ctx["runs_per_game"] - 5.5) < 0.01  # (2*3 + 7*7)/10


def test_f5_runs_from_play_events(conn):
    _insert_game(conn, 10, "NYY", "BOS", 6, 4)
    _insert_plays_f5(conn, 10, away_f5=3, home_f5=2)

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx["f5_sample_size"] == 1
    assert abs(ctx["f5_runs_per_game"] - 3.0) < 0.01       # NYY is away: f5_away=3
    assert abs(ctx["f5_runs_allowed_per_game"] - 2.0) < 0.01  # BOS f5=2


def test_late_runs_equal_final_minus_f5(conn):
    _insert_game(conn, 20, "NYY", "BOS", 6, 4)
    _insert_plays_f5(conn, 20, away_f5=3, home_f5=2)

    ctx = compute_team_context("NYY", "2026", conn)
    assert abs(ctx["late_runs_per_game"] - 3.0) < 0.01       # 6-3
    assert abs(ctx["late_runs_allowed_per_game"] - 2.0) < 0.01  # 4-2


def test_f5_sample_size_zero_without_play_data(conn):
    _insert_game(conn, 30, "ATL", "PHI", 5, 3)
    ctx = compute_team_context("ATL", "2026", conn)
    assert ctx["f5_sample_size"] == 0
    assert ctx["f5_runs_per_game"] is None


def test_home_away_splits(conn):
    _insert_game(conn, 40, "NYM", "ATL", 6, 4)  # NYM away: scored 6
    _insert_game(conn, 41, "PHI", "NYM", 2, 5)  # NYM home: scored 5
    ctx = compute_team_context("NYM", "2026", conn)
    assert abs(ctx["away_runs_per_game"] - 6.0) < 0.01
    assert abs(ctx["home_runs_per_game"] - 5.0) < 0.01


def test_ratings_clamped_0_to_100(conn):
    for i in range(5):
        _insert_game(conn, 50+i, "MIN", f"T{i}", 0, 20)  # MIN scores 0, allows 20
    ctx = compute_team_context("MIN", "2026", conn)
    for key in ["offense_rating", "defense_pitching_rating", "f5_offense_rating",
                "bullpen_risk_rating", "comeback_scoring_rating", "overall_context_score"]:
        if ctx[key] is not None:
            assert 0.0 <= ctx[key] <= 100.0, f"{key}={ctx[key]} out of range"


def test_sample_size_equals_games_played(conn):
    for i in range(4):
        _insert_game(conn, 70+i, "CHC", f"T{i}", 4, 3)
    ctx = compute_team_context("CHC", "2026", conn)
    assert ctx["sample_size"] == 4
    assert ctx["games_played"] == 4


def test_refresh_writes_to_db(conn):
    _insert_game(conn, 80, "LAD", "SDP", 5, 3)
    _insert_game(conn, 81, "LAD", "SFG", 4, 2)
    result = refresh_team_context("2026", conn)
    assert "LAD" in result["teams"]
    row = conn.execute(
        "SELECT * FROM mlb_team_context WHERE team_abbr='LAD' AND season='2026'"
    ).fetchone()
    assert row is not None
    assert row["games_played"] == 2


def test_refresh_is_idempotent(conn):
    _insert_game(conn, 90, "HOU", "TEX", 7, 3)
    refresh_team_context("2026", conn)
    refresh_team_context("2026", conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_team_context WHERE team_abbr='HOU'"
    ).fetchone()[0]
    assert count == 1


def test_league_average_team_rates_near_50(conn):
    # 10 away games scoring 4, 10 home games scoring 5 → avg ≈ 4.5 RPG
    for i in range(10):
        _insert_game(conn, 200+i, "AVG", f"A{i}", 4, 5, date_suffix=f"04-{i+1:02d}")
        _insert_game(conn, 220+i, f"B{i}", "AVG", 5, 4, date_suffix=f"04-{i+1:02d}")
    ctx = compute_team_context("AVG", "2026", conn)
    assert ctx is not None
    assert 40 <= ctx["offense_rating"] <= 60
    assert 40 <= ctx["defense_pitching_rating"] <= 60
```

Run: `python -m pytest tests/test_mlb_team_context.py -x` → most fail with ImportError.

### Implementation: `mlb/team_context.py`

```python
"""
mlb/team_context.py — Season-to-date team ratings from stored MLB data.

Ratings are 0-100, calibrated so an average MLB team scores ~50.
Formulas are explicit and tweak-friendly; no ML involved.

Rating calibration (2026 MLB estimates):
  LEAGUE_AVG_RPG  = 4.5   runs/game (full game, per team)
  LEAGUE_AVG_F5   = 2.2   runs in innings 1-5 (per team)
  LEAGUE_AVG_LATE = 2.3   runs in innings 6+ (per team)
  SCALE_RPG       = 10.0  rating points per 1 RPG above/below avg
  SCALE_F5        = 12.0  more sensitive for inning-level splits
"""
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

from db.schema import init_db

log = logging.getLogger(__name__)

_LEAGUE_AVG_RPG  = 4.5
_LEAGUE_AVG_F5   = 2.2
_LEAGUE_AVG_LATE = 2.3
_SCALE_RPG       = 10.0
_SCALE_F5        = 12.0


def _now() -> str:
    return datetime.now().isoformat()


def _open_conn() -> sqlite3.Connection:
    return init_db(os.environ.get("DB_PATH", "kalshi_mlb.db"))


def _avg(lst: list) -> Optional[float]:
    return round(sum(lst) / len(lst), 3) if lst else None


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _get_f5_scores(game_pk: int, conn: sqlite3.Connection) -> Optional[tuple[int, int]]:
    """
    Returns (away_f5_runs, home_f5_runs) from the last at-bat in inning ≤ 5,
    or None if no play-by-play data is stored for this game.
    Cumulative away_score / home_score at that at-bat = F5 totals.
    """
    row = conn.execute(
        """
        SELECT away_score, home_score FROM mlb_play_events
        WHERE game_pk = ? AND inning <= 5
        ORDER BY at_bat_index DESC LIMIT 1
        """,
        (game_pk,),
    ).fetchone()
    if row is None:
        return None
    return (row["away_score"] or 0, row["home_score"] or 0)


# ── Rating formulas ───────────────────────────────────────────────────────────

def _rate_offense(rpg: Optional[float], recent_7: Optional[float]) -> float:
    """Blend season RPG (40%) with last-7 RPG (60%) to capture current form."""
    if rpg is None:
        return 50.0
    eff = (0.6 * recent_7 + 0.4 * rpg) if recent_7 is not None else rpg
    return round(_clamp(50.0 + (eff - _LEAGUE_AVG_RPG) * _SCALE_RPG), 1)


def _rate_defense(ra_pg: Optional[float], recent_ra_7: Optional[float]) -> float:
    """Lower runs allowed → higher defense/pitching rating."""
    if ra_pg is None:
        return 50.0
    eff = (0.6 * recent_ra_7 + 0.4 * ra_pg) if recent_ra_7 is not None else ra_pg
    return round(_clamp(50.0 + (_LEAGUE_AVG_RPG - eff) * _SCALE_RPG), 1)


def _rate_f5_offense(f5_rpg: Optional[float]) -> float:
    if f5_rpg is None:
        return 50.0
    return round(_clamp(50.0 + (f5_rpg - _LEAGUE_AVG_F5) * _SCALE_F5), 1)


def _rate_f5_pitching_risk(f5_ra_pg: Optional[float]) -> float:
    """Higher F5 runs allowed → higher F5 pitching risk score."""
    if f5_ra_pg is None:
        return 50.0
    return round(_clamp(50.0 + (f5_ra_pg - _LEAGUE_AVG_F5) * _SCALE_F5), 1)


def _rate_bullpen_risk(late_ra_pg: Optional[float]) -> float:
    """Higher late-inning runs allowed → higher bullpen risk score."""
    if late_ra_pg is None:
        return 50.0
    return round(_clamp(50.0 + (late_ra_pg - _LEAGUE_AVG_LATE) * _SCALE_F5), 1)


def _rate_comeback_scoring(late_rpg: Optional[float], rpg: Optional[float]) -> float:
    """
    Teams that score late AND score overall are dangerous when trailing.
    Composite of late-inning scoring (60%) and overall offense (40%).
    """
    if late_rpg is None or rpg is None:
        return 50.0
    composite = 0.6 * late_rpg + 0.4 * rpg
    avg_composite = 0.6 * _LEAGUE_AVG_LATE + 0.4 * _LEAGUE_AVG_RPG
    return round(_clamp(50.0 + (composite - avg_composite) * _SCALE_F5), 1)


def _overall_score(offense: float, defense: float, f5_offense: float) -> float:
    return round(0.4 * offense + 0.4 * defense + 0.2 * f5_offense, 1)


# ── Core computation ──────────────────────────────────────────────────────────

def compute_team_context(
    team_abbr: str,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[dict]:
    """
    Compute all season metrics and ratings for team_abbr.
    Returns None if no final games are found.
    """
    like = f"{season}%"

    away_rows = conn.execute(
        """
        SELECT game_pk, game_date,
               final_away_score AS scored, final_home_score AS allowed
        FROM mlb_games
        WHERE away_abbr = ? AND is_final = 1 AND game_date LIKE ?
        ORDER BY game_date ASC
        """,
        (team_abbr, like),
    ).fetchall()

    home_rows = conn.execute(
        """
        SELECT game_pk, game_date,
               final_home_score AS scored, final_away_score AS allowed
        FROM mlb_games
        WHERE home_abbr = ? AND is_final = 1 AND game_date LIKE ?
        ORDER BY game_date ASC
        """,
        (team_abbr, like),
    ).fetchall()

    name_row = conn.execute(
        """
        SELECT CASE WHEN away_abbr = ? THEN away_team ELSE home_team END AS team_name
        FROM mlb_games
        WHERE (away_abbr = ? OR home_abbr = ?) AND is_final = 1 LIMIT 1
        """,
        (team_abbr, team_abbr, team_abbr),
    ).fetchone()
    team_name = name_row["team_name"] if name_row else team_abbr

    all_games: list[tuple[dict, str]] = sorted(
        [(dict(r), "away") for r in away_rows] +
        [(dict(r), "home") for r in home_rows],
        key=lambda x: x[0]["game_date"],
    )

    if not all_games:
        return None

    # ── Season stats ──────────────────────────────────────────────────────────
    scored_list  = [g["scored"]  for g, _ in all_games]
    allowed_list = [g["allowed"] for g, _ in all_games]
    home_scored  = [g["scored"]  for g, side in all_games if side == "home"]
    away_scored  = [g["scored"]  for g, side in all_games if side == "away"]

    rpg      = _avg(scored_list)
    ra_pg    = _avg(allowed_list)
    home_rpg = _avg(home_scored)
    away_rpg = _avg(away_scored)

    # ── Last-7 stats ──────────────────────────────────────────────────────────
    last_7       = all_games[-7:]
    recent_rpg_7 = _avg([g["scored"]  for g, _ in last_7])
    recent_ra_7  = _avg([g["allowed"] for g, _ in last_7])

    # ── F5 and late stats (requires play-by-play in mlb_play_events) ──────────
    f5_scored_list    = []
    f5_allowed_list   = []
    late_scored_list  = []
    late_allowed_list = []

    for game, side in all_games:
        f5 = _get_f5_scores(game["game_pk"], conn)
        if f5 is None:
            continue

        f5_away, f5_home = f5
        if side == "away":
            f5_team = f5_away
            f5_opp  = f5_home
            late_t  = (game["scored"]  or 0) - f5_away
            late_o  = (game["allowed"] or 0) - f5_home
        else:
            f5_team = f5_home
            f5_opp  = f5_away
            late_t  = (game["scored"]  or 0) - f5_home
            late_o  = (game["allowed"] or 0) - f5_away

        # Skip rows with negative late runs (bad or partial play data)
        if late_t < 0 or late_o < 0:
            continue

        f5_scored_list.append(f5_team)
        f5_allowed_list.append(f5_opp)
        late_scored_list.append(late_t)
        late_allowed_list.append(late_o)

    f5_rpg    = _avg(f5_scored_list)
    f5_ra_pg  = _avg(f5_allowed_list)
    late_rpg  = _avg(late_scored_list)
    late_ra_pg = _avg(late_allowed_list)

    # ── Ratings ───────────────────────────────────────────────────────────────
    offense_r  = _rate_offense(rpg, recent_rpg_7)
    defense_r  = _rate_defense(ra_pg, recent_ra_7)
    f5_off_r   = _rate_f5_offense(f5_rpg)
    f5_pit_r   = _rate_f5_pitching_risk(f5_ra_pg)
    bp_risk_r  = _rate_bullpen_risk(late_ra_pg)
    comeback_r = _rate_comeback_scoring(late_rpg, rpg)
    overall_r  = _overall_score(offense_r, defense_r, f5_off_r)

    return {
        "team_abbr":                      team_abbr,
        "team_name":                      team_name,
        "season":                         season,
        "games_played":                   len(all_games),
        "runs_per_game":                  rpg,
        "runs_allowed_per_game":          ra_pg,
        "home_runs_per_game":             home_rpg,
        "away_runs_per_game":             away_rpg,
        "recent_runs_per_game_7":         recent_rpg_7,
        "recent_runs_allowed_per_game_7": recent_ra_7,
        "f5_runs_per_game":               f5_rpg,
        "f5_runs_allowed_per_game":       f5_ra_pg,
        "late_runs_per_game":             late_rpg,
        "late_runs_allowed_per_game":     late_ra_pg,
        "offense_rating":                 offense_r,
        "defense_pitching_rating":        defense_r,
        "f5_offense_rating":              f5_off_r,
        "f5_pitching_risk_rating":        f5_pit_r,
        "bullpen_risk_rating":            bp_risk_r,
        "late_game_risk_rating":          bp_risk_r,   # same concept
        "comeback_scoring_rating":        comeback_r,
        "overall_context_score":          overall_r,
        "sample_size":                    len(all_games),
        "f5_sample_size":                 len(f5_scored_list),
        "last_updated":                   _now(),
    }


def _upsert_team_context(conn: sqlite3.Connection, ctx: dict) -> None:
    conn.execute(
        """
        INSERT INTO mlb_team_context
          (team_abbr, team_name, season, games_played,
           runs_per_game, runs_allowed_per_game,
           home_runs_per_game, away_runs_per_game,
           recent_runs_per_game_7, recent_runs_allowed_per_game_7,
           f5_runs_per_game, f5_runs_allowed_per_game,
           late_runs_per_game, late_runs_allowed_per_game,
           offense_rating, defense_pitching_rating,
           f5_offense_rating, f5_pitching_risk_rating,
           bullpen_risk_rating, late_game_risk_rating,
           comeback_scoring_rating, overall_context_score,
           sample_size, f5_sample_size, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(team_abbr, season) DO UPDATE SET
          team_name                       = excluded.team_name,
          games_played                    = excluded.games_played,
          runs_per_game                   = excluded.runs_per_game,
          runs_allowed_per_game           = excluded.runs_allowed_per_game,
          home_runs_per_game              = excluded.home_runs_per_game,
          away_runs_per_game              = excluded.away_runs_per_game,
          recent_runs_per_game_7          = excluded.recent_runs_per_game_7,
          recent_runs_allowed_per_game_7  = excluded.recent_runs_allowed_per_game_7,
          f5_runs_per_game                = excluded.f5_runs_per_game,
          f5_runs_allowed_per_game        = excluded.f5_runs_allowed_per_game,
          late_runs_per_game              = excluded.late_runs_per_game,
          late_runs_allowed_per_game      = excluded.late_runs_allowed_per_game,
          offense_rating                  = excluded.offense_rating,
          defense_pitching_rating         = excluded.defense_pitching_rating,
          f5_offense_rating               = excluded.f5_offense_rating,
          f5_pitching_risk_rating         = excluded.f5_pitching_risk_rating,
          bullpen_risk_rating             = excluded.bullpen_risk_rating,
          late_game_risk_rating           = excluded.late_game_risk_rating,
          comeback_scoring_rating         = excluded.comeback_scoring_rating,
          overall_context_score           = excluded.overall_context_score,
          sample_size                     = excluded.sample_size,
          f5_sample_size                  = excluded.f5_sample_size,
          last_updated                    = excluded.last_updated
        """,
        (
            ctx["team_abbr"], ctx["team_name"], ctx["season"],
            ctx["games_played"],
            ctx["runs_per_game"], ctx["runs_allowed_per_game"],
            ctx["home_runs_per_game"], ctx["away_runs_per_game"],
            ctx["recent_runs_per_game_7"], ctx["recent_runs_allowed_per_game_7"],
            ctx["f5_runs_per_game"], ctx["f5_runs_allowed_per_game"],
            ctx["late_runs_per_game"], ctx["late_runs_allowed_per_game"],
            ctx["offense_rating"], ctx["defense_pitching_rating"],
            ctx["f5_offense_rating"], ctx["f5_pitching_risk_rating"],
            ctx["bullpen_risk_rating"], ctx["late_game_risk_rating"],
            ctx["comeback_scoring_rating"], ctx["overall_context_score"],
            ctx["sample_size"], ctx["f5_sample_size"], ctx["last_updated"],
        ),
    )


def refresh_team_context(
    season: str = "2026",
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Compute and upsert context for every team with final games in the season.
    Returns {refreshed, team_count, teams, errors}.
    """
    _own = conn is None
    if _own:
        conn = _open_conn()

    summary: dict = {"refreshed": True, "team_count": 0, "teams": [], "errors": []}

    try:
        rows = conn.execute(
            """
            SELECT DISTINCT abbr FROM (
                SELECT away_abbr AS abbr FROM mlb_games
                WHERE is_final = 1 AND game_date LIKE ?
                UNION
                SELECT home_abbr AS abbr FROM mlb_games
                WHERE is_final = 1 AND game_date LIKE ?
            ) ORDER BY abbr
            """,
            (f"{season}%", f"{season}%"),
        ).fetchall()

        for row in rows:
            abbr = row[0]
            try:
                ctx = compute_team_context(abbr, season, conn)
                if ctx is not None:
                    _upsert_team_context(conn, ctx)
                    summary["teams"].append(abbr)
                    summary["team_count"] += 1
            except Exception as exc:
                log.error("team_context error team=%s: %s", abbr, exc)
                summary["errors"].append(f"{abbr}: {exc}")

        conn.commit()

    except Exception as exc:
        log.error("refresh_team_context error: %s", exc)
        summary["refreshed"] = False
        summary["errors"].append(str(exc))
    finally:
        if _own:
            conn.close()

    return summary


def get_all_team_contexts(season: str, conn: sqlite3.Connection) -> list[dict]:
    """Fetch all mlb_team_context rows for a season, sorted by overall score DESC."""
    rows = conn.execute(
        """
        SELECT * FROM mlb_team_context WHERE season = ?
        ORDER BY overall_context_score DESC
        """,
        (season,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_team_context(
    team_abbr: str,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM mlb_team_context WHERE team_abbr = ? AND season = ?",
        (team_abbr, season),
    ).fetchone()
    return dict(row) if row else None
```

Run full test suite: `python -m pytest tests/test_mlb_team_context.py -x` → all pass.
Run existing tests: `python -m pytest tests/ --ignore=test_results.txt -x` → no regressions.

### Commit
```
git add mlb/team_context.py tests/test_mlb_team_context.py db/schema.py
git commit -m "feat: mlb/team_context.py — metric computation + upsert (Task 5b)"
```

---

## Task 3 — API: `TeamContextOut` schema + `api/routers/mlb.py` + main.py wiring

**Files modified:** `api/schemas.py`, `api/main.py`
**Files created:** `api/routers/mlb.py`

### `api/schemas.py` — append `TeamContextOut`

Add at the end of the file (after the `IngestRequest`/`DryRunRequest` section):

```python
# ---------------------------------------------------------------------------
# MLB Team Context
# ---------------------------------------------------------------------------

class TeamContextOut(BaseModel):
    id: int
    team_abbr: str
    team_name: Optional[str] = None
    season: str
    games_played: int
    runs_per_game: Optional[float] = None
    runs_allowed_per_game: Optional[float] = None
    home_runs_per_game: Optional[float] = None
    away_runs_per_game: Optional[float] = None
    recent_runs_per_game_7: Optional[float] = None
    recent_runs_allowed_per_game_7: Optional[float] = None
    f5_runs_per_game: Optional[float] = None
    f5_runs_allowed_per_game: Optional[float] = None
    late_runs_per_game: Optional[float] = None
    late_runs_allowed_per_game: Optional[float] = None
    offense_rating: Optional[float] = None
    defense_pitching_rating: Optional[float] = None
    f5_offense_rating: Optional[float] = None
    f5_pitching_risk_rating: Optional[float] = None
    bullpen_risk_rating: Optional[float] = None
    late_game_risk_rating: Optional[float] = None
    comeback_scoring_rating: Optional[float] = None
    overall_context_score: Optional[float] = None
    sample_size: int
    f5_sample_size: int
    last_updated: str
```

### `api/routers/mlb.py` (create new file)

```python
"""api/routers/mlb.py — MLB team context endpoints."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import ListResponse, TeamContextOut
from mlb.team_context import (
    get_all_team_contexts,
    get_team_context,
    refresh_team_context,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=ListResponse[TeamContextOut])
def list_team_contexts(season: str = "2026", conn=Depends(get_db)):
    rows = get_all_team_contexts(season, conn)
    items = [TeamContextOut.model_validate(r) for r in rows]
    return ListResponse(total=len(items), items=items)


@router.get("/{team_abbr}", response_model=TeamContextOut)
def get_team_context_endpoint(
    team_abbr: str,
    season: str = "2026",
    conn=Depends(get_db),
):
    row = get_team_context(team_abbr.upper(), season, conn)
    if row is None:
        raise HTTPException(404, f"No context for {team_abbr} in {season}")
    return TeamContextOut.model_validate(row)


@router.post("/refresh")
def refresh_context(season: str = "2026", conn=Depends(get_db)):
    return refresh_team_context(season, conn)
```

### `api/main.py` — add import + router registration

Add `mlb` to the import line:
```python
from api.routers import candidates, health, ingest, kalshi_markets, mlb, positions, signals, summary
```

Add router registration after the kalshi line:
```python
app.include_router(mlb.router, prefix="/api/mlb/team-context", tags=["mlb"])
```

### Smoke test
```
uvicorn api.main:app --reload --port 8000
curl http://localhost:8000/api/mlb/team-context?season=2026
# → {"total": 0, "items": []}
```

### Commit
```
git add api/schemas.py api/routers/mlb.py api/main.py
git commit -m "feat: GET /api/mlb/team-context endpoints (Task 5c)"
```

---

## Task 4 — Frontend: `MLBTeamContext.tsx` + wiring

**Files modified:** `frontend/src/types/api.ts`, `frontend/src/api/client.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`
**Files created:** `frontend/src/pages/MLBTeamContext.tsx`

### `frontend/src/types/api.ts` — append `TeamContext` interface

```typescript
export interface TeamContext {
  id: number
  team_abbr: string
  team_name: string | null
  season: string
  games_played: number
  runs_per_game: number | null
  runs_allowed_per_game: number | null
  home_runs_per_game: number | null
  away_runs_per_game: number | null
  recent_runs_per_game_7: number | null
  recent_runs_allowed_per_game_7: number | null
  f5_runs_per_game: number | null
  f5_runs_allowed_per_game: number | null
  late_runs_per_game: number | null
  late_runs_allowed_per_game: number | null
  offense_rating: number | null
  defense_pitching_rating: number | null
  f5_offense_rating: number | null
  f5_pitching_risk_rating: number | null
  bullpen_risk_rating: number | null
  late_game_risk_rating: number | null
  comeback_scoring_rating: number | null
  overall_context_score: number | null
  sample_size: number
  f5_sample_size: number
  last_updated: string
}
```

### `frontend/src/api/client.ts` — add import + two methods

Add `TeamContext` to the import line at top:
```typescript
import type {
  // ... existing types ...
  TeamContext,
} from '../types/api'
```

Add to the `api` object:
```typescript
  mlbTeamContext: (params?: { season?: string }) =>
    apiFetch<ListResponse<TeamContext>>('/api/mlb/team-context', params as Params),

  mlbTeamContextRefresh: (season = '2026') =>
    apiPost<{ refreshed: boolean; team_count: number; teams: string[]; errors: string[] }>(
      `/api/mlb/team-context/refresh?season=${season}`,
      {},
    ),
```

### `frontend/src/pages/MLBTeamContext.tsx` (create new file)

```tsx
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { TeamContext } from '../types/api'

function RatingCell({ value }: { value: number | null }) {
  if (value === null) return <span className="text-slate-600">—</span>
  const color =
    value >= 65 ? 'text-green-400' :
    value >= 50 ? 'text-blue-400'  :
    value >= 35 ? 'text-yellow-400' :
                  'text-red-400'
  return <span className={color}>{value.toFixed(0)}</span>
}

function Num({ value, decimals = 1 }: { value: number | null; decimals?: number }) {
  if (value === null) return <span className="text-slate-600">—</span>
  return <span className="text-slate-300">{value.toFixed(decimals)}</span>
}

const TH = ({ children, right }: { children: React.ReactNode; right?: boolean }) => (
  <th className={`pb-2 pr-3 text-[11px] font-medium text-slate-500 uppercase tracking-wider${right ? ' text-right' : ''}`}>
    {children}
  </th>
)

export function MLBTeamContext() {
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['mlb-team-context'],
    queryFn: () => api.mlbTeamContext(),
  })

  const refresh = useMutation({
    mutationFn: () => api.mlbTeamContextRefresh(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mlb-team-context'] }),
  })

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">MLB Team Context</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Season-to-date ratings · 0-100 · ~50 = league average
          </p>
        </div>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md disabled:opacity-50 transition-colors"
        >
          {refresh.isPending ? 'Refreshing…' : 'Refresh Ratings'}
        </button>
      </div>

      {refresh.isSuccess && refresh.data && (
        <div className="mb-4 text-xs text-green-400">
          Refreshed {refresh.data.team_count} teams.
          {refresh.data.errors.length > 0 && (
            <span className="ml-2 text-yellow-400">{refresh.data.errors.length} errors.</span>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-800">
                <TH>Team</TH>
                <TH right>GP</TH>
                <TH right>RPG</TH>
                <TH right>RA/G</TH>
                <TH right>Off</TH>
                <TH right>F5-Off</TH>
                <TH right>Def</TH>
                <TH right>BP Risk</TH>
                <TH right>Late Risk</TH>
                <TH right>Cmbk</TH>
                <TH right>Overall</TH>
                <TH right>F5n</TH>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((t: TeamContext) => (
                <tr
                  key={t.team_abbr}
                  className="border-b border-slate-800/50 hover:bg-slate-800/20 transition-colors"
                >
                  <td className="py-2 pr-3">
                    <span className="font-medium text-slate-100">{t.team_abbr}</span>
                    {t.team_name && (
                      <span className="ml-2 text-[11px] text-slate-600">{t.team_name}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right text-slate-400">{t.games_played}</td>
                  <td className="py-2 pr-3 text-right"><Num value={t.runs_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.runs_allowed_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.offense_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.f5_offense_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.defense_pitching_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.bullpen_risk_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.late_game_risk_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.comeback_scoring_rating} /></td>
                  <td className="py-2 pr-3 text-right">
                    <span className="font-medium"><RatingCell value={t.overall_context_score} /></span>
                  </td>
                  <td className="py-2 text-right text-[11px] text-slate-600">{t.f5_sample_size}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.total ?? 0) === 0 && (
            <p className="text-slate-500 mt-6 text-center text-sm">
              No team context data yet. Click <strong>Refresh Ratings</strong> to compute from stored games.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
```

### `frontend/src/App.tsx` — add route

Add import:
```typescript
import { MLBTeamContext } from './pages/MLBTeamContext'
```

Add route inside `<Route element={<Layout />}>`:
```typescript
<Route path="/mlb-context" element={<MLBTeamContext />} />
```

### `frontend/src/components/Layout.tsx` — add nav entry + icon

Add `TableCellsIcon` function before the `NAV` array:
```typescript
function TableCellsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h7.5c.621 0 1.125-.504 1.125-1.125m-9.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 3.75h-7.5A1.125 1.125 0 0 1 12 18.375m9.75-12.75c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125m19.5 0v1.5c0 .621-.504 1.125-1.125 1.125M2.25 5.625v1.5c0 .621.504 1.125 1.125 1.125m0 0h17.25m-17.25 0c-.621 0-1.125.504-1.125 1.125m18.375-1.125c.621 0 1.125.504 1.125 1.125m-1.125-1.125H3.375m15 0v7.5M3.375 8.25v7.5" />
    </svg>
  )
}
```

Add to the `NAV` array (after the `/kalshi` entry):
```typescript
{ path: '/mlb-context', label: 'MLB Context', Icon: TableCellsIcon },
```

### Smoke test
Start frontend dev server: `cd frontend && npm run dev`
Navigate to http://localhost:5173/mlb-context — table renders with empty state or data depending on DB.
Click "Refresh Ratings" button — calls POST /api/mlb/team-context/refresh, table updates.

### Commit
```
git add frontend/src/types/api.ts frontend/src/api/client.ts \
        frontend/src/pages/MLBTeamContext.tsx \
        frontend/src/App.tsx frontend/src/components/Layout.tsx
git commit -m "feat: MLBTeamContext page + wiring (Task 5d)"
```

---

## Task 5 — Run full test suite + final commit

```bash
python -m pytest tests/ --ignore=test_results.txt -v
```

Expected: all existing tests pass, plus the 14 new tests in `test_mlb_team_context.py`.

If any existing test regressed:
- Check that `db/schema.py` DDL change didn't break `init_db` for existing test fixtures.
- The new `CREATE TABLE IF NOT EXISTS` is idempotent and has no effect on existing tables.

### Final integration smoke test (with real data)
```bash
# 1. Fetch some games for a recent date
python -c "from mlb.game_store import fetch_and_store_schedule; print(fetch_and_store_schedule('2026-06-10'))"

# 2. Refresh team context
python -c "from mlb.team_context import refresh_team_context; import json; print(json.dumps(refresh_team_context(), indent=2, default=str))"

# 3. Query the DB
python -c "
from db.schema import init_db
conn = init_db('kalshi_mlb.db')
rows = conn.execute('SELECT team_abbr, games_played, offense_rating, overall_context_score FROM mlb_team_context ORDER BY overall_context_score DESC').fetchall()
for r in rows: print(dict(r))
"
```

### Commit
```
git commit -m "test: full mlb_team_context test suite passes (Task 5 complete)"
```

---

## Definition of Done

- [ ] `mlb_team_context` table exists in DB after `init_db`
- [ ] `refresh_team_context("2026")` runs without error given stored `mlb_games` rows
- [ ] Ratings stay in [0, 100] regardless of extreme scores
- [ ] `GET /api/mlb/team-context` returns `{ total, items }` envelope
- [ ] Frontend `/mlb-context` renders table with color-coded ratings
- [ ] Refresh button calls API and updates table
- [ ] All 14 new tests pass
- [ ] No regressions in existing test suite
