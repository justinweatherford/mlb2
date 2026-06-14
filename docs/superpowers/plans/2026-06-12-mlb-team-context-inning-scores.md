# Plan: MLB Team Context — Inning-Level Scoring Refinement
**Date:** 2026-06-12

## Goal
Replace the play-event-based F5 proxy with authoritative inning-by-inning run data so F5, late-game, bullpen risk, and comeback ratings are real numbers rather than 50 for every team.

## Architecture
| File | Change |
|---|---|
| `db/schema.py` | Add `mlb_inning_scores` table; add `context_confidence` migration for `mlb_team_context` |
| `mlb/game_store.py` | Add `_upsert_inning_scores`; call it from `fetch_and_store_game` after linescore fetch |
| `mlb/team_context.py` | Replace `_get_f5_scores` (play_events) with `_get_inning_totals` (inning_scores); add `context_confidence` |
| `api/schemas.py` | Add `context_confidence` to `TeamContextOut` |
| `frontend/src/types/api.ts` | Add `context_confidence` to `TeamContext` |
| `frontend/src/pages/MLBTeamContext.tsx` | Add raw F5/late columns + confidence badge |
| `tests/test_mlb_inning_scores.py` | New file — 10 tests for inning storage + game_store integration |
| `tests/test_mlb_team_context.py` | 5 new tests for inning-based context + confidence |
| `_seed_games.py` | Also fetch game details for recent final games to populate inning data |

## Tech Stack
- SQLite `ON CONFLICT(game_pk, inning) DO UPDATE` for idempotent inning upserts
- MLB Stats API `/api/v1/game/{pk}/linescore` — `innings` array, each entry has `num`, `away.runs`, `home.runs`
- FastAPI, Pydantic, React + TanStack Query (unchanged patterns)

## Data flow
```
fetch_and_store_game(game_pk)
  → stats_api.fetch_linescore(game_pk)
  → _upsert_inning_scores(conn, game_pk, linescore, away_abbr, home_abbr)
  → mlb_inning_scores rows: one per (game_pk, inning)

refresh_team_context(season)
  → compute_team_context(team_abbr, season, conn)
  → _get_inning_totals(game_pk, conn)   ← NEW; replaces _get_f5_scores
  → f5_scored_list, late_scored_list, etc.
```

## Linescore API shape (for reference)
```json
{
  "innings": [
    {"num": 1, "away": {"runs": 2, "hits": 3, "errors": 0},
                "home": {"runs": 0, "hits": 1, "errors": 0}},
    {"num": 2, ...},
    ...
  ]
}
```

---

## Task 1 — DB: `mlb_inning_scores` table + `context_confidence` migration

**Files modified:** `db/schema.py`

### Failing test (write first in `tests/test_mlb_inning_scores.py`)
```python
from db.schema import init_db

def test_mlb_inning_scores_table_exists():
    conn = init_db(":memory:")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mlb_inning_scores'"
    ).fetchone()
    assert row is not None
    conn.close()

def test_mlb_team_context_has_context_confidence_column():
    conn = init_db(":memory:")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mlb_team_context)").fetchall()]
    assert "context_confidence" in cols
    conn.close()
```

Run: `python -m pytest tests/test_mlb_inning_scores.py -x` → both fail.

### Implementation

**Append to `DDL` string** in `db/schema.py` (before closing `"""`):

```sql
-- ── MLB inning-level scoring ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_inning_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk     INTEGER NOT NULL,
    inning      INTEGER NOT NULL,
    away_abbr   TEXT    NOT NULL,
    home_abbr   TEXT    NOT NULL,
    away_runs   INTEGER NOT NULL DEFAULT 0,
    home_runs   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    UNIQUE(game_pk, inning)
);
CREATE INDEX IF NOT EXISTS idx_mlb_inning_scores_pk ON mlb_inning_scores(game_pk);
```

**Add migration** for the new `context_confidence` column in `_apply_migrations`:
```python
"ALTER TABLE mlb_team_context ADD COLUMN context_confidence TEXT NOT NULL DEFAULT 'low'",
```

Run tests → both pass.

### Commit
```
git add db/schema.py tests/test_mlb_inning_scores.py
git commit -m "feat: add mlb_inning_scores table + context_confidence migration (Task 6a)"
```

---

## Task 2 — `mlb/game_store.py`: parse linescore innings into `mlb_inning_scores`

**Files modified:** `mlb/game_store.py`

### Failing tests (add to `tests/test_mlb_inning_scores.py`)

