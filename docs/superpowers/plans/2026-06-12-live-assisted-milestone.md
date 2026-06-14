# Plan: Live-Assisted Manual Testing Milestone

**Date:** 2026-06-12

## Goal
Get the app functional enough for live-assisted, manually executed real-money testing within one week — no auto-trading, observation and decision-support only.

## Architecture
```
MLB Stats API ──► mlb/game_store.py ──► mlb_game_states, mlb_inning_scores
Kalshi WS     ──► kalshi/normalizer.py ──► kalshi_markets (prices updated live)

live_watcher.py (new polling loop, 30s cadence)
  ├── reads: mlb_game_states (latest per game)
  ├── reads: kalshi_markets (by game_id + market_type)
  ├── reads: mlb_team_context (by team_abbr)
  ├── calls: mlb/candidate_generator.py (3 candidate types)
  ├── calls: mlb/guardrails.py (8 checks)
  └── writes: candidate_events

FastAPI
  ├── GET  /api/candidates/live        ← new
  ├── POST /api/journal                ← new
  ├── GET  /api/journal                ← new
  └── PATCH /api/journal/{id}          ← new

React
  └── CandidateReview.tsx (new page)
```

## Tech Stack
- SQLite (WAL mode, existing pattern), FastAPI, Pydantic, React + TanStack Query
- MLB Stats API (read-only, no auth) via `mlb/stats_api.py`
- Kalshi prices from `kalshi_markets` table (kept live by `kalshi_ws.py`)

---

## Priority Sequence (P1 done; P2–P8 below)

| # | Area | Status |
|---|------|--------|
| P1 | Inning-level F5/late context | ✅ done |
| P2 | Kalshi market semantics | ⬜ Task A |
| P3 | F5 settlement safety | ⬜ Task B |
| P4 | candidate_events table + API | ⬜ Task C |
| P5 | Live candidate generator | ⬜ Task D |
| P6 | Candidate review dashboard | ⬜ Task E |
| P7 | Manual trade journal | ⬜ Task F |
| P8 | Live-trade guardrails | ⬜ Task G |

---

## Task A — Kalshi Market Semantics (P2)

**Files:** `db/schema.py`, `kalshi/semantics.py` (new), `tests/test_kalshi_semantics.py` (new)

### A1 — DB migration: add `yes_means` + `is_semantics_clear` to `kalshi_markets`

Add to `_apply_migrations` in `db/schema.py`:
```python
"ALTER TABLE kalshi_markets ADD COLUMN yes_means TEXT NOT NULL DEFAULT 'unknown'",
"ALTER TABLE kalshi_markets ADD COLUMN is_semantics_clear INTEGER NOT NULL DEFAULT 0",
"ALTER TABLE kalshi_markets ADD COLUMN game_open_price_cents INTEGER",
```

`yes_means` values: `'over'` | `'under'` | `'home_win'` | `'away_win'` | `'unknown'`
`game_open_price_cents`: mid-price (bid+ask)/2 captured once when game first goes live; used for reprice detection.

### A2 — New module: `kalshi/semantics.py`

```python
"""
kalshi/semantics.py — Parse YES/NO contract direction from Kalshi market metadata.

Rules:
  full_game_total / f5_total:
    rules_primary contains "exceed" or "over" → yes_means='over'
    rules_primary contains "under" or "not exceed" → yes_means='under'
    title/subtitle fallback for same keywords
  moneyline:
    YES = named team wins — we just record 'home_win' or 'away_win' from away_team/home_team fields
    We do NOT try to parse team name here; reconciler already handles it.
    → yes_means='home_win' if home_team in title, else 'away_win' if away_team in title, else 'unknown'
  spread_run_line / f5_spread:
    always → yes_means='unknown', is_semantics_clear=0
  team_total:
    same text search as totals
"""
import re
import sqlite3
from typing import Optional

_OVER_WORDS  = re.compile(r'\b(over|exceed|more than|greater)\b', re.IGNORECASE)
_UNDER_WORDS = re.compile(r'\b(under|not exceed|fewer|less than)\b', re.IGNORECASE)


def parse_yes_means(
    market_type: Optional[str],
    title: Optional[str],
    subtitle: Optional[str],
    rules_primary: Optional[str],
    away_team: Optional[str],
    home_team: Optional[str],
) -> tuple[str, bool]:
    """
    Return (yes_means, is_semantics_clear).
    is_semantics_clear=True only when yes_means is unambiguously resolved.
    """
    mtype = (market_type or "").lower()

    # Spread: always ambiguous
    if mtype in ("spread_run_line", "f5_spread"):
        return ("unknown", False)

    # Moneyline: team-based; reconciler handles team resolution, we just flag direction
    if mtype == "moneyline":
        combined = " ".join(filter(None, [title, subtitle])).upper()
        home = (home_team or "").upper()
        away = (away_team or "").upper()
        if home and home in combined:
            return ("home_win", True)
        if away and away in combined:
            return ("away_win", True)
        return ("unknown", False)

    # Totals (full_game_total, f5_total, team_total)
    if mtype in ("full_game_total", "f5_total", "team_total"):
        # Prefer rules_primary (most explicit), then title, then subtitle
        for text in [rules_primary, title, subtitle]:
            if not text:
                continue
            if _OVER_WORDS.search(text):
                return ("over", True)
            if _UNDER_WORDS.search(text):
                return ("under", True)
        return ("unknown", False)

    return ("unknown", False)


def refresh_market_semantics(conn: sqlite3.Connection) -> dict:
    """
    Backfill yes_means + is_semantics_clear for all rows in kalshi_markets.
    Safe to call multiple times.
    """
    rows = conn.execute("SELECT * FROM kalshi_markets").fetchall()
    updated = skipped = 0
    for row in rows:
        yes_means, clear = parse_yes_means(
            row["market_type"], row["title"], row["subtitle"],
            row["rules_primary"], row["away_team"], row["home_team"],
        )
        conn.execute(
            "UPDATE kalshi_markets SET yes_means=?, is_semantics_clear=? WHERE id=?",
            (yes_means, int(clear), row["id"]),
        )
        if clear:
            updated += 1
        else:
            skipped += 1
    conn.commit()
    return {"updated": updated, "skipped_unclear": skipped}
```

### A3 — Tests: `tests/test_kalshi_semantics.py`

```python
import pytest
from kalshi.semantics import parse_yes_means, refresh_market_semantics
from db.schema import init_db


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _ins(conn, market_ticker, market_type, title="", subtitle="", rules="", away="NYY", home="BOS"):
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, subtitle, rules_primary,
            away_team, home_team, raw_json, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,'{}',datetime('now'),datetime('now'))""",
        (market_ticker, "EVT", market_type, title, subtitle, rules, away, home),
    )
    conn.commit()


def test_full_game_total_over_from_rules():
    yes, clear = parse_yes_means("full_game_total", None, None,
                                  "The market settles YES if total runs exceed 8.5", None, None)
    assert yes == "over" and clear is True


def test_full_game_total_under_from_rules():
    yes, clear = parse_yes_means("full_game_total", None, None,
                                  "Settles YES if total runs are under 8.5", None, None)
    assert yes == "under" and clear is True


def test_f5_total_over_from_title():
    yes, clear = parse_yes_means("f5_total", "F5 Total Over 4.5", None, None, None, None)
    assert yes == "over" and clear is True


def test_f5_total_under_from_title():
    yes, clear = parse_yes_means("f5_total", "F5 Total Under 4.5", None, None, None, None)
    assert yes == "under" and clear is True


def test_spread_always_unknown():
    yes, clear = parse_yes_means("spread_run_line", "NYY -1.5", None, None, "NYY", "BOS")
    assert yes == "unknown" and clear is False


def test_moneyline_home_team():
    yes, clear = parse_yes_means("moneyline", "BOS Moneyline", None, None, "NYY", "BOS")
    assert yes == "home_win" and clear is True


def test_moneyline_away_team():
    yes, clear = parse_yes_means("moneyline", "NYY Moneyline", None, None, "NYY", "BOS")
    assert yes == "away_win" and clear is True


def test_moneyline_no_team_match():
    yes, clear = parse_yes_means("moneyline", "Who wins?", None, None, "NYY", "BOS")
    assert yes == "unknown" and clear is False


def test_no_text_anywhere_unknown():
    yes, clear = parse_yes_means("full_game_total", None, None, None, None, None)
    assert yes == "unknown" and clear is False


def test_refresh_market_semantics_backfills(conn):
    _ins(conn, "MKT1", "full_game_total", rules="exceed 8.5")
    _ins(conn, "MKT2", "spread_run_line", title="NYY -1.5")
    result = refresh_market_semantics(conn)
    assert result["updated"] == 1
    assert result["skipped_unclear"] == 1
    row1 = conn.execute("SELECT yes_means, is_semantics_clear FROM kalshi_markets WHERE market_ticker='MKT1'").fetchone()
    assert row1["yes_means"] == "over"
    assert row1["is_semantics_clear"] == 1
```

Run: `python -m pytest tests/test_kalshi_semantics.py -x` → all pass.

### Commit A
```
git add db/schema.py kalshi/semantics.py tests/test_kalshi_semantics.py
git commit -m "feat: Kalshi market semantics — yes_means, is_semantics_clear (Task A)"
```

---

## Task B — F5 Settlement Safety (P3)

**Files:** `mlb/reconciler.py`, `tests/test_mlb_reconciler.py`

