# Historical Context Integration

## Goal
Surface read-only historical pattern context alongside candidate/setup cards in Slate Review, using the existing Historical Pattern Engine without changing any candidate generation or scoring logic.

## Architecture
- `mlb/candidate_pattern_mapper.py` maps a candidate row to the most relevant PatternResult
- `api/routers/candidate_history.py` exposes a batch GET endpoint
- `api/main.py` registers the new router
- `frontend/src/types/api.ts` adds `HistoricalContext` type
- `frontend/src/api/client.ts` adds `candidateHistoricalContext(date)` call
- `frontend/src/components/HistoricalContextBadge.tsx` compact display component
- `frontend/src/pages/SlateReview.tsx` wires HistoricalContextBadge into Timeline and Setups tabs
- `tests/test_candidate_pattern_mapper.py` covers all mapping + API behavior

## Tech Stack
- Python: existing `mlb/historical_patterns.py` (5 pattern functions, PatternResult)
- FastAPI: existing `api/deps.get_db` + router pattern
- React/TypeScript: existing `useQuery`, `Badge`, `SlateReview` patterns

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `mlb/candidate_pattern_mapper.py` | CREATE | Maps candidate row dict → PatternResult; one function per candidate type |
| `api/routers/candidate_history.py` | CREATE | `GET /api/mlb/candidates/historical-context` batch endpoint |
| `api/main.py` | MODIFY | Import + register `candidate_history` router |
| `tests/test_candidate_pattern_mapper.py` | CREATE | All backend TDD tests |
| `frontend/src/types/api.ts` | MODIFY | Add `HistoricalContext` interface |
| `frontend/src/api/client.ts` | MODIFY | Add `candidateHistoricalContext(date)` |
| `frontend/src/components/HistoricalContextBadge.tsx` | CREATE | Compact display; unavailable/thin/usable states |
| `frontend/src/pages/SlateReview.tsx` | MODIFY | Wire HistoricalContextBadge into Timeline rows + Setups tab filter chips |

---

## Step 1 — Write all failing backend tests

**File:** `tests/test_candidate_pattern_mapper.py`

Tests to cover:
- `map_candidate_to_pattern` exists and is callable
- `market_overreaction` / `fg_total` → `find_noisy_inning_cases` called with correct params
- `team_total_lag` → `summarize_team_total_after_state` called with correct params
- `f5_total` derivative → `summarize_f5_pace` called
- spread/run-line → returns `unavailable` HistoricalContextResult
- missing candidate fields → returns `unavailable` gracefully
- `as_of_date` passed through from candidate's `created_at` date
- blocked candidate still returns historical context (does not change blocked status)
- no TAKE/recommendation keys in result
- no candidate generation import
- batch mapper: list of candidates → list of HistoricalContextResult
- one bad candidate does not raise
- thin sample warning surfaces in result
- confidence_label propagated correctly

**TDD:** Write tests first, confirm all fail, then implement.

---

## Step 2 — Implement `mlb/candidate_pattern_mapper.py`