Add rich linescore fixture and game_store tests:

```python
import pytest
from unittest.mock import patch
from mlb.game_store import fetch_and_store_game

# Shared fixture data
_FEED_FINAL = {
    "gameData": {
        "datetime": {"officialDate": "2026-06-12"},
        "status": {"abstractGameState": "Final"},
        "teams": {
            "away": {"abbreviation": "NYY", "name": "New York Yankees"},
            "home": {"abbreviation": "BOS", "name": "Boston Red Sox"},
        },
    },
    "liveData": {
        "linescore": {
            "teams": {"away": {"runs": 7}, "home": {"runs": 4}},
            "offense": {},
        },
        "plays": {"currentPlay": {"matchup": {"batter": {}, "pitcher": {}}}},
    },
}

_LINESCORE_FULL = {
    "innings": [
        {"num": 1, "away": {"runs": 2, "hits": 3, "errors": 0}, "home": {"runs": 0, "hits": 1, "errors": 0}},
        {"num": 2, "away": {"runs": 0, "hits": 1, "errors": 0}, "home": {"runs": 1, "hits": 2, "errors": 0}},
        {"num": 3, "away": {"runs": 1, "hits": 2, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 0}},
        {"num": 4, "away": {"runs": 0, "hits": 0, "errors": 0}, "home": {"runs": 2, "hits": 3, "errors": 0}},
        {"num": 5, "away": {"runs": 1, "hits": 1, "errors": 0}, "home": {"runs": 0, "hits": 1, "errors": 0}},
        {"num": 6, "away": {"runs": 0, "hits": 2, "errors": 0}, "home": {"runs": 3, "hits": 4, "errors": 0}},
        {"num": 7, "away": {"runs": 2, "hits": 3, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 0}},
        {"num": 8, "away": {"runs": 0, "hits": 0, "errors": 0}, "home": {"runs": 1, "hits": 2, "errors": 0}},
        {"num": 9, "away": {"runs": 1, "hits": 1, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 0}},
    ]
}
# Away F5: 2+0+1+0+1=4  Away late: 0+2+0+1=3  Away total: 7
# Home F5: 0+1+0+2+0=3  Home late: 3+0+1+0=4  Home total: 7

_BOXSCORE = {"teams": {"away": {}, "home": {}}}
_PBP      = {"allPlays": []}


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _run_fetch(conn, game_pk=1001, feed=None, linescore=None):
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=feed or _FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=linescore), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        return fetch_and_store_game(game_pk, conn=conn)


def test_inning_scores_inserted(conn):
    _run_fetch(conn, linescore=_LINESCORE_FULL)
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 9


def test_inning_scores_correct_values(conn):
    _run_fetch(conn, linescore=_LINESCORE_FULL)
    row = conn.execute(
        "SELECT away_runs, home_runs FROM mlb_inning_scores WHERE game_pk=1001 AND inning=1"
    ).fetchone()
    assert row["away_runs"] == 2
    assert row["home_runs"] == 0


def test_inning_scores_idempotent(conn):
    _run_fetch(conn, linescore=_LINESCORE_FULL)
    _run_fetch(conn, linescore=_LINESCORE_FULL)
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 9  # not 18


def test_missing_linescore_does_not_crash(conn):
    result = _run_fetch(conn, linescore=None)
    assert result["fetched"] is True
    assert "innings_inserted" not in result or result.get("innings_inserted", 0) == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 0


def test_empty_innings_array_does_not_crash(conn):
    _run_fetch(conn, linescore={"innings": []})
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 0


def test_inning_with_missing_runs_defaults_to_zero(conn):
    linescore = {"innings": [{"num": 1, "away": {}, "home": {}}]}
    _run_fetch(conn, linescore=linescore)
    row = conn.execute(
        "SELECT away_runs, home_runs FROM mlb_inning_scores WHERE game_pk=1001 AND inning=1"
    ).fetchone()
    assert row["away_runs"] == 0
    assert row["home_runs"] == 0


def test_summary_includes_innings_inserted(conn):
    result = _run_fetch(conn, linescore=_LINESCORE_FULL)
    assert result.get("innings_inserted") == 9
    assert result.get("innings_skipped") == 0


def test_away_abbr_stored_in_inning_scores(conn):
    _run_fetch(conn, linescore=_LINESCORE_FULL)
    row = conn.execute(
        "SELECT away_abbr, home_abbr FROM mlb_inning_scores WHERE game_pk=1001 LIMIT 1"
    ).fetchone()
    assert row["away_abbr"] == "NYY"
    assert row["home_abbr"] == "BOS"
```

