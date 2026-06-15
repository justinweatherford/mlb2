## Goal
Add L1/L5/L10 rolling scoring-form metrics to team context with null-score game exclusion, without touching candidate generation or existing formulas.

## Architecture
- `db/schema.py` — DDL + 6 ALTER TABLE migrations for new columns
- `mlb/team_context.py` — null guard in SQL, compute L1/5/10, update upsert + debug output
- `frontend/src/types/api.ts` — extend TeamContext interface with 6 new fields
- `frontend/src/pages/MLBTeamContext.tsx` — rolling form compact row in TeamDebugPanel
- `tests/test_rolling_form.py` — dedicated test file

## Tech Stack
SQLite (ALTER TABLE migrations), Python dataclasses, React/TypeScript, TanStack Query

---

## Step 1 — DB schema: add 6 new columns

**File:** `db/schema.py`

In `DDL`, add to `mlb_team_context` table (after `recent_runs_allowed_per_game_7`):
```sql
-- Rolling form windows (L1/L5/L10)
l1_rpg                          REAL,
l5_rpg                          REAL,
l10_rpg                         REAL,
l1_scoring_form_rating          REAL,
l5_scoring_form_rating          REAL,
l10_scoring_form_rating         REAL,
```

In `_migrations`, append 6 ALTER TABLE statements:
```python
"ALTER TABLE mlb_team_context ADD COLUMN l1_rpg REAL",
"ALTER TABLE mlb_team_context ADD COLUMN l5_rpg REAL",
"ALTER TABLE mlb_team_context ADD COLUMN l10_rpg REAL",
"ALTER TABLE mlb_team_context ADD COLUMN l1_scoring_form_rating REAL",
"ALTER TABLE mlb_team_context ADD COLUMN l5_scoring_form_rating REAL",
"ALTER TABLE mlb_team_context ADD COLUMN l10_scoring_form_rating REAL",
```

TDD: test `init_db(":memory:")` — SELECT l1_rpg FROM mlb_team_context → no error.

---

## Step 2 — Backend: null guard + L1/5/10 computation

**File:** `mlb/team_context.py`

**2a.** Add `_rate_scoring_form(rpg)` helper (pure formula, no blend):
```python
def _rate_scoring_form(rpg: Optional[float]) -> Optional[float]:
    if rpg is None:
        return None
    return round(_clamp(50.0 + (rpg - _LEAGUE_AVG_RPG) * _SCALE_RPG), 1)
```

**2b.** In `compute_team_context()`, add null-score guard to both SQL queries:
```sql
AND final_away_score IS NOT NULL AND final_home_score IS NOT NULL AND final_total IS NOT NULL
```

**2c.** After `all_games` list is built, compute rolling windows:
```python
l1_rpg  = _avg([g["scored"] for g, _ in all_games[-1:]])
l5_rpg  = _avg([g["scored"] for g, _ in all_games[-5:]])
l10_rpg = _avg([g["scored"] for g, _ in all_games[-10:]])
l1_rating  = _rate_scoring_form(l1_rpg)
l5_rating  = _rate_scoring_form(l5_rpg)
l10_rating = _rate_scoring_form(l10_rpg)
```

**2d.** Add 6 fields to the returned dict.

**2e.** Update `_upsert_team_context()` to write the 6 new fields (INSERT values + ON CONFLICT SET).

TDD: test null-score game excluded, L1/5/10 computed correctly, existing offense_rating unchanged.

---

## Step 3 — Backend: debug output includes rolling RPG

**File:** `mlb/team_context.py` — `compute_team_context_debug()`

In the offense `_rating_detail` inputs dict, include L1/L5/L10 from stored context:
```python
{
    "season_rpg": rpg,
    "recent_7_rpg": rec_7,
    "l1_rpg": stored.get("l1_rpg"),
    "l5_rpg": stored.get("l5_rpg"),
    "l10_rpg": stored.get("l10_rpg"),
}
```

Add note in the non-default-50 branch:
```python
note="L1/L5/L10 shown for comparison; scoring form formula uses 0.6×L7 + 0.4×season"
```

Apply same extended inputs dict to the null-data branch too (all None).

TDD: test that debug output offense.inputs has l1_rpg, l5_rpg, l10_rpg keys.

---

## Step 4 — Frontend types

**File:** `frontend/src/types/api.ts`

Extend `TeamContext` interface:
```ts
l1_rpg: number | null
l5_rpg: number | null
l10_rpg: number | null
l1_scoring_form_rating: number | null
l5_scoring_form_rating: number | null
l10_scoring_form_rating: number | null
```

---

## Step 5 — Frontend display

**File:** `frontend/src/pages/MLBTeamContext.tsx`

In `TeamDebugPanel`, add a compact rolling form section between calibration banner and formula cards:

```tsx
{/* Rolling scoring form */}
<div className="text-[11px] text-slate-500 bg-[#090d1a] rounded px-3 py-2 border border-[#1a2540] space-y-1">
  <div className="font-semibold text-slate-400">Scoring Form</div>
  <div className="flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px]">
    <span>Season: <span className="text-slate-300">{data.ratings.offense.inputs?.season_rpg?.toFixed(1) ?? '—'} RPG</span></span>
    <span>L1: <span className="text-slate-300">{data.ratings.offense.inputs?.l1_rpg?.toFixed(1) ?? '—'}</span></span>
    <span>L5: <span className="text-slate-300">{data.ratings.offense.inputs?.l5_rpg?.toFixed(1) ?? '—'}</span></span>
    <span>L7: <span className="text-slate-300">{data.ratings.offense.inputs?.recent_7_rpg?.toFixed(1) ?? '—'}</span></span>
    <span>L10: <span className="text-slate-300">{data.ratings.offense.inputs?.l10_rpg?.toFixed(1) ?? '—'}</span></span>
  </div>
</div>
```

TDD: TypeScript build must pass (`npx tsc --noEmit`).