```python
from dataclasses import dataclass, asdict
from typing import Optional
import sqlite3
from mlb.historical_patterns import (
    PatternResult, find_noisy_inning_cases,
    summarize_team_total_after_state, summarize_f5_pace,
    summarize_late_scoring, summarize_true_offense_mismatch_cases,
)

@dataclass
class HistoricalContextResult:
    candidate_id: Optional[int]
    matched_pattern_type: Optional[str]   # None if unavailable
    pattern_name: str
    sample_size: int
    confidence_label: str
    summary_text: str
    continuation_rate: Optional[float]
    cooldown_rate: Optional[float]
    average_rest_of_game_runs: Optional[float]
    median_rest_of_game_runs: Optional[float]
    threshold_hit_rates: dict
    warnings: list
    as_of_date: str
    filters_used: dict
    available: bool


_SPREAD_TYPES = frozenset({
    "fg_spread", "f5_spread", "spread_run_line", "f5_moneyline", "fg_moneyline",
})


def _unavailable(candidate_id, reason: str, as_of_date: str) -> HistoricalContextResult:
    return HistoricalContextResult(
        candidate_id=candidate_id,
        matched_pattern_type=None,
        pattern_name="unavailable",
        sample_size=0,
        confidence_label="insufficient_sample",
        summary_text=reason,
        continuation_rate=None,
        cooldown_rate=None,
        average_rest_of_game_runs=None,
        median_rest_of_game_runs=None,
        threshold_hit_rates={},
        warnings=[],
        as_of_date=as_of_date,
        filters_used={},
        available=False,
    )


def _summary_text(r: PatternResult) -> str:
    if r.sample_size == 0:
        return "Not enough matching history yet."
    parts = [f"Similar cases: {r.sample_size} | Confidence: {r.confidence_label.replace('_', ' ')}"]
    if r.cooldown_rate is not None:
        parts.append(f"Cooldown rate: {r.cooldown_rate:.0%}")
    if r.average_rest_of_game_runs is not None:
        parts.append(f"Avg rest-of-game runs: {r.average_rest_of_game_runs:.1f}")
    if r.warnings:
        parts.append(r.warnings[0])
    return " | ".join(parts)


def map_candidate_to_pattern(
    conn: sqlite3.Connection,
    candidate: dict,
    as_of_date: Optional[str] = None,
) -> HistoricalContextResult:
    cid = candidate.get("id")
    deriv = candidate.get("derivative_type") or candidate.get("selected_derivative_type") or ""
    candidate_type = candidate.get("candidate_type") or ""
    inning = candidate.get("inning")
    team = candidate.get("selected_team_abbr")
    score_away = candidate.get("score_away")
    score_home = candidate.get("score_home")
    created_at = candidate.get("created_at") or ""
    aod = as_of_date or (created_at[:10] if len(created_at) >= 10 else None)
    if not aod:
        from datetime import date
        aod = date.today().isoformat()

    if deriv in _SPREAD_TYPES:
        return _unavailable(cid, "Historical pattern unavailable for this derivative.", aod)

    # F5 total
    if "f5_total" in deriv or deriv == "f5_total":
        runs_so_far = (score_away or 0) + (score_home or 0)
        r = summarize_f5_pace(conn, runs_through_inning=runs_so_far,
                              inning=inning or 2, as_of_date=aod)
        return HistoricalContextResult(
            candidate_id=cid, matched_pattern_type="f5_pace",
            pattern_name=r.pattern_name, sample_size=r.sample_size,
            confidence_label=r.confidence_label, summary_text=_summary_text(r),
            continuation_rate=r.continuation_rate, cooldown_rate=r.cooldown_rate,
            average_rest_of_game_runs=r.average_rest_of_game_runs,
            median_rest_of_game_runs=r.median_rest_of_game_runs,
            threshold_hit_rates=r.threshold_hit_rates, warnings=r.warnings,
            as_of_date=aod, filters_used=r.filters_used, available=r.sample_size > 0,
        )

    # Team total lag
    if "team_total" in deriv or candidate_type == "team_total_lag":
        if not team:
            return _unavailable(cid, "No team identified for team total pattern.", aod)
        # runs through inning for the selected team
        if score_away is not None and score_home is not None:
            # crude: we don't know which side yet; use combined as proxy
            runs = score_away + score_home
        else:
            runs = 0
        r = summarize_team_total_after_state(
            conn, team=team, runs_through_inning=runs,
            inning=inning or 3, as_of_date=aod,
        )
        return HistoricalContextResult(
            candidate_id=cid, matched_pattern_type="team_total_after_state",
            pattern_name=r.pattern_name, sample_size=r.sample_size,
            confidence_label=r.confidence_label, summary_text=_summary_text(r),
            continuation_rate=r.continuation_rate, cooldown_rate=r.cooldown_rate,
            average_rest_of_game_runs=r.average_rest_of_game_runs,
            median_rest_of_game_runs=r.median_rest_of_game_runs,
            threshold_hit_rates=r.threshold_hit_rates, warnings=r.warnings,
            as_of_date=aod, filters_used=r.filters_used, available=r.sample_size > 0,
        )

    # FG total overreaction / late game
    if "fg_total" in deriv or "full_game" in deriv or "market_overreaction" in candidate_type:
        if inning is not None and inning >= 6:
            r = summarize_late_scoring(conn, inning_start=inning, as_of_date=aod)
        else:
            r = find_noisy_inning_cases(conn, min_runs=3, as_of_date=aod, inning=inning)
        return HistoricalContextResult(
            candidate_id=cid, matched_pattern_type=r.pattern_name,
            pattern_name=r.pattern_name, sample_size=r.sample_size,
            confidence_label=r.confidence_label, summary_text=_summary_text(r),
            continuation_rate=r.continuation_rate, cooldown_rate=r.cooldown_rate,
            average_rest_of_game_runs=r.average_rest_of_game_runs,
            median_rest_of_game_runs=r.median_rest_of_game_runs,
            threshold_hit_rates=r.threshold_hit_rates, warnings=r.warnings,
            as_of_date=aod, filters_used=r.filters_used, available=r.sample_size > 0,
        )

    return _unavailable(cid, "No pattern mapping defined for this setup.", aod)


def map_candidates_batch(
    conn: sqlite3.Connection,
    candidates: list[dict],
    as_of_date: Optional[str] = None,
) -> list[HistoricalContextResult]:
    results = []
    for c in candidates:
        try:
            results.append(map_candidate_to_pattern(conn, c, as_of_date=as_of_date))
        except Exception:
            cid = c.get("id")
            from datetime import date
            aod = as_of_date or date.today().isoformat()
            results.append(_unavailable(cid, "Error computing historical context.", aod))
    return results
```