### B1 — Add F5 total sum helper

Add to `mlb/reconciler.py` after the `_UNDER_RE` constant:

```python
def _get_f5_total(game_pk: int, conn: sqlite3.Connection) -> Optional[int]:
    """Sum innings 1–5 from mlb_inning_scores. Returns None if no inning data."""
    rows = conn.execute(
        "SELECT away_runs, home_runs FROM mlb_inning_scores "
        "WHERE game_pk=? AND inning<=5",
        (game_pk,),
    ).fetchall()
    if not rows:
        return None
    return sum((r["away_runs"] or 0) + (r["home_runs"] or 0) for r in rows)
```

### B2 — Extend direction values for F5 markets

Change `_direction_from_market` so F5 totals return distinct direction strings:

```python
    if mtype == "f5_total":
        if _OVER_RE.search(text):
            return "f5_over_yes"
        if _UNDER_RE.search(text):
            return "f5_under_yes"

    if mtype == "full_game_total":
        if _OVER_RE.search(text):
            return "over_yes"
        if _UNDER_RE.search(text):
            return "under_yes"
```

### B3 — Extend `_determine_outcome` to handle F5 directions

In `_determine_outcome`, add this block **before** the full-game total block:

```python
    # ── F5 total ──────────────────────────────────────────────────────────
    if direction in ("f5_over_yes", "f5_under_yes"):
        game_pk_val = pos.get("game_pk") if hasattr(pos, "get") else None
        if game_pk_val is None:
            # Try to look up game_pk from mlb_games via game_id
            g = conn.execute(
                "SELECT game_pk FROM mlb_games WHERE game_id=? LIMIT 1",
                (pos["game_id"],),
            ).fetchone() if conn else None
            game_pk_val = g["game_pk"] if g else None

        f5_total = _get_f5_total(game_pk_val, conn) if game_pk_val and conn else None

        if f5_total is None:
            return "needs_review"   # no inning data — settle manually
        if f5_total == market_line:
            return "needs_review"   # push
        if direction == "f5_over_yes":
            yes_result = "win" if f5_total > market_line else "loss"
        else:
            yes_result = "win" if f5_total < market_line else "loss"
        return yes_result if side == "YES" else _flip(yes_result)
```