Run: `python -m pytest tests/test_mlb_inning_scores.py -x` → fails on inning-related tests.

### Implementation: add `_upsert_inning_scores` to `mlb/game_store.py`

**Add new helper** after `_upsert_plays` (around line 152):

```python
def _upsert_inning_scores(
    conn: sqlite3.Connection,
    game_pk: int,
    linescore: dict,
    away_abbr: str,
    home_abbr: str,
) -> tuple[int, int]:
    """
    Parse the linescore innings array and upsert one row per inning.
    Returns (inserted, skipped).
    ON CONFLICT updates away_runs/home_runs so re-fetches stay correct.
    """
    inserted = skipped = 0
    for inn in linescore.get("innings") or []:
        try:
            num = inn.get("num")
            if num is None:
                skipped += 1
                continue
            away_runs = (inn.get("away") or {}).get("runs") or 0
            home_runs = (inn.get("home") or {}).get("runs") or 0
            cur = conn.execute(
                """
                INSERT INTO mlb_inning_scores
                  (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(game_pk, inning) DO UPDATE SET
                  away_runs = excluded.away_runs,
                  home_runs = excluded.home_runs
                """,
                (game_pk, num, away_abbr, home_abbr, away_runs, home_runs, _now()),
            )
            inserted += 1 if cur.rowcount > 0 else 0
            skipped  += 0 if cur.rowcount > 0 else 1
        except Exception as exc:
            log.warning("inning score error (game_pk=%d inning=%s): %s", game_pk, inn.get("num"), exc)
            skipped += 1
    return inserted, skipped
```

**Modify `fetch_and_store_game`:**

1. Before the `try:` block, initialise abbreviations so linescore step can access them:
```python
away_abbr = home_abbr = None   # resolved from game feed; needed for inning upsert
```

2. In the linescore section (step 2), after `log_response(...)`:
```python
        if linescore is not None:
            log_response("linescore", linescore, date_str=date_for_log, game_pk=game_pk)
            summary["endpoints_logged"].append("linescore")
            if away_abbr and home_abbr:
                ins, skip = _upsert_inning_scores(conn, game_pk, linescore, away_abbr, home_abbr)
                summary["innings_inserted"] = ins
                summary["innings_skipped"]  = skip
        else:
            summary["errors"].append("linescore: fetch returned None")
```

3. Add `innings_inserted` and `innings_skipped` to the initial `summary` dict:
```python
    summary: dict = {
        "fetched": False,
        "game_pk": game_pk,
        "date": None,
        "endpoints_logged": [],
        "game_upserted": False,
        "game_state_inserted": False,
        "plays_inserted": 0,
        "plays_skipped": 0,
        "innings_inserted": 0,
        "innings_skipped": 0,
        "errors": [],
    }
```

Run tests → all pass.
Run full suite: `python -m pytest tests/ --ignore=test_results.txt -q` → no regressions.

### Commit
```
git add mlb/game_store.py tests/test_mlb_inning_scores.py
git commit -m "feat: parse linescore innings into mlb_inning_scores (Task 6b)"
```

---

## Task 3 — `mlb/team_context.py`: use inning scores; add `context_confidence`

**Files modified:** `mlb/team_context.py`

### Failing tests (add to `tests/test_mlb_team_context.py`)

