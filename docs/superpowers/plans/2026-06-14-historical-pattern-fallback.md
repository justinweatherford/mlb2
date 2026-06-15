## Goal
Add layered fallback / similarity matching to historical pattern engine so thin-sample exact matches fall back to broader league/nearby-state context, clearly labeled.

## Architecture
- `mlb/historical_patterns.py` — add `runs_range` param to existing functions; add layered wrapper functions
- `mlb/candidate_pattern_mapper.py` — extend `HistoricalContextResult` with 6 fallback fields; use layered wrappers
- `frontend/src/types/api.ts` — extend `HistoricalContext` interface
- `frontend/src/components/HistoricalContextBadge.tsx` — compact fallback display
- `tests/test_historical_pattern_fallback.py` — new test file

## Tech Stack
SQLite in-memory tests, Python dataclasses, React/TypeScript

---

## Layer definitions

### team_total_after_state
| Layer | Team | Runs filter | Inning |
|---|---|---|---|
| A exact_team_exact_state | same team | exact | same |
| B exact_team_nearby_state | same team | ±1 | same |
| C league_exact_state | any team | exact | same |
| D league_nearby_state | any team | ±1 | same |

### noisy_inning
| Layer | Team | Inning | Notes |
|---|---|---|---|
| A exact_team_exact_inning | same team | same | current |
| B league_exact_inning | any team | same | drop team filter |
| C league_any_inning | any team | any | broadest |

### f5_pace
| Layer | Runs filter | Notes |
|---|---|---|
| A exact_state | exact runs_through_inning | current |
| B nearby_state | ±1 | |
| C nearby_state_wider | ±2 | |

Selection rule: choose first layer with sample_size >= 5 (thin_sample threshold). Report all layers in `all_layers_summary`.

---

## Step 1 — Extend `summarize_team_total_after_state` with `runs_range` param

**File:** `mlb/historical_patterns.py`

Add optional `runs_range: Optional[tuple[int,int]] = None` parameter. When provided, replace `actual_runs_through != runs_through_inning` check with range check. Backward compatible — existing callers unchanged.

TDD: test that `runs_range=(5,7)` matches games with 5, 6, or 7 runs through inning.

---

## Step 2 — Extend `summarize_f5_pace` with `runs_range` param

**File:** `mlb/historical_patterns.py`

Same pattern: add `runs_range: Optional[tuple[int,int]] = None`. When provided, replace exact match with range check.

TDD: test that range match returns more cases than exact match.

---

## Step 3 — Add layered wrapper functions

**File:** `mlb/historical_patterns.py`

Add three functions:

```python
def layered_team_total_after_state(
    conn, *, team, runs_through_inning, inning, as_of_date=None, season=None
) -> tuple[PatternResult, list[dict], str, bool, str]:
    """Returns (best_result, all_layers_summary, selected_layer, fallback_used, fallback_warning)"""
    layers = [
        ("exact_team_exact_state", summarize_team_total_after_state(
            conn, team=team, runs_through_inning=runs_through_inning, inning=inning, ...)),
        ("exact_team_nearby_state", summarize_team_total_after_state(
            conn, team=team, runs_through_inning=runs_through_inning, inning=inning,
            runs_range=(runs_through_inning-1, runs_through_inning+1), ...)),
        ("league_exact_state", summarize_team_total_after_state(
            conn, team=None, runs_through_inning=runs_through_inning, inning=inning, ...)),
        ("league_nearby_state", summarize_team_total_after_state(
            conn, team=None, runs_through_inning=runs_through_inning, inning=inning,
            runs_range=(runs_through_inning-1, runs_through_inning+1), ...)),
    ]
    # Select first layer with sample_size >= 5
    ...
```

```python
def layered_noisy_inning(
    conn, *, min_runs, team=None, inning=None, as_of_date=None, season=None
) -> tuple[PatternResult, list[dict], str, bool, str]:
    layers = [
        ("exact_team_exact_inning", find_noisy_inning_cases(conn, team=team, inning=inning, ...)),
        ("league_exact_inning", find_noisy_inning_cases(conn, team=None, inning=inning, ...)),
        ("league_any_inning", find_noisy_inning_cases(conn, team=None, inning=None, ...)),
    ]
```

```python
def layered_f5_pace(
    conn, *, runs_through_inning, inning, as_of_date=None, season=None
) -> tuple[PatternResult, list[dict], str, bool, str]:
    layers = [
        ("exact_state", summarize_f5_pace(conn, runs_through_inning=runs_through_inning, inning=inning, ...)),
        ("nearby_state", summarize_f5_pace(conn, ..., runs_range=(runs_through_inning-1, runs_through_inning+1), ...)),
        ("nearby_state_wider", summarize_f5_pace(conn, ..., runs_range=(runs_through_inning-2, runs_through_inning+2), ...)),
    ]
```

Note: when team=None is passed to `summarize_team_total_after_state`, the SQL just drops the team filter (already how it works via optional param).

TDD: test each layered function selects correct layer, reports exact_sample_size correctly.

---

## Step 4 — Extend `HistoricalContextResult` with fallback fields

**File:** `mlb/candidate_pattern_mapper.py`

```python
@dataclass
class HistoricalContextResult:
    # existing fields ...
    exact_sample_size: int = 0
    selected_layer: str = "exact_team_exact_state"
    selected_layer_sample_size: int = 0
    all_layers_summary: list = field(default_factory=list)
    fallback_used: bool = False
    fallback_warning: str = ""
```

Update `_from_pattern` to accept the extra fallback args. Add `_from_layered` helper.

Update `map_candidate_to_pattern` to call `layered_*` functions instead of direct functions for the three pattern types.

TDD: test that `HistoricalContextResult` has all 6 new fields; test fallback_used=True when exact sample < 5.

---

## Step 5 — Frontend types

**File:** `frontend/src/types/api.ts`

Extend `HistoricalContext` interface:
```ts
exact_sample_size: number
selected_layer: string
selected_layer_sample_size: number
all_layers_summary: Array<{layer: string; sample_size: number; confidence_label: string}>
fallback_used: boolean
fallback_warning: string
```

---

## Step 6 — Frontend badge display

**File:** `frontend/src/components/HistoricalContextBadge.tsx`

When `ctx.fallback_used`:
- Row 1: `{sample_size} similar cases ({selected_layer_label}) | {confidence_chip}`
- Row 2: `Exact: {exact_sample_size} | cool {cooldown_rate}% · avg {avg}r`

When not fallback_used:
- Same as current display (no change)

Layer labels:
```ts
function layerShortLabel(layer: string): string {
  if (layer === "exact_team_exact_state") return "exact"
  if (layer === "exact_team_nearby_state") return "team ±1"
  if (layer === "league_exact_state") return "league exact"
  if (layer === "league_nearby_state") return "league ±1"
  if (layer === "league_any_inning") return "league"
  if (layer === "exact_team_exact_inning") return "team exact"
  if (layer === "nearby_state") return "±1 run"
  if (layer === "nearby_state_wider") return "±2 runs"
  return layer
}
```