---

## Step 3 — Implement `api/routers/candidate_history.py`

```python
"""
GET /api/mlb/candidates/historical-context
  ?date=YYYY-MM-DD    (defaults to today)
  &limit=100

Returns list of HistoricalContextResult for all latest-unique candidates on date.
One-per-candidate. Errors on individual candidates are swallowed — they return
available=False. Does not change any candidate data.
"""
from dataclasses import asdict
from datetime import date
from typing import Optional
import sqlite3

from fastapi import APIRouter, Depends, Query
from api.deps import get_db
from mlb.candidates import list_candidate_events
from mlb.candidate_pattern_mapper import map_candidates_batch

router = APIRouter()

@router.get("/mlb/candidates/historical-context")
def get_candidates_historical_context(
    date_str: Optional[str] = Query(default=None, alias="date"),
    limit: int = Query(default=200, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    rows = list_candidate_events(
        db, date_from=day, date_to=day,
        latest_unique=True, limit=limit,
    )
    candidates = [dict(r) for r in rows]
    results = map_candidates_batch(db, candidates, as_of_date=day)
    return {
        "date": day,
        "count": len(results),
        "items": [asdict(r) for r in results],
    }
```

---

## Step 4 — Register router in `api/main.py`

Add one import + one `app.include_router` line (same as the historical_patterns router).

---

## Step 5 — Frontend types + API client

In `frontend/src/types/api.ts` add:
```typescript
export interface HistoricalContext {
  candidate_id: number | null
  matched_pattern_type: string | null
  pattern_name: string
  sample_size: number
  confidence_label: string
  summary_text: string
  continuation_rate: number | null
  cooldown_rate: number | null
  average_rest_of_game_runs: number | null
  median_rest_of_game_runs: number | null
  threshold_hit_rates: Record<string, number | null>
  warnings: string[]
  as_of_date: string
  filters_used: Record<string, unknown>
  available: boolean
}

export interface HistoricalContextResponse {
  date: string
  count: number
  items: HistoricalContext[]
}
```

In `frontend/src/api/client.ts` add:
```typescript
candidateHistoricalContext: (date: string) =>
  apiFetch<HistoricalContextResponse>(`/api/mlb/candidates/historical-context`, { date }),
```

---

## Step 6 — `HistoricalContextBadge.tsx` component

```tsx
// frontend/src/components/HistoricalContextBadge.tsx
// Compact, read-only. Shows: sample size, confidence, cooldown rate, avg runs.
// Unavailable state: "Not enough matching history yet."
// Blocked candidates: still shows context; never implies override.
```

Three visual states:
1. `available=false` → small muted text "No historical data"
2. `confidence_label=insufficient_sample|thin_sample` + available → yellow chip "thin" + sample count + warning
3. `usable_sample|strong_sample` → compact inline: "48 cases · usable · cooldown 58% · avg 2.1r"

---

## Step 7 — Wire into SlateReview

In the **Timeline** tab (`TimelineTable`): add a "History" column — renders `<HistoricalContextBadge />` keyed by `e.id`.

Context lookup: fetch `candidateHistoricalContext(date)` once per tab, build a `Map<number, HistoricalContext>` by `candidate_id`, pass down.

In the **Setups** tab: add filter chips below summary cards:
- `All` | `Usable history` | `Thin/None`

Filter applies to `SetupsTable` rows, matched by `market_ticker`.

---

## Constraints (enforced in every file)

- No TAKE/recommendation/signal fields anywhere
- No changes to candidate generation, scoring, guardrails
- blocked candidates remain blocked; historical context is display-only
- `as_of_date` = the candidate's date, not today (prevents future data leak)

---

## Step sequence (TDD)

1. Write failing tests → `test_candidate_pattern_mapper.py`
2. Implement `mlb/candidate_pattern_mapper.py` → GREEN
3. Implement `api/routers/candidate_history.py` → router tests GREEN
4. Register router in `api/main.py`
5. Add TypeScript types + api client method
6. Create `HistoricalContextBadge.tsx`
7. Modify `SlateReview.tsx`
8. Full suite + browser check