`_settle_position` and `reconcile_game_final` need access to `conn` inside `_determine_outcome`. The call signature of `_determine_outcome` already receives `market` (which doesn't carry `conn`). Pass `conn` explicitly:

Change `_determine_outcome` signature:
```python
def _determine_outcome(
    pos: sqlite3.Row,
    final_data: dict,
    direction: str,
    market: Optional[sqlite3.Row] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
```

Update call site in `reconcile_game_final`:
```python
outcome = _determine_outcome(pos, final_data, direction, market=market, conn=conn)
```

### B4 — Failing tests for F5 settlement

Add to `tests/test_mlb_reconciler.py`:

```python
# helpers already exist in reconciler test; add these:

def _insert_inning_scores(conn, game_pk, innings):
    for inning, away_r, home_r in innings:
        conn.execute(
            """INSERT OR REPLACE INTO mlb_inning_scores
               (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
               VALUES (?,?,'A','H',?,?,datetime('now'))""",
            (game_pk, inning, away_r, home_r),
        )
    conn.commit()


def test_f5_total_settles_from_inning_scores(conn):
    # F5: 3 away + 2 home = 5 runs; line = 4.5; f5_over_yes → win
    _insert_game(conn, game_pk=9001, game_id="NYY@BOS",
                 away="NYY", home="BOS", away_score=7, home_score=4, is_final=1)
    _insert_inning_scores(conn, 9001, [
        (1,1,0),(2,1,0),(3,1,1),(4,0,1),(5,0,0),
        (6,2,1),(7,1,0),(8,0,0),(9,1,1),
    ])
    # F5: away=3, home=2, total=5
    _insert_market(conn, game_id="NYY@BOS", line=4.5, mtype="f5_total",
                   rules="settles YES if total runs exceed 4.5")
    _insert_position(conn, game_id="NYY@BOS", line=4.5, side="YES",
                     signal_type="f5_total_test")
    from mlb.reconciler import reconcile_game_final
    result = reconcile_game_final(9001, conn=conn)
    assert result.settled == 1
    pos = conn.execute("SELECT net_pnl_cents, settlement_status FROM paper_positions WHERE game_id='NYY@BOS'").fetchone()
    assert pos["settlement_status"] == "settled_confirmed"


def test_f5_total_uses_f5_not_final_total(conn):
    # Final total = 11 (over 4.5), F5 total = 4 (under 4.5)
    # f5_over_yes position should LOSE
    _insert_game(conn, game_pk=9002, game_id="ATL@PHI",
                 away="ATL", home="PHI", away_score=7, home_score=4, is_final=1)
    _insert_inning_scores(conn, 9002, [
        (1,0,0),(2,1,1),(3,0,1),(4,0,1),(5,1,0),  # F5: ATL=2, PHI=3, total=5? no: 2+3=5
        # Actually let me make F5=4: (1,1,0),(2,1,0),(3,0,1),(4,0,1),(5,0,0) → ATL=2,PHI=2,total=4
        # override:
    ])
    # Redo with F5 total = 4
    conn.execute("DELETE FROM mlb_inning_scores WHERE game_pk=9002")
    _insert_inning_scores(conn, 9002, [
        (1,1,0),(2,1,0),(3,0,1),(4,0,1),(5,0,0),  # F5: ATL=2, PHI=2 → 4
        (6,2,1),(7,1,0),(8,1,1),(9,1,0),           # late: 7 more
    ])
    _insert_market(conn, game_id="ATL@PHI", line=4.5, mtype="f5_total",
                   rules="settles YES if total runs exceed 4.5")
    _insert_position(conn, game_id="ATL@PHI", line=4.5, side="YES",
                     signal_type="f5_total_test")
    from mlb.reconciler import reconcile_game_final
    result = reconcile_game_final(9002, conn=conn)
    assert result.settled == 1
    pos = conn.execute("SELECT net_pnl_cents FROM paper_positions WHERE game_id='ATL@PHI'").fetchone()
    assert pos["net_pnl_cents"] < 0   # loss


def test_f5_total_no_inning_data_needs_review(conn):
    _insert_game(conn, game_pk=9003, game_id="HOU@TEX",
                 away="HOU", home="TEX", away_score=5, home_score=3, is_final=1)
    # No inning scores inserted
    _insert_market(conn, game_id="HOU@TEX", line=7.5, mtype="f5_total",
                   rules="settles YES if total runs exceed 7.5")
    _insert_position(conn, game_id="HOU@TEX", line=7.5, side="YES",
                     signal_type="f5_total_test")
    from mlb.reconciler import reconcile_game_final
    result = reconcile_game_final(9003, conn=conn)
    assert result.needs_review == 1
```

Run: `python -m pytest tests/test_mlb_reconciler.py -x` → all pass.

### Commit B
```
git add mlb/reconciler.py tests/test_mlb_reconciler.py
git commit -m "feat: F5 settlement uses inning scores not final total (Task B)"
```

---

## Task C — candidate_events Table + API (P4)

**Files:** `db/schema.py`, `api/schemas.py`, `api/routers/candidates.py`, `tests/test_candidate_events.py` (new)

### C1 — DB: add `candidate_events` table

Append to `DDL` in `db/schema.py` (before the closing `"""`):

```sql
-- ── Live candidate tracking ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS candidate_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,

    -- Game identity
    game_pk                 INTEGER NOT NULL,
    game_id                 TEXT NOT NULL,
    away_abbr               TEXT NOT NULL,
    home_abbr               TEXT NOT NULL,

    -- Candidate classification
    candidate_type          TEXT NOT NULL,

    -- Game state at trigger time
    trigger_reason          TEXT NOT NULL,
    inning                  INTEGER,
    inning_half             TEXT,
    away_score              INTEGER,
    home_score              INTEGER,
    current_total           INTEGER,

    -- Market at trigger time
    market_ticker           TEXT,
    market_type             TEXT,
    line_value              REAL,
    yes_bid_cents           INTEGER,
    yes_ask_cents           INTEGER,
    spread_cents            INTEGER,
    mid_cents               INTEGER,
    game_open_price_cents   INTEGER,
    price_move_cents        INTEGER,

    -- Proposed trade
    suggested_side          TEXT,
    estimated_entry_cents   INTEGER,

    -- Team context snapshot (denormalized)
    away_context_confidence TEXT,
    home_context_confidence TEXT,
    away_overall_score      REAL,
    home_overall_score      REAL,

    -- Guardrails
    guardrail_status        TEXT NOT NULL DEFAULT 'pending',
    guardrail_reasons_json  TEXT NOT NULL DEFAULT '[]',

    -- Lifecycle
    status                  TEXT NOT NULL DEFAULT 'watching',
    notes                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_events_game   ON candidate_events(game_id);
CREATE INDEX IF NOT EXISTS idx_candidate_events_status ON candidate_events(status);
CREATE INDEX IF NOT EXISTS idx_candidate_events_type   ON candidate_events(candidate_type);
```

Also add `candidate_events` to `_apply_migrations` (schema is idempotent via `CREATE TABLE IF NOT EXISTS` — no explicit migration needed for fresh tables; the `executescript(DDL)` call handles it).

### C2 — Pydantic schema

Add to `api/schemas.py`:

```python
# ---------------------------------------------------------------------------
# Live candidate events
# ---------------------------------------------------------------------------

CANDIDATE_TYPE_LABELS: dict[str, str] = {
    "full_game_total_extreme_reprice_watch": "Full Game Reprice",
    "f5_total_overreaction_fade_watch":      "F5 Overreaction",
    "trailing_team_total_lag_watch":         "Trailing Team Lag",
}

GUARDRAIL_STATUS_LABELS: dict[str, str] = {
    "eligible":    "Eligible",
    "observe_only": "Observe Only",
    "blocked":     "Blocked",
    "pending":     "Pending",
}


class CandidateEventOut(BaseModel):
    id: int
    created_at: str
    updated_at: str
    game_pk: int
    game_id: str
    away_abbr: str
    home_abbr: str
    candidate_type: str
    candidate_type_label: str
    trigger_reason: str
    inning: Optional[int] = None
    inning_half: Optional[str] = None
    away_score: Optional[int] = None
    home_score: Optional[int] = None
    current_total: Optional[int] = None
    market_ticker: Optional[str] = None
    market_type: Optional[str] = None
    line_value: Optional[float] = None
    yes_bid_cents: Optional[int] = None
    yes_ask_cents: Optional[int] = None
    spread_cents: Optional[int] = None
    mid_cents: Optional[int] = None
    game_open_price_cents: Optional[int] = None
    price_move_cents: Optional[int] = None
    suggested_side: Optional[str] = None
    estimated_entry_cents: Optional[int] = None
    away_context_confidence: Optional[str] = None
    home_context_confidence: Optional[str] = None
    away_overall_score: Optional[float] = None
    home_overall_score: Optional[float] = None
    guardrail_status: str
    guardrail_reasons: list[str]
    status: str
    notes: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _enrich(cls, data: Any) -> Any:
        if isinstance(data, dict):
            ct = data.get("candidate_type", "")
            data["candidate_type_label"] = CANDIDATE_TYPE_LABELS.get(ct, ct.replace("_", " ").title())
            data["guardrail_reasons"] = json.loads(data.pop("guardrail_reasons_json", None) or "[]")
        return data
```

### C3 — API endpoint

Add to `api/routers/candidates.py` (alongside existing pace-fade routes):

```python
from api.schemas import CandidateEventOut

@router.get("/live", response_model=ListResponse[CandidateEventOut])
def list_live_candidates(
    game_id: Optional[str] = None,
    candidate_type: Optional[str] = None,
    status: Optional[str] = None,
    guardrail_status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    filters = []
    params: list = []
    if game_id:
        filters.append("game_id = ?"); params.append(game_id)
    if candidate_type:
        filters.append("candidate_type = ?"); params.append(candidate_type)
    if status:
        filters.append("status = ?"); params.append(status)
    if guardrail_status:
        filters.append("guardrail_status = ?"); params.append(guardrail_status)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM candidate_events {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM candidate_events {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return ListResponse(total=total, items=[dict(r) for r in rows])
```

Register in `api/main.py`: the candidates router already exists — the new route is added to the same file.

### C4 — Tests: `tests/test_candidate_events.py`

```python
from db.schema import init_db
from datetime import datetime


def _now():
    return datetime.now().isoformat()


def _insert_candidate(conn, candidate_type="full_game_total_extreme_reprice_watch",
                       game_id="NYY@BOS", guardrail_status="eligible", status="watching"):
    conn.execute(
        """INSERT INTO candidate_events
           (created_at, updated_at, game_pk, game_id, away_abbr, home_abbr,
            candidate_type, trigger_reason, guardrail_status, guardrail_reasons_json, status)
           VALUES (?,?,?,?,?,?,?,?,?,'[]',?)""",
        (_now(), _now(), 1, game_id, "NYY", "BOS",
         candidate_type, "test trigger", guardrail_status, status),
    )
    conn.commit()


def test_candidate_events_table_exists():
    conn = init_db(":memory:")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_events'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_insert_and_retrieve_candidate():
    conn = init_db(":memory:")
    _insert_candidate(conn)
    row = conn.execute("SELECT * FROM candidate_events").fetchone()
    assert row["candidate_type"] == "full_game_total_extreme_reprice_watch"
    assert row["guardrail_status"] == "eligible"
    conn.close()


def test_guardrail_status_filter():
    conn = init_db(":memory:")
    _insert_candidate(conn, guardrail_status="eligible")
    _insert_candidate(conn, game_id="ATL@PHI", guardrail_status="blocked")
    rows = conn.execute(
        "SELECT * FROM candidate_events WHERE guardrail_status='blocked'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


def test_candidate_type_index_works():
    conn = init_db(":memory:")
    _insert_candidate(conn, candidate_type="f5_total_overreaction_fade_watch")
    rows = conn.execute(
        "SELECT * FROM candidate_events WHERE candidate_type='f5_total_overreaction_fade_watch'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()
```

Run: `python -m pytest tests/test_candidate_events.py -x` → all pass.

### Commit C
```
git add db/schema.py api/schemas.py api/routers/candidates.py tests/test_candidate_events.py
git commit -m "feat: candidate_events table + /api/candidates/live endpoint (Task C)"
```

---

## Task D — Live Candidate Generator (P5)

**Files:** `mlb/guardrails.py` (new), `mlb/candidate_generator.py` (new), `live_watcher.py` (new), `tests/test_candidate_generator.py` (new)

### D1 — Guardrails module: `mlb/guardrails.py`

```python
"""
mlb/guardrails.py — 8 guardrail checks for live candidate eligibility.

Checks (in order):
  1. market_semantics_unclear     — is_semantics_clear = 0
  2. bid_ask_missing              — yes_bid or yes_ask is None
  3. hard_block_spread_over_12c   — spread > 12¢ → blocked
  4. observe_only_spread_over_8c  — spread 9–12¢ → observe_only
  5. rally_active                 — runners on base within last 2 game states
  6. market_nearly_settled        — F5 market: inning≥5 bot 2-out; full: inning≥9 2-out
  7. duplicate_candidate_same_game — another watching/eligible candidate for this game
  8. max_one_trade_per_game       — manual_trade_journal has an entry for this game
"""
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GuardrailResult:
    status: str  # eligible | observe_only | blocked
    reasons: list[str] = field(default_factory=list)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0] > 0


def check_guardrails(
    *,
    is_semantics_clear: bool,
    yes_bid_cents: Optional[int],
    yes_ask_cents: Optional[int],
    market_type: str,
    inning: int,
    inning_half: str,
    outs: int,
    runner_state: Optional[str],
    game_id: str,
    candidate_event_id: Optional[int] = None,
    conn: sqlite3.Connection,
) -> GuardrailResult:
    reasons: list[str] = []
    blocked = False
    observe_only = False

    # 1. Semantics
    if not is_semantics_clear:
        reasons.append("market_semantics_unclear")
        blocked = True

    # 2. Bid/ask missing
    if yes_bid_cents is None or yes_ask_cents is None:
        reasons.append("bid_ask_missing")
        blocked = True

    # 3 & 4. Spread
    if yes_bid_cents is not None and yes_ask_cents is not None:
        spread = yes_ask_cents - yes_bid_cents
        if spread > 12:
            reasons.append(f"hard_block_spread_{spread}c")
            blocked = True
        elif spread > 8:
            reasons.append(f"observe_only_spread_{spread}c")
            observe_only = True

    # 5. Rally active (runners on base)
    rs = (runner_state or "").strip()
    if rs and rs not in ("none", "{}", "[]", ""):
        reasons.append("rally_possible_runners_on_base")
        observe_only = True

    # 6. Market nearly settled
    mtype = (market_type or "").lower()
    if mtype == "f5_total" and inning >= 5 and inning_half == "bottom" and outs >= 2:
        reasons.append("f5_market_nearly_settled")
        blocked = True
    elif mtype == "full_game_total" and inning >= 9 and outs >= 2:
        reasons.append("full_game_nearly_settled")
        blocked = True

    # 7. Duplicate candidate same game (exclude self if updating)
    q = "SELECT COUNT(*) FROM candidate_events WHERE game_id=? AND status IN ('watching','eligible')"
    params: list = [game_id]
    if candidate_event_id is not None:
        q += " AND id != ?"
        params.append(candidate_event_id)
    dup_count = conn.execute(q, params).fetchone()[0]
    if dup_count >= 1:
        reasons.append("duplicate_candidate_same_game")
        observe_only = True

    # 8. Max one trade per game
    if _table_exists(conn, "manual_trade_journal"):
        traded = conn.execute(
            "SELECT COUNT(*) FROM manual_trade_journal WHERE game_id=? AND outcome != 'void'",
            (game_id,),
        ).fetchone()[0]
        if traded >= 1:
            reasons.append("max_one_trade_per_game_reached")
            blocked = True

    if blocked:
        return GuardrailResult(status="blocked", reasons=reasons)
    if observe_only:
        return GuardrailResult(status="observe_only", reasons=reasons)
    return GuardrailResult(status="eligible", reasons=reasons)
```

### D2 — Tests: guardrails

Add to `tests/test_candidate_generator.py`:

```python
import pytest
from db.schema import init_db
from mlb.guardrails import check_guardrails, GuardrailResult


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


BASE = dict(
    is_semantics_clear=True,
    yes_bid_cents=42,
    yes_ask_cents=48,
    market_type="full_game_total",
    inning=3,
    inning_half="top",
    outs=1,
    runner_state="",
    game_id="NYY@BOS",
)


def test_eligible_baseline(conn):
    r = check_guardrails(**BASE, conn=conn)
    assert r.status == "eligible"
    assert r.reasons == []


def test_block_semantics_unclear(conn):
    r = check_guardrails(**{**BASE, "is_semantics_clear": False}, conn=conn)
    assert r.status == "blocked"
    assert "market_semantics_unclear" in r.reasons


def test_block_bid_missing(conn):
    r = check_guardrails(**{**BASE, "yes_bid_cents": None}, conn=conn)
    assert r.status == "blocked"
    assert "bid_ask_missing" in r.reasons


def test_hard_block_spread_13c(conn):
    r = check_guardrails(**{**BASE, "yes_bid_cents": 40, "yes_ask_cents": 53}, conn=conn)
    assert r.status == "blocked"
    assert any("hard_block_spread" in reason for reason in r.reasons)


def test_observe_only_spread_10c(conn):
    r = check_guardrails(**{**BASE, "yes_bid_cents": 40, "yes_ask_cents": 50}, conn=conn)
    assert r.status == "observe_only"
    assert any("observe_only_spread" in reason for reason in r.reasons)


def test_observe_only_runners_on_base(conn):
    r = check_guardrails(**{**BASE, "runner_state": "1B"}, conn=conn)
    assert r.status == "observe_only"
    assert "rally_possible_runners_on_base" in r.reasons


def test_block_f5_nearly_settled(conn):
    r = check_guardrails(**{**BASE, "market_type": "f5_total",
                             "inning": 5, "inning_half": "bottom", "outs": 2}, conn=conn)
    assert r.status == "blocked"
    assert "f5_market_nearly_settled" in r.reasons


def test_observe_only_duplicate_candidate(conn):
    conn.execute(
        """INSERT INTO candidate_events
           (created_at, updated_at, game_pk, game_id, away_abbr, home_abbr,
            candidate_type, trigger_reason, guardrail_status, guardrail_reasons_json, status)
           VALUES (datetime('now'),datetime('now'),1,'NYY@BOS','NYY','BOS',
                   'f5_total_overreaction_fade_watch','test','eligible','[]','watching')"""
    )
    conn.commit()
    r = check_guardrails(**BASE, conn=conn)
    assert r.status == "observe_only"
    assert "duplicate_candidate_same_game" in r.reasons
```

### D3 — Candidate generator module: `mlb/candidate_generator.py`

```python
"""
mlb/candidate_generator.py — Observation-only live candidate detection.

Three candidate types:
  full_game_total_extreme_reprice_watch
    Trigger: full_game_total mid price moved ≥15¢ from game_open_price_cents
    Inning:  1–6 only
    Side:    if price went UP → suggested_side=NO (under); if DOWN → YES (over)

  f5_total_overreaction_fade_watch
    Trigger: f5_total mid moved ≥12¢ from game_open_price AND inning ≤ 3
             AND current_total >= 5
    Side:    if f5 price went UP → suggested_side=NO (under)

  trailing_team_total_lag_watch
    Trigger: score_diff ≥ 3 AND inning 4–7 AND trailing team comeback_scoring_rating > 60
             AND trailing team's full_game_total price for their expected score seems low
    Side:    YES on full_game_total if trailing team is likely to score more
    Note:    Uses full_game_total as proxy (team_total may not exist)
"""
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from mlb.guardrails import check_guardrails, GuardrailResult

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat()


@dataclass
class CandidateSignal:
    candidate_type: str
    trigger_reason: str
    market_ticker: Optional[str]
    market_type: Optional[str]
    line_value: Optional[float]
    yes_bid_cents: Optional[int]
    yes_ask_cents: Optional[int]
    spread_cents: Optional[int]
    mid_cents: Optional[int]
    game_open_price_cents: Optional[int]
    price_move_cents: Optional[int]
    suggested_side: Optional[str]
    estimated_entry_cents: Optional[int]
    is_semantics_clear: bool


def _mid(bid: Optional[int], ask: Optional[int]) -> Optional[int]:
    if bid is None or ask is None:
        return None
    return (bid + ask) // 2


def _get_latest_game_state(game_pk: int, conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM mlb_game_states WHERE game_pk=? ORDER BY checked_at DESC LIMIT 1",
        (game_pk,),
    ).fetchone()


def _get_markets(game_id: str, market_type: str, conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM kalshi_markets WHERE game_id=? AND market_type=? AND status='open'",
        (game_id, market_type),
    ).fetchall()


def _get_team_context(team_abbr: str, season: str, conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM mlb_team_context WHERE team_abbr=? AND season=?",
        (team_abbr, season),
    ).fetchone()


def _upsert_candidate(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    away_abbr: str,
    home_abbr: str,
    game_state: sqlite3.Row,
    signal: CandidateSignal,
    guardrail: GuardrailResult,
    away_ctx: Optional[sqlite3.Row],
    home_ctx: Optional[sqlite3.Row],
) -> int:
    """Insert or update a candidate_events row. Returns row id."""
    inning = game_state["inning"] if game_state else None
    inning_half = game_state["inning_half"] if game_state else None
    away_score = game_state["away_score"] if game_state else None
    home_score = game_state["home_score"] if game_state else None
    current_total = (
        (away_score or 0) + (home_score or 0)
        if away_score is not None and home_score is not None
        else None
    )

    now = _now()
    season = str(datetime.now().year)

    # Check for existing candidate this game+type in watching/eligible
    existing = conn.execute(
        "SELECT id FROM candidate_events "
        "WHERE game_id=? AND candidate_type=? AND status IN ('watching','eligible','observe_only')"
        " ORDER BY created_at DESC LIMIT 1",
        (game_id, signal.candidate_type),
    ).fetchone()

    guardrail_status = guardrail.status
    # Map observe_only → watching with note
    if guardrail_status == "observe_only":
        db_status = "watching"
    elif guardrail_status == "blocked":
        db_status = "blocked"
    else:
        db_status = "eligible"

    row_data = dict(
        game_pk=game_pk,
        game_id=game_id,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
        candidate_type=signal.candidate_type,
        trigger_reason=signal.trigger_reason,
        inning=inning,
        inning_half=inning_half,
        away_score=away_score,
        home_score=home_score,
        current_total=current_total,
        market_ticker=signal.market_ticker,
        market_type=signal.market_type,
        line_value=signal.line_value,
        yes_bid_cents=signal.yes_bid_cents,
        yes_ask_cents=signal.yes_ask_cents,
        spread_cents=signal.spread_cents,
        mid_cents=signal.mid_cents,
        game_open_price_cents=signal.game_open_price_cents,
        price_move_cents=signal.price_move_cents,
        suggested_side=signal.suggested_side,
        estimated_entry_cents=signal.estimated_entry_cents,
        away_context_confidence=away_ctx["context_confidence"] if away_ctx else None,
        home_context_confidence=home_ctx["context_confidence"] if home_ctx else None,
        away_overall_score=away_ctx["overall_context_score"] if away_ctx else None,
        home_overall_score=home_ctx["overall_context_score"] if home_ctx else None,
        guardrail_status=guardrail_status,
        guardrail_reasons_json=json.dumps(guardrail.reasons),
        status=db_status,
        updated_at=now,
    )

    if existing:
        # Update price/guardrail fields on existing row
        conn.execute(
            """UPDATE candidate_events SET
               yes_bid_cents=:yes_bid_cents, yes_ask_cents=:yes_ask_cents,
               spread_cents=:spread_cents, mid_cents=:mid_cents,
               price_move_cents=:price_move_cents,
               guardrail_status=:guardrail_status,
               guardrail_reasons_json=:guardrail_reasons_json,
               status=:status, updated_at=:updated_at
               WHERE id=:id""",
            {**row_data, "id": existing["id"]},
        )
        return existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO candidate_events
               (created_at, updated_at, game_pk, game_id, away_abbr, home_abbr,
                candidate_type, trigger_reason, inning, inning_half, away_score, home_score,
                current_total, market_ticker, market_type, line_value,
                yes_bid_cents, yes_ask_cents, spread_cents, mid_cents,
                game_open_price_cents, price_move_cents, suggested_side, estimated_entry_cents,
                away_context_confidence, home_context_confidence,
                away_overall_score, home_overall_score,
                guardrail_status, guardrail_reasons_json, status)
               VALUES
               (:created_at, :updated_at, :game_pk, :game_id, :away_abbr, :home_abbr,
                :candidate_type, :trigger_reason, :inning, :inning_half, :away_score, :home_score,
                :current_total, :market_ticker, :market_type, :line_value,
                :yes_bid_cents, :yes_ask_cents, :spread_cents, :mid_cents,
                :game_open_price_cents, :price_move_cents, :suggested_side, :estimated_entry_cents,
                :away_context_confidence, :home_context_confidence,
                :away_overall_score, :home_overall_score,
                :guardrail_status, :guardrail_reasons_json, :status)""",
            {**row_data, "created_at": now},
        )
        return cur.lastrowid


# ── Candidate type 1: Full game total extreme reprice ─────────────────────

def _check_full_game_reprice(
    game_id: str,
    inning: int,
    conn: sqlite3.Connection,
) -> Optional[CandidateSignal]:
    if inning < 1 or inning > 6:
        return None
    markets = _get_markets(game_id, "full_game_total", conn)
    for mkt in markets:
        bid = mkt["yes_bid_cents"]
        ask = mkt["yes_ask_cents"]
        open_px = mkt["game_open_price_cents"]
        mid = _mid(bid, ask)
        if mid is None or open_px is None:
            continue
        move = mid - open_px
        if abs(move) < 15:
            continue
        suggested = "NO" if move > 0 else "YES"   # price up → under (NO); price down → over (YES)
        return CandidateSignal(
            candidate_type="full_game_total_extreme_reprice_watch",
            trigger_reason=f"full_game_total mid moved {move:+d}¢ from open ({open_px}¢ → {mid}¢), line={mkt['line_value']}",
            market_ticker=mkt["market_ticker"],
            market_type="full_game_total",
            line_value=mkt["line_value"],
            yes_bid_cents=bid,
            yes_ask_cents=ask,
            spread_cents=(ask - bid) if ask and bid else None,
            mid_cents=mid,
            game_open_price_cents=open_px,
            price_move_cents=move,
            suggested_side=suggested,
            estimated_entry_cents=ask if suggested == "YES" else (100 - bid),
            is_semantics_clear=bool(mkt["is_semantics_clear"]),
        )
    return None


# ── Candidate type 2: F5 total overreaction fade ──────────────────────────

def _check_f5_overreaction(
    game_id: str,
    inning: int,
    away_score: int,
    home_score: int,
    conn: sqlite3.Connection,
) -> Optional[CandidateSignal]:
    if inning < 1 or inning > 3:
        return None
    current_total = away_score + home_score
    if current_total < 5:
        return None
    markets = _get_markets(game_id, "f5_total", conn)
    for mkt in markets:
        bid = mkt["yes_bid_cents"]
        ask = mkt["yes_ask_cents"]
        open_px = mkt["game_open_price_cents"]
        mid = _mid(bid, ask)
        if mid is None or open_px is None:
            continue
        move = mid - open_px
        if move < 12:    # only trigger on upward repricing (over got more expensive)
            continue
        return CandidateSignal(
            candidate_type="f5_total_overreaction_fade_watch",
            trigger_reason=(
                f"F5 total mid up {move:+d}¢ after {current_total} runs in inning {inning}; "
                f"line={mkt['line_value']}, open={open_px}¢→{mid}¢"
            ),
            market_ticker=mkt["market_ticker"],
            market_type="f5_total",
            line_value=mkt["line_value"],
            yes_bid_cents=bid,
            yes_ask_cents=ask,
            spread_cents=(ask - bid) if ask and bid else None,
            mid_cents=mid,
            game_open_price_cents=open_px,
            price_move_cents=move,
            suggested_side="NO",   # fade the over: buy NO (under)
            estimated_entry_cents=(100 - bid),
            is_semantics_clear=bool(mkt["is_semantics_clear"]),
        )
    return None


# ── Candidate type 3: Trailing team total lag ─────────────────────────────

def _check_trailing_team_lag(
    game_id: str,
    away_abbr: str,
    home_abbr: str,
    inning: int,
    away_score: int,
    home_score: int,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[CandidateSignal]:
    if inning < 4 or inning > 7:
        return None
    diff = abs(away_score - home_score)
    if diff < 3:
        return None

    trailing_abbr = away_abbr if away_score < home_score else home_abbr
    leading_abbr  = home_abbr if trailing_abbr == away_abbr else away_abbr
    ctx = _get_team_context(trailing_abbr, season, conn)
    if ctx is None or (ctx["comeback_scoring_rating"] or 0) <= 60:
        return None

    # Use full_game_total as price proxy (team_total may not exist)
    markets = _get_markets(game_id, "full_game_total", conn)
    for mkt in markets:
        bid = mkt["yes_bid_cents"]
        ask = mkt["yes_ask_cents"]
        mid = _mid(bid, ask)
        if mid is None:
            continue
        # If trailing team has high comeback rating and full-game over is cheap → watch
        if mid > 45:   # market still pricing reasonable chance of comeback total
            continue
        return CandidateSignal(
            candidate_type="trailing_team_total_lag_watch",
            trigger_reason=(
                f"{trailing_abbr} trails {away_score}-{home_score} in inning {inning} "
                f"with comeback_rating={ctx['comeback_scoring_rating']:.0f}; "
                f"full_game_total mid={mid}¢ (line={mkt['line_value']})"
            ),
            market_ticker=mkt["market_ticker"],
            market_type="full_game_total",
            line_value=mkt["line_value"],
            yes_bid_cents=bid,
            yes_ask_cents=ask,
            spread_cents=(ask - bid) if ask and bid else None,
            mid_cents=mid,
            game_open_price_cents=mkt["game_open_price_cents"],
            price_move_cents=None,
            suggested_side="YES",   # over; trailing team may score to cover
            estimated_entry_cents=ask,
            is_semantics_clear=bool(mkt["is_semantics_clear"]),
        )
    return None


# ── Public entry point ────────────────────────────────────────────────────

def run_candidate_checks(
    game_pk: int,
    game_id: str,
    away_abbr: str,
    home_abbr: str,
    season: str,
    conn: sqlite3.Connection,
) -> list[int]:
    """
    Run all 3 candidate type checks for one live game.
    Writes/updates candidate_events rows.
    Returns list of candidate_event IDs written.
    """
    game_state = _get_latest_game_state(game_pk, conn)
    if game_state is None:
        return []

    inning      = game_state["inning"] or 0
    inning_half = game_state["inning_half"] or "top"
    outs        = game_state["outs"] or 0
    away_score  = game_state["away_score"] or 0
    home_score  = game_state["home_score"] or 0
    runner_state = game_state["runner_state"] or ""

    away_ctx = _get_team_context(away_abbr, season, conn)
    home_ctx = _get_team_context(home_abbr, season, conn)

    checks = [
        _check_full_game_reprice(game_id, inning, conn),
        _check_f5_overreaction(game_id, inning, away_score, home_score, conn),
        _check_trailing_team_lag(game_id, away_abbr, home_abbr, inning,
                                  away_score, home_score, season, conn),
    ]

    written_ids = []
    for signal in checks:
        if signal is None:
            continue
        guardrail = check_guardrails(
            is_semantics_clear=signal.is_semantics_clear,
            yes_bid_cents=signal.yes_bid_cents,
            yes_ask_cents=signal.yes_ask_cents,
            market_type=signal.market_type or "",
            inning=inning,
            inning_half=inning_half,
            outs=outs,
            runner_state=runner_state,
            game_id=game_id,
            conn=conn,
        )
        row_id = _upsert_candidate(
            conn, game_pk, game_id, away_abbr, home_abbr,
            game_state, signal, guardrail, away_ctx, home_ctx,
        )
        written_ids.append(row_id)
        log.info(
            "candidate %s game=%s type=%s guardrail=%s",
            row_id, game_id, signal.candidate_type, guardrail.status,
        )

    conn.commit()
    return written_ids
```

### D4 — Tests: candidate generator

Add to `tests/test_candidate_generator.py`:

```python
import pytest
from db.schema import init_db
from mlb.candidate_generator import run_candidate_checks
from datetime import datetime


def _now():
    return datetime.now().isoformat()


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _insert_game_state(conn, game_pk, inning=2, inning_half="top", outs=0,
                        away_score=5, home_score=1, runner_state=""):
    conn.execute(
        """INSERT INTO mlb_game_states
           (game_pk, checked_at, status, inning, inning_half, outs,
            away_score, home_score, balls, strikes, runner_state)
           VALUES (?,datetime('now'),'Live',?,?,?,?,?,0,0,?)""",
        (game_pk, inning, inning_half, outs, away_score, home_score, runner_state),
    )
    conn.commit()


def _insert_market(conn, game_id, market_type, line=8.5, bid=55, ask=62,
                    open_px=42, ticker=None, semantics_clear=1, yes_means="over"):
    ticker = ticker or f"KXMLB-{game_id}-{market_type}-{line}"
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, game_id, line_value,
            yes_bid_cents, yes_ask_cents, status,
            game_open_price_cents, is_semantics_clear, yes_means,
            raw_json, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,'open',?,?,?,'{}',datetime('now'),datetime('now'))""",
        (ticker, "EVT", market_type, game_id, line, bid, ask,
         open_px, semantics_clear, yes_means),
    )
    conn.commit()
    return ticker


def test_no_candidates_without_game_state(conn):
    ids = run_candidate_checks(1, "NYY@BOS", "NYY", "BOS", "2026", conn)
    assert ids == []


def test_full_game_reprice_triggers(conn):
    _insert_game_state(conn, game_pk=1, inning=3, away_score=4, home_score=2)
    _insert_market(conn, "NYY@BOS", "full_game_total", bid=65, ask=72, open_px=48)
    # mid=68, open=48, move=+20 ≥ 15 → triggers
    ids = run_candidate_checks(1, "NYY@BOS", "NYY", "BOS", "2026", conn)
    assert len(ids) >= 1
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (ids[0],)).fetchone()
    assert row["candidate_type"] == "full_game_total_extreme_reprice_watch"
    assert row["suggested_side"] == "NO"   # price went up → fade = NO (under)


def test_full_game_reprice_no_trigger_small_move(conn):
    _insert_game_state(conn, game_pk=2, inning=3, away_score=2, home_score=1)
    _insert_market(conn, "ATL@PHI", "full_game_total", bid=52, ask=58, open_px=48)
    # mid=55, open=48, move=+7 < 15 → no trigger
    ids = run_candidate_checks(2, "ATL@PHI", "ATL", "PHI", "2026", conn)
    assert all(
        conn.execute("SELECT candidate_type FROM candidate_events WHERE id=?", (i,)).fetchone()["candidate_type"]
        != "full_game_total_extreme_reprice_watch"
        for i in ids
    )


def test_f5_overreaction_triggers(conn):
    _insert_game_state(conn, game_pk=3, inning=2, away_score=4, home_score=3)
    # current_total=7 ≥ 5; f5 mid moved up ≥12¢
    _insert_market(conn, "HOU@TEX", "f5_total", line=4.5, bid=68, ask=76, open_px=52)
    ids = run_candidate_checks(3, "HOU@TEX", "HOU", "TEX", "2026", conn)
    f5_ids = [i for i in ids if conn.execute(
        "SELECT candidate_type FROM candidate_events WHERE id=?", (i,)
    ).fetchone()["candidate_type"] == "f5_total_overreaction_fade_watch"]
    assert len(f5_ids) == 1


def test_f5_overreaction_no_trigger_early_low_total(conn):
    _insert_game_state(conn, game_pk=4, inning=2, away_score=2, home_score=1)
    # total=3 < 5 → no trigger
    _insert_market(conn, "MIL@CHC", "f5_total", line=4.5, bid=68, ask=76, open_px=52)
    ids = run_candidate_checks(4, "MIL@CHC", "MIL", "CHC", "2026", conn)
    f5_ids = [i for i in ids if conn.execute(
        "SELECT candidate_type FROM candidate_events WHERE id=?", (i,)
    ).fetchone()["candidate_type"] == "f5_total_overreaction_fade_watch"]
    assert f5_ids == []


def test_candidate_blocked_semantics_unclear(conn):
    _insert_game_state(conn, game_pk=5, inning=3, away_score=4, home_score=2)
    _insert_market(conn, "LAD@SF", "full_game_total", bid=65, ask=72, open_px=48,
                    semantics_clear=0, yes_means="unknown")
    ids = run_candidate_checks(5, "LAD@SF", "LAD", "SF", "2026", conn)
    if ids:
        row = conn.execute(
            "SELECT guardrail_status FROM candidate_events WHERE id=?", (ids[0],)
        ).fetchone()
        assert row["guardrail_status"] == "blocked"


def test_upsert_updates_existing_candidate(conn):
    _insert_game_state(conn, game_pk=6, inning=3, away_score=4, home_score=2)
    _insert_market(conn, "COL@ARI", "full_game_total", bid=65, ask=72, open_px=48)
    ids1 = run_candidate_checks(6, "COL@ARI", "COL", "ARI", "2026", conn)
    ids2 = run_candidate_checks(6, "COL@ARI", "COL", "ARI", "2026", conn)
    # Should update existing, not duplicate
    total = conn.execute(
        "SELECT COUNT(*) FROM candidate_events WHERE game_id='COL@ARI'"
    ).fetchone()[0]
    assert total == len(set(ids1 + ids2))   # no new rows
```

### D5 — Live watcher polling loop: `live_watcher.py`

```python
"""
live_watcher.py — Polling loop: check live MLB games, run candidate detection.

Usage:
  python live_watcher.py                  # polls every 30 seconds
  python live_watcher.py --interval 60    # custom interval in seconds
  python live_watcher.py --dry-run        # log candidates, skip DB writes

Reads:
  mlb_games (status='Live' or 'In Progress')
  mlb_game_states (latest snapshot per game)
  kalshi_markets (by game_id, status=open)
  mlb_team_context

Writes:
  candidate_events (via run_candidate_checks)
  kalshi_markets.game_open_price_cents (set once when game first goes live)

Does NOT:
  Place orders, modify paper_positions, call Kalshi trade endpoints
"""
import argparse
import logging
import os
import time
from datetime import datetime

from db.schema import init_db
from mlb.candidate_generator import run_candidate_checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("live_watcher")

_LIVE_STATUSES = ("Live", "In Progress", "Game In Progress")


def _set_open_prices(conn) -> None:
    """
    For any kalshi_market where game_open_price_cents is NULL and the game is live,
    set it from the current mid price.  Called once per poll cycle.
    """
    live_game_ids = [
        r["game_id"] for r in conn.execute(
            "SELECT game_id FROM mlb_games WHERE status IN ({}) AND game_id IS NOT NULL".format(
                ",".join("?" * len(_LIVE_STATUSES))
            ),
            list(_LIVE_STATUSES),
        ).fetchall()
    ]
    if not live_game_ids:
        return
    placeholders = ",".join("?" * len(live_game_ids))
    markets = conn.execute(
        f"SELECT id, yes_bid_cents, yes_ask_cents FROM kalshi_markets "
        f"WHERE game_id IN ({placeholders}) AND game_open_price_cents IS NULL "
        f"AND yes_bid_cents IS NOT NULL AND yes_ask_cents IS NOT NULL",
        live_game_ids,
    ).fetchall()
    for mkt in markets:
        mid = (mkt["yes_bid_cents"] + mkt["yes_ask_cents"]) // 2
        conn.execute(
            "UPDATE kalshi_markets SET game_open_price_cents=? WHERE id=?",
            (mid, mkt["id"]),
        )
    if markets:
        conn.commit()
        log.info("set game_open_price_cents for %d markets", len(markets))


def _poll_once(conn, dry_run: bool = False) -> dict:
    season = str(datetime.now().year)
    _set_open_prices(conn)

    live_games = conn.execute(
        "SELECT game_pk, game_id, away_abbr, home_abbr FROM mlb_games "
        "WHERE status IN ({})".format(",".join("?" * len(_LIVE_STATUSES))),
        list(_LIVE_STATUSES),
    ).fetchall()

    total_candidates = 0
    for game in live_games:
        if dry_run:
            log.info("dry-run: would check game_pk=%s game_id=%s",
                      game["game_pk"], game["game_id"])
            continue
        ids = run_candidate_checks(
            game_pk=game["game_pk"],
            game_id=game["game_id"],
            away_abbr=game["away_abbr"],
            home_abbr=game["home_abbr"],
            season=season,
            conn=conn,
        )
        total_candidates += len(ids)

    return {"live_games": len(live_games), "candidates_written": total_candidates}


def main():
    parser = argparse.ArgumentParser(description="Live candidate watcher")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval seconds")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = os.environ.get("DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)
    log.info("live_watcher started: interval=%ds dry_run=%s", args.interval, args.dry_run)

    try:
        while True:
            try:
                result = _poll_once(conn, dry_run=args.dry_run)
                log.info("poll: live_games=%d candidates=%d",
                          result["live_games"], result["candidates_written"])
            except Exception as exc:
                log.error("poll error: %s", exc)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("live_watcher stopped")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

Run: `python -m pytest tests/test_candidate_generator.py -x` → all pass.

### Commit D
```
git add mlb/guardrails.py mlb/candidate_generator.py live_watcher.py tests/test_candidate_generator.py
git commit -m "feat: live candidate generator + guardrails (Task D)"
```

---

## Task E — Candidate Review Dashboard (P6)

**Files:** `frontend/src/pages/CandidateReview.tsx` (new), `frontend/src/types/api.ts`, `frontend/src/api/client.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`

### E1 — TS type: add to `frontend/src/types/api.ts`

```typescript
export interface CandidateEvent {
  id: number
  created_at: string
  updated_at: string
  game_pk: number
  game_id: string
  away_abbr: string
  home_abbr: string
  candidate_type: string
  candidate_type_label: string
  trigger_reason: string
  inning: number | null
  inning_half: string | null
  away_score: number | null
  home_score: number | null
  current_total: number | null
  market_ticker: string | null
  market_type: string | null
  line_value: number | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  spread_cents: number | null
  mid_cents: number | null
  game_open_price_cents: number | null
  price_move_cents: number | null
  suggested_side: string | null
  estimated_entry_cents: number | null
  away_context_confidence: string | null
  home_context_confidence: string | null
  away_overall_score: number | null
  home_overall_score: number | null
  guardrail_status: string
  guardrail_reasons: string[]
  status: string
  notes: string | null
}
```

### E2 — API client: add to `frontend/src/api/client.ts`

Find the existing `api` object and add:
```typescript
  liveCandidates: (params?: { status?: string; guardrail_status?: string; limit?: number }) =>
    fetch(`/api/candidates/live?${new URLSearchParams(
      Object.entries(params ?? {}).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])
    )}`).then(r => r.json()) as Promise<{ total: number; items: CandidateEvent[] }>,
```

### E3 — New page: `frontend/src/pages/CandidateReview.tsx`

```tsx
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { CandidateEvent } from '../types/api'

const GUARDRAIL_COLORS: Record<string, string> = {
  eligible:     'bg-green-900/40 text-green-400 border-green-800/50',
  observe_only: 'bg-yellow-900/40 text-yellow-400 border-yellow-800/50',
  blocked:      'bg-red-900/40 text-red-400 border-red-800/50',
  pending:      'bg-slate-800 text-slate-500 border-slate-700',
}

function GuardrailBadge({ status }: { status: string }) {
  const cls = GUARDRAIL_COLORS[status] ?? GUARDRAIL_COLORS.pending
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${cls}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

function Cents({ v }: { v: number | null }) {
  if (v === null) return <span className="text-slate-600">—</span>
  return <span className="text-slate-300">{v}¢</span>
}

function MoveCell({ v }: { v: number | null }) {
  if (v === null) return <span className="text-slate-600">—</span>
  const color = v > 0 ? 'text-red-400' : 'text-green-400'
  return <span className={color}>{v > 0 ? '+' : ''}{v}¢</span>
}

export function CandidateReview() {
  const qc = useQueryClient()
  const [guardrailFilter, setGuardrailFilter] = useState<string>('')
  const [expanded, setExpanded] = useState<number | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['live-candidates', guardrailFilter],
    queryFn: () => api.liveCandidates({ guardrail_status: guardrailFilter || undefined, limit: 100 }),
    refetchInterval: 30_000,
  })

  const items: CandidateEvent[] = data?.items ?? []

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Live Candidates</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Observation only · refreshes every 30s · no orders placed
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <select
            className="text-sm bg-slate-800 border border-slate-700 text-slate-300 rounded px-2 py-1"
            value={guardrailFilter}
            onChange={e => setGuardrailFilter(e.target.value)}
          >
            <option value="">All guardrail states</option>
            <option value="eligible">Eligible</option>
            <option value="observe_only">Observe Only</option>
            <option value="blocked">Blocked</option>
          </select>
          <button
            className="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-md transition-colors"
            onClick={() => qc.invalidateQueries({ queryKey: ['live-candidates'] })}
          >
            Refresh
          </button>
        </div>
      </div>

      {isLoading && <p className="text-slate-500 text-sm">Loading…</p>}

      {!isLoading && items.length === 0 && (
        <p className="text-slate-500 text-center mt-10 text-sm">
          No candidates yet. Start <code className="text-slate-400">python live_watcher.py</code> while games are live.
        </p>
      )}

      <div className="space-y-2">
        {items.map(c => (
          <div
            key={c.id}
            className="border border-slate-800 rounded-lg bg-slate-900/50 overflow-hidden"
          >
            {/* Summary row */}
            <button
              className="w-full text-left px-4 py-3 flex items-center gap-4 hover:bg-slate-800/30 transition-colors"
              onClick={() => setExpanded(expanded === c.id ? null : c.id)}
            >
              <div className="w-28 shrink-0">
                <span className="font-medium text-slate-100 text-sm">{c.game_id}</span>
                {c.inning && (
                  <span className="ml-2 text-[11px] text-slate-500">
                    {c.inning_half === 'top' ? '▲' : '▼'}{c.inning}
                  </span>
                )}
              </div>

              <div className="w-44 shrink-0">
                <span className="text-xs text-slate-400">{c.candidate_type_label}</span>
              </div>

              <div className="flex-1 text-xs text-slate-500 truncate">{c.trigger_reason}</div>

              <div className="flex items-center gap-3 shrink-0">
                {c.line_value !== null && (
                  <span className="text-xs text-slate-400">Line {c.line_value}</span>
                )}
                {c.yes_bid_cents !== null && c.yes_ask_cents !== null && (
                  <span className="text-xs text-slate-400">
                    {c.yes_bid_cents}¢ / {c.yes_ask_cents}¢
                  </span>
                )}
                {c.spread_cents !== null && (
                  <span className={`text-xs ${c.spread_cents > 12 ? 'text-red-400' : c.spread_cents > 8 ? 'text-yellow-400' : 'text-slate-400'}`}>
                    spread {c.spread_cents}¢
                  </span>
                )}
                <GuardrailBadge status={c.guardrail_status} />
                {c.suggested_side && (
                  <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${c.suggested_side === 'YES' ? 'bg-blue-900/50 text-blue-300' : 'bg-orange-900/50 text-orange-300'}`}>
                    {c.suggested_side}
                  </span>
                )}
              </div>
            </button>

            {/* Detail panel */}
            {expanded === c.id && (
              <div className="border-t border-slate-800 px-4 py-3 grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-[11px] text-slate-500 uppercase tracking-wider mb-2">Market</p>
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-slate-500">Ticker</span>
                      <span className="text-slate-300 font-mono">{c.market_ticker ?? '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Type</span>
                      <span className="text-slate-300">{c.market_type ?? '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Bid / Ask</span>
                      <span className="text-slate-300">
                        <Cents v={c.yes_bid_cents} /> / <Cents v={c.yes_ask_cents} />
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Open price</span>
                      <Cents v={c.game_open_price_cents} />
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Price move</span>
                      <MoveCell v={c.price_move_cents} />
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">Est. entry</span>
                      <Cents v={c.estimated_entry_cents} />
                    </div>
                  </div>
                </div>

                <div>
                  <p className="text-[11px] text-slate-500 uppercase tracking-wider mb-2">Guardrails</p>
                  {c.guardrail_reasons.length === 0 ? (
                    <p className="text-xs text-green-400">No blockers</p>
                  ) : (
                    <ul className="space-y-1">
                      {c.guardrail_reasons.map(r => (
                        <li key={r} className="text-xs text-yellow-400 flex items-start gap-1">
                          <span className="mt-0.5">⚠</span> {r.replace(/_/g, ' ')}
                        </li>
                      ))}
                    </ul>
                  )}

                  <p className="text-[11px] text-slate-500 uppercase tracking-wider mb-2 mt-3">Team Context</p>
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-slate-500">{c.away_abbr} overall</span>
                      <span className="text-slate-300">{c.away_overall_score?.toFixed(0) ?? '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">{c.home_abbr} overall</span>
                      <span className="text-slate-300">{c.home_overall_score?.toFixed(0) ?? '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">{c.away_abbr} conf</span>
                      <span className="text-slate-300">{c.away_context_confidence ?? '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">{c.home_abbr} conf</span>
                      <span className="text-slate-300">{c.home_context_confidence ?? '—'}</span>
                    </div>
                  </div>
                </div>

                <div className="col-span-2">
                  <p className="text-[11px] text-slate-500 uppercase tracking-wider mb-1">Trigger Reason</p>
                  <p className="text-xs text-slate-300 break-words">{c.trigger_reason}</p>
                </div>

                <div className="col-span-2 flex justify-end">
                  <span className="text-[11px] text-slate-600">
                    Detected {new Date(c.created_at).toLocaleTimeString()} ·
                    Updated {new Date(c.updated_at).toLocaleTimeString()}
                  </span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
```

### E4 — Wire into router

In `frontend/src/App.tsx`, add the import and route:
```tsx
import { CandidateReview } from './pages/CandidateReview'
// inside <Routes>:
<Route path="/candidates/live" element={<CandidateReview />} />
```

In `frontend/src/components/Layout.tsx`, add nav link:
```tsx
{ path: '/candidates/live', label: 'Live Candidates' }
```

### Commit E
```
git add frontend/src/pages/CandidateReview.tsx frontend/src/types/api.ts \
        frontend/src/api/client.ts frontend/src/App.tsx frontend/src/components/Layout.tsx
git commit -m "feat: CandidateReview dashboard page (Task E)"
```

---

## Task F — Manual Trade Journal (P7)

**Files:** `db/schema.py`, `api/schemas.py`, `api/routers/journal.py` (new), `api/main.py`, `frontend/src/types/api.ts`, `frontend/src/pages/CandidateReview.tsx`, `tests/test_journal.py` (new)

### F1 — DB: add `manual_trade_journal` table

Append to `DDL` in `db/schema.py`:

```sql
-- ── Manual real-trade journal ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS manual_trade_journal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    candidate_event_id  INTEGER REFERENCES candidate_events(id),
    game_id             TEXT NOT NULL,
    market_ticker       TEXT NOT NULL,
    side                TEXT NOT NULL,        -- YES | NO
    filled_price_cents  INTEGER NOT NULL,
    units               INTEGER NOT NULL DEFAULT 1,
    notes               TEXT,
    outcome             TEXT NOT NULL DEFAULT 'pending',  -- pending | win | loss | void
    actual_pnl_cents    INTEGER,
    settled_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_journal_game_id ON manual_trade_journal(game_id);
```

### F2 — Pydantic schemas

Add to `api/schemas.py`:

```python
# ---------------------------------------------------------------------------
# Manual trade journal
# ---------------------------------------------------------------------------

class JournalEntryIn(BaseModel):
    candidate_event_id: Optional[int] = None
    game_id: str
    market_ticker: str
    side: str                   # YES | NO
    filled_price_cents: int
    units: int = 1
    notes: Optional[str] = None


class JournalEntryUpdate(BaseModel):
    outcome: Optional[str] = None       # pending | win | loss | void
    actual_pnl_cents: Optional[int] = None
    settled_at: Optional[str] = None
    notes: Optional[str] = None


class JournalEntryOut(BaseModel):
    id: int
    created_at: str
    updated_at: str
    candidate_event_id: Optional[int]
    game_id: str
    market_ticker: str
    side: str
    filled_price_cents: int
    units: int
    notes: Optional[str]
    outcome: str
    actual_pnl_cents: Optional[int]
    settled_at: Optional[str]
```

### F3 — API router: `api/routers/journal.py`

```python
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import JournalEntryIn, JournalEntryOut, JournalEntryUpdate, ListResponse

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _now() -> str:
    return datetime.now().isoformat()


@router.post("", response_model=JournalEntryOut, status_code=201)
def create_journal_entry(
    body: JournalEntryIn,
    conn: sqlite3.Connection = Depends(get_db),
):
    now = _now()
    cur = conn.execute(
        """INSERT INTO manual_trade_journal
           (created_at, updated_at, candidate_event_id, game_id, market_ticker,
            side, filled_price_cents, units, notes, outcome)
           VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
        (now, now, body.candidate_event_id, body.game_id, body.market_ticker,
         body.side, body.filled_price_cents, body.units, body.notes),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM manual_trade_journal WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    # Update linked candidate_event status to 'logged'
    if body.candidate_event_id:
        conn.execute(
            "UPDATE candidate_events SET status='logged', updated_at=? WHERE id=?",
            (now, body.candidate_event_id),
        )
        conn.commit()
    return dict(row)


@router.get("", response_model=ListResponse[JournalEntryOut])
def list_journal_entries(
    game_id: Optional[str] = None,
    outcome: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    filters = []
    params: list = []
    if game_id:
        filters.append("game_id = ?"); params.append(game_id)
    if outcome:
        filters.append("outcome = ?"); params.append(outcome)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM manual_trade_journal {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM manual_trade_journal {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return ListResponse(total=total, items=[dict(r) for r in rows])


@router.patch("/{entry_id}", response_model=JournalEntryOut)
def update_journal_entry(
    entry_id: int,
    body: JournalEntryUpdate,
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(
        "SELECT * FROM manual_trade_journal WHERE id=?", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    updates: dict = {"updated_at": _now()}
    if body.outcome is not None:
        updates["outcome"] = body.outcome
    if body.actual_pnl_cents is not None:
        updates["actual_pnl_cents"] = body.actual_pnl_cents
    if body.settled_at is not None:
        updates["settled_at"] = body.settled_at
    if body.notes is not None:
        updates["notes"] = body.notes

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE manual_trade_journal SET {set_clause} WHERE id=?",
        list(updates.values()) + [entry_id],
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM manual_trade_journal WHERE id=?", (entry_id,)
    ).fetchone())
```

Register in `api/main.py`:
```python
from api.routers import journal
app.include_router(journal.router)
```

### F4 — Tests: `tests/test_journal.py`

```python
from fastapi.testclient import TestClient
from api.main import app
import pytest

client = TestClient(app)


def test_create_journal_entry():
    r = client.post("/api/journal", json={
        "game_id": "NYY@BOS",
        "market_ticker": "KXMLB-TEST",
        "side": "YES",
        "filled_price_cents": 48,
        "units": 1,
        "notes": "Manual test entry",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["outcome"] == "pending"
    assert data["game_id"] == "NYY@BOS"


def test_list_journal_entries():
    r = client.get("/api/journal")
    assert r.status_code == 200
    assert "total" in r.json()
    assert "items" in r.json()


def test_update_journal_outcome():
    create = client.post("/api/journal", json={
        "game_id": "ATL@PHI",
        "market_ticker": "KXMLB-TEST2",
        "side": "NO",
        "filled_price_cents": 52,
    })
    eid = create.json()["id"]
    r = client.patch(f"/api/journal/{eid}", json={
        "outcome": "win",
        "actual_pnl_cents": 48,
        "settled_at": "2026-06-12T20:00:00",
    })
    assert r.status_code == 200
    assert r.json()["outcome"] == "win"
    assert r.json()["actual_pnl_cents"] == 48


def test_update_missing_entry_404():
    r = client.patch("/api/journal/999999", json={"outcome": "win"})
    assert r.status_code == 404
```

### F5 — "Log Trade" button in CandidateReview

In `CandidateReview.tsx`, inside the expanded detail panel (col-span-2 section), add:

```tsx
{/* Log Trade button — only show for eligible/observe_only candidates */}
{(c.guardrail_status === 'eligible' || c.guardrail_status === 'observe_only') && (
  <LogTradeButton candidate={c} onLogged={() => qc.invalidateQueries({ queryKey: ['live-candidates'] })} />
)}
```

Add `LogTradeButton` component at top of file:

```tsx
function LogTradeButton({ candidate, onLogged }: { candidate: CandidateEvent; onLogged: () => void }) {
  const [open, setOpen] = useState(false)
  const [price, setPrice] = useState(String(candidate.estimated_entry_cents ?? ''))
  const [side, setSide] = useState(candidate.suggested_side ?? 'YES')
  const [units, setUnits] = useState('1')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    await fetch('/api/journal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_event_id: candidate.id,
        game_id: candidate.game_id,
        market_ticker: candidate.market_ticker ?? '',
        side,
        filled_price_cents: parseInt(price),
        units: parseInt(units),
        notes: notes || null,
      }),
    })
    setSaving(false)
    setOpen(false)
    onLogged()
  }

  if (!open) {
    return (
      <button
        className="text-xs px-2 py-1 bg-blue-800/50 text-blue-300 border border-blue-700/50 rounded hover:bg-blue-700/50 transition-colors"
        onClick={() => setOpen(true)}
      >
        Log Trade
      </button>
    )
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select
        className="text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded px-2 py-1"
        value={side} onChange={e => setSide(e.target.value)}
      >
        <option>YES</option>
        <option>NO</option>
      </select>
      <input
        className="text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded px-2 py-1 w-16"
        placeholder="price ¢" value={price} onChange={e => setPrice(e.target.value)}
      />
      <input
        className="text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded px-2 py-1 w-12"
        placeholder="units" value={units} onChange={e => setUnits(e.target.value)}
      />
      <input
        className="text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded px-2 py-1 flex-1 min-w-24"
        placeholder="notes (optional)" value={notes} onChange={e => setNotes(e.target.value)}
      />
      <button
        className="text-xs px-2 py-1 bg-green-800/50 text-green-300 border border-green-700/50 rounded disabled:opacity-50"
        onClick={submit} disabled={saving || !price}
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button
        className="text-xs text-slate-500 hover:text-slate-300"
        onClick={() => setOpen(false)}
      >
        Cancel
      </button>
    </div>
  )
}
```

Run: `python -m pytest tests/test_journal.py -x` → all pass.

### Commit F
```
git add db/schema.py api/schemas.py api/routers/journal.py api/main.py \
        frontend/src/types/api.ts frontend/src/pages/CandidateReview.tsx \
        tests/test_journal.py
git commit -m "feat: manual trade journal + Log Trade button in CandidateReview (Task F)"
```

---

## Task G — Final Integration + Full Test Run (P8 / Definition of Done)

**Files:** No new files. Integration smoke test + final run.

### G1 — Wire semantics refresh into kalshi_discover.py

After `kalshi_discover.py` upserts markets (inside the main discovery flow, after the bulk upsert), call:
```python
from kalshi.semantics import refresh_market_semantics
refresh_market_semantics(conn)
```

### G2 — Wire semantics refresh into kalshi/normalizer.py

After upserting a market row in the WS normalizer, call `parse_yes_means` for that one row and update `yes_means`/`is_semantics_clear` in place (not a full refresh — just the one updated market):

```python
from kalshi.semantics import parse_yes_means
yes_means, clear = parse_yes_means(
    mkt["market_type"], mkt["title"], mkt["subtitle"],
    mkt["rules_primary"], mkt["away_team"], mkt["home_team"],
)
conn.execute(
    "UPDATE kalshi_markets SET yes_means=?, is_semantics_clear=? WHERE market_ticker=?",
    (yes_means, int(clear), ticker),
)
```

### G3 — Full test suite

```
python -m pytest tests/ --ignore=test_results.txt -q
```

Expected: ≥ 560 passed (536 existing + ~10 semantics + ~8 reconciler + ~4 candidate_events + ~9 guardrails + ~7 candidate_generator + ~4 journal), 0 failures.

### G4 — Smoke test (manual, while a game is live)

```bash
# Terminal 1: WebSocket price collector
python kalshi_ws.py --sport mlb

# Terminal 2: MLB game detail poller (run once per game to populate game states)
python -c "
from db.schema import init_db
from mlb.game_store import fetch_and_store_game
import os
conn = init_db(os.environ.get('DB_PATH','kalshi_mlb.db'))
# replace with a live game_pk
fetch_and_store_game(777182, conn=conn)
conn.close()
"

# Terminal 3: Live watcher
python live_watcher.py --interval 30

# Terminal 4: API + frontend
uvicorn api.main:app --reload &
cd frontend && npm run dev

# Then open http://localhost:5173/candidates/live
# Verify: candidates appear, guardrail badges show, detail panel opens, Log Trade works
```

### Commit G
```
git add kalshi_discover.py kalshi/normalizer.py
git commit -m "feat: wire semantics refresh into discovery + WS normalizer (Task G)"
```

---

## Definition of Done Checklist

- [ ] **A** Kalshi markets have `yes_means` + `is_semantics_clear` parsed from rules_primary/title
- [ ] **B** F5 total settlement uses `mlb_inning_scores` (innings 1–5 sum), not `mlb_games.final_total`
- [ ] **C** `candidate_events` table exists; GET `/api/candidates/live` returns JSON
- [ ] **D** `live_watcher.py` runs, detects all 3 candidate types, applies 8 guardrails, writes to DB
- [ ] **E** `/candidates/live` React page shows cards with guardrail badge, trigger reason, bid/ask, team context
- [ ] **F** POST `/api/journal` persists a trade; PATCH updates outcome; "Log Trade" button works in UI
- [ ] **G** Full test suite ≥ 560 passed, 0 failures; smoke test shows live candidates during a real game
- [ ] No order placement code exists anywhere in the codebase
- [ ] `live_watcher.py` never calls Kalshi trade endpoints (read-only)