```python
# Helper for inning data — add after _insert_plays_f5

def _insert_inning_scores(conn, game_pk: int, innings: list[tuple[int, int, int]]) -> None:
    """Insert (inning, away_runs, home_runs) rows for a game."""
    for inning, away_r, home_r in innings:
        conn.execute(
            """
            INSERT OR REPLACE INTO mlb_inning_scores
              (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
            VALUES (?,?,'A','H',?,?,datetime('now'))
            """,
            (game_pk, inning, away_r, home_r),
        )
    conn.commit()


# 15. F5 computed from inning scores
def test_f5_from_inning_scores(conn):
    _insert_game(conn, 500, "NYY", "BOS", 7, 4)
    # F5: NYY(away) 2+0+1+0+1=4, BOS(home) 0+1+0+2+0=3
    # Late: NYY 0+2+0+1=3, BOS 3+0+1+0=4
    _insert_inning_scores(conn, 500, [
        (1,2,0),(2,0,1),(3,1,0),(4,0,2),(5,1,0),
        (6,0,3),(7,2,0),(8,0,1),(9,1,0),
    ])
    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert ctx["f5_sample_size"] == 1
    assert abs(ctx["f5_runs_per_game"] - 4.0) < 0.01
    assert abs(ctx["f5_runs_allowed_per_game"] - 3.0) < 0.01


# 16. Late runs from inning scores
def test_late_runs_from_inning_scores(conn):
    _insert_game(conn, 501, "NYY", "BOS", 7, 4)
    _insert_inning_scores(conn, 501, [
        (1,2,0),(2,0,1),(3,1,0),(4,0,2),(5,1,0),
        (6,0,3),(7,2,0),(8,0,1),(9,1,0),
    ])
    ctx = compute_team_context("NYY", "2026", conn)
    assert abs(ctx["late_runs_per_game"] - 3.0) < 0.01
    assert abs(ctx["late_runs_allowed_per_game"] - 4.0) < 0.01


# 17. Bullpen risk rises with high late runs allowed
def test_bullpen_risk_high_when_late_runs_allowed_high(conn):
    for i in range(5):
        _insert_game(conn, 510+i, "ATL", f"T{i}", 5, 8)  # ATL allows 8
        # F5: ATL scores 2, allows 3; late: ATL scores 3, allows 5
        _insert_inning_scores(conn, 510+i, [
            (1,0,1),(2,1,1),(3,0,0),(4,1,1),(5,0,0),
            (6,1,2),(7,1,1),(8,0,1),(9,1,1),
        ])
    ctx = compute_team_context("ATL", "2026", conn)
    assert ctx["bullpen_risk_rating"] > 50


# 18. Comeback rating rises with high late runs scored
def test_comeback_rating_high_when_late_scoring_high(conn):
    for i in range(5):
        _insert_game(conn, 520+i, "HOU", f"T{i}", 8, 3)
        # HOU late scoring: 5 runs in innings 6+
        _insert_inning_scores(conn, 520+i, [
            (1,1,1),(2,0,1),(3,1,0),(4,0,0),(5,1,1),
            (6,2,0),(7,1,0),(8,1,1),(9,1,0),
        ])
    ctx = compute_team_context("HOU", "2026", conn)
    assert ctx["comeback_scoring_rating"] > 50


# 19. context_confidence is low for <10 games
def test_context_confidence_low(conn):
    for i in range(5):
        _insert_game(conn, 530+i, "COL", f"T{i}", 4, 3)
    ctx = compute_team_context("COL", "2026", conn)
    assert ctx["context_confidence"] == "low"


# 20. context_confidence is medium for 10-30 games
def test_context_confidence_medium(conn):
    for i in range(15):
        _insert_game(conn, 540+i, "MIL", f"T{i}", 4, 3, date_suffix=f"04-{i+1:02d}")
    ctx = compute_team_context("MIL", "2026", conn)
    assert ctx["context_confidence"] == "medium"


# 21. context_confidence is high for 31+ games
def test_context_confidence_high(conn):
    for i in range(35):
        _insert_game(conn, 560+i, "LAD", f"T{i}", 5, 3, date_suffix=f"04-{i+1:02d}")
    ctx = compute_team_context("LAD", "2026", conn)
    assert ctx["context_confidence"] == "high"
```

Run: `python -m pytest tests/test_mlb_team_context.py -x -k "inning or confidence or bullpen or comeback"` → fails.

### Implementation: update `mlb/team_context.py`

**Replace `_get_f5_scores`** with `_get_inning_totals`:

```python
def _get_inning_totals(
    game_pk: int,
    conn: sqlite3.Connection,
) -> Optional[dict[str, int]]:
    """
    Returns {away_f5, home_f5, away_late, home_late} from mlb_inning_scores,
    or None if no inning data is stored for this game.
    F5  = innings 1-5.  Late = innings 6+.
    """
    rows = conn.execute(
        "SELECT inning, away_runs, home_runs FROM mlb_inning_scores "
        "WHERE game_pk = ? ORDER BY inning",
        (game_pk,),
    ).fetchall()
    if not rows:
        return None
    return {
        "away_f5":   sum((r["away_runs"] or 0) for r in rows if r["inning"] <= 5),
        "home_f5":   sum((r["home_runs"] or 0) for r in rows if r["inning"] <= 5),
        "away_late": sum((r["away_runs"] or 0) for r in rows if r["inning"] >= 6),
        "home_late": sum((r["home_runs"] or 0) for r in rows if r["inning"] >= 6),
    }
```

**Add `_context_confidence`** helper:

```python
def _context_confidence(games_played: int) -> str:
    """
    Simple three-tier confidence label for candidate logic to use as a weight.
    low    < 10 games  — early season; ratings are noisy
    medium 10-30 games — reasonable sample
    high   31+ games   — full season evidence
    """
    if games_played >= 31:
        return "high"
    if games_played >= 10:
        return "medium"
    return "low"
```

**Replace the F5/late computation block** inside `compute_team_context`:

Replace (starting at "# ── F5 and late stats"):
```python
    # ── F5 and late stats (requires mlb_inning_scores rows for the game) ─────
    f5_scored_list    = []
    f5_allowed_list   = []
    late_scored_list  = []
    late_allowed_list = []

    for game, side in all_games:
        totals = _get_inning_totals(game["game_pk"], conn)
        if totals is None:
            continue

        if side == "away":
            f5_scored_list.append(totals["away_f5"])
            f5_allowed_list.append(totals["home_f5"])
            late_scored_list.append(totals["away_late"])
            late_allowed_list.append(totals["home_late"])
        else:
            f5_scored_list.append(totals["home_f5"])
            f5_allowed_list.append(totals["away_f5"])
            late_scored_list.append(totals["home_late"])
            late_allowed_list.append(totals["away_late"])
```

(Remove the old negative-guard check — inning sums can't be negative.)

**Add `context_confidence` to the return dict:**
```python
        "context_confidence":             _context_confidence(len(all_games)),
```

**Update `_upsert_team_context`** to include `context_confidence` in the INSERT column list and values, and add it to the `ON CONFLICT DO UPDATE SET` clause:
```sql
-- in INSERT column list, after f5_sample_size:
context_confidence
-- in VALUES tuple: ctx["context_confidence"]
-- in DO UPDATE SET:
context_confidence = excluded.context_confidence,
```

Run tests → all pass.
Run full suite: `python -m pytest tests/ --ignore=test_results.txt -q` → no regressions.

### Commit
```
git add mlb/team_context.py tests/test_mlb_team_context.py
git commit -m "feat: inning-based F5/late context + context_confidence (Task 6c)"
```

---

## Task 4 — API schema + frontend update

**Files modified:** `api/schemas.py`, `frontend/src/types/api.ts`, `frontend/src/pages/MLBTeamContext.tsx`

### `api/schemas.py` — add `context_confidence` to `TeamContextOut`

```python
class TeamContextOut(BaseModel):
    # ... (existing fields unchanged) ...
    sample_size: int
    f5_sample_size: int
    context_confidence: str = "low"   # ← ADD
    last_updated: str
```

### `frontend/src/types/api.ts` — add field to `TeamContext`

```typescript
export interface TeamContext {
  // ... (existing fields unchanged) ...
  sample_size: number
  f5_sample_size: number
  context_confidence: string   // ← ADD
  last_updated: string
}
```

### `frontend/src/pages/MLBTeamContext.tsx` — full replacement

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

function ConfBadge({ value }: { value: string }) {
  const style =
    value === 'high'   ? 'bg-green-900/40 text-green-400 border-green-800/50' :
    value === 'medium' ? 'bg-yellow-900/40 text-yellow-400 border-yellow-800/50' :
                         'bg-slate-800 text-slate-500 border-slate-700'
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${style}`}>
      {value}
    </span>
  )
}

function TH({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`pb-2 pr-3 text-[11px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap${right ? ' text-right' : ''}`}>
      {children}
    </th>
  )
}

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
            Season-to-date ratings · 0–100 · ~50 = league average · F5n = inning-data sample
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
            <span className="ml-2 text-yellow-400">{refresh.data.errors.length} error(s).</span>
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
                <TH right>Conf</TH>
                <TH right>RPG</TH>
                <TH right>RA/G</TH>
                <TH right>Off</TH>
                <TH right>Def</TH>
                <TH right>F5 RPG</TH>
                <TH right>F5 RA/G</TH>
                <TH right>F5-Off</TH>
                <TH right>F5-Pit</TH>
                <TH right>Late+</TH>
                <TH right>Late-</TH>
                <TH right>BP Risk</TH>
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
                  <td className="py-2 pr-3 text-right"><ConfBadge value={t.context_confidence} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.runs_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.runs_allowed_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.offense_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.defense_pitching_rating} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.f5_runs_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.f5_runs_allowed_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.f5_offense_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.f5_pitching_risk_rating} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.late_runs_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.late_runs_allowed_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.bullpen_risk_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.comeback_scoring_rating} /></td>
                  <td className="py-2 pr-3 text-right font-medium">
                    <RatingCell value={t.overall_context_score} />
                  </td>
                  <td className="py-2 text-right text-[11px] text-slate-600">{t.f5_sample_size}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.total ?? 0) === 0 && (
            <p className="text-slate-500 mt-6 text-center text-sm">
              No team context data yet.{' '}
              <button
                onClick={() => refresh.mutate()}
                disabled={refresh.isPending}
                className="underline hover:text-slate-300"
              >
                Click here to compute from stored games.
              </button>
            </p>
          )}
        </div>
      )}
    </div>
  )
}
```

### Commit
```
git add api/schemas.py frontend/src/types/api.ts frontend/src/pages/MLBTeamContext.tsx
git commit -m "feat: context_confidence in API + updated MLBTeamContext table (Task 6d)"
```

---

## Task 5 — Update `_seed_games.py` to backfill inning data, then full test run

**Files modified:** `_seed_games.py`

The seed script currently only calls `fetch_and_store_schedule`, which does not call `fetch_and_store_game`, so `mlb_inning_scores` stays empty. Update it to also fetch game details (linescore included) for the last N days of final games.

```python
from mlb.game_store import fetch_and_store_schedule, fetch_and_store_game
from mlb.team_context import refresh_team_context
from db.schema import init_db
import os

DB_PATH = os.environ.get("DB_PATH", "kalshi_mlb.db")

SCHEDULE_DATES = [
    "2026-06-05", "2026-06-06", "2026-06-07",
    "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11",
]

# Fetch game-level detail (linescore → inning scores) for this many days back.
# Each game = ~4 API calls. Keep small to avoid hammering the API.
DETAIL_DATES = ["2026-06-09", "2026-06-10", "2026-06-11"]

conn = init_db(DB_PATH)

print("=== Fetching schedules ===")
total_games = 0
for d in SCHEDULE_DATES:
    r = fetch_and_store_schedule(d, conn=conn)
    total_games += r["games_seen"]
    status = "OK" if r["fetched"] else "FAIL"
    print(f"  {d}: {status}  games={r['games_seen']}")
print(f"  Total games stored: {total_games}\n")

print("=== Fetching game details (linescore/inning data) ===")
detail_pks = [
    r["game_pk"]
    for r in conn.execute(
        "SELECT game_pk FROM mlb_games WHERE is_final=1 AND game_date IN ({})".format(
            ",".join("?" * len(DETAIL_DATES))
        ),
        DETAIL_DATES,
    ).fetchall()
]
print(f"  Games to detail-fetch: {len(detail_pks)}")
innings_total = 0
for pk in detail_pks:
    r = fetch_and_store_game(pk, conn=conn)
    innings_total += r.get("innings_inserted", 0)
print(f"  Inning rows inserted: {innings_total}\n")

print("=== Refreshing team context ===")
result = refresh_team_context("2026", conn=conn)
print(f"  Teams refreshed: {result['team_count']}")
print(f"  Teams: {result['teams']}")
if result["errors"]:
    print(f"  Errors: {result['errors']}")

conn.close()
```

### Final test run
```
python -m pytest tests/ --ignore=test_results.txt -v 2>&1 | tail -5
```
Expected: ≥ 536 passed (519 existing + 10 inning tests + 7 new context tests), 0 failures.

### Final smoke test
```
python _seed_games.py
```
Then open http://localhost:5173/mlb-context → click **Refresh Ratings** → verify:
- F5 RPG and F5 RA/G columns show real numbers (not `—`) for teams with detail-fetched games
- F5-Off, F5-Pit, BP Risk, Late Risk, Comeback show values other than 50
- Conf badge shows `low` (sample is still < 10 games per team from 3-day detail window)
- F5n column shows > 0

### Commit
```
git add _seed_games.py
git commit -m "feat: seed script fetches game details for inning data (Task 6e)"
```

---

## Definition of Done
- [ ] `mlb_inning_scores` table exists after `init_db`
- [ ] `fetch_and_store_game` populates inning rows from linescore; idempotent
- [ ] Missing/empty linescore does not crash
- [ ] `compute_team_context` uses inning sums for F5/late metrics
- [ ] `context_confidence` is `low`/`medium`/`high` based on `games_played`
- [ ] `TeamContextOut` and `TeamContext` TS interface include `context_confidence`
- [ ] React table shows F5 RPG, F5 RA/G, Late+, Late-, Conf badge, F5n
- [ ] All ≥536 tests pass, no regressions
- [ ] After running `_seed_games.py`, F5 columns are no longer stuck at `—`/50
