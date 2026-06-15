# Historical Pattern Engine v1

## Goal
Build a read-only MLB historical pattern analysis module that answers "what usually happened next?" for live game setups, with as-of-date safety and no impact on candidate generation.

## Architecture
- `mlb/historical_patterns.py` — all pattern query functions + PatternResult dataclass + confidence labels
- `api/routers/historical_patterns.py` — single GET endpoint `/api/mlb/historical-patterns/summary`
- `api/main.py` — register new router
- `tests/test_historical_patterns.py` — TDD tests

Data sources (read-only): `mlb_inning_scores`, `mlb_games`, `mlb_play_events`, `mlb_team_context`, `fangraphs_team_offense`, `kalshi_orderbook_snapshots` (placeholder hook only).

## Tech Stack
- SQLite (existing schema, no new tables)
- FastAPI (existing router pattern)
- stdlib only: dataclasses, statistics, datetime

## File Map
| File | Action | Responsibility |
|------|--------|---------------|
| `mlb/historical_patterns.py` | CREATE | PatternResult, 5 query functions, confidence labels, Kalshi hook stub |
| `api/routers/historical_patterns.py` | CREATE | GET /api/mlb/historical-patterns/summary |
| `api/main.py` | MODIFY | register new router |
| `tests/test_historical_patterns.py` | CREATE | all tests |

## PatternResult fields
```python
@dataclass
class PatternResult:
    pattern_name: str
    sample_size: int
    filters_used: dict
    as_of_date: str
    matching_cases: list[dict]   # raw rows for debugging
    outcome_summary: dict
    continuation_rate: float | None
    cooldown_rate: float | None
    average_rest_of_game_runs: float | None
    median_rest_of_game_runs: float | None
    threshold_hit_rates: dict[str, float]   # "3.5": 0.72, etc.
    confidence_label: str   # insufficient_sample | thin_sample | usable_sample | strong_sample
    notes: str
    warnings: list[str]
```

## Confidence thresholds
- `< 5` → `insufficient_sample`
- `5–19` → `thin_sample`
- `20–49` → `usable_sample`
- `≥ 50` → `strong_sample`

## 5 Pattern Functions
1. `find_noisy_inning_cases(conn, min_runs, as_of_date, season, team, inning)` → PatternResult
2. `summarize_team_total_after_state(conn, team, runs_through_inning, as_of_date, season)` → PatternResult
3. `summarize_f5_pace(conn, runs_through_inning, inning, as_of_date, season)` → PatternResult
4. `summarize_late_scoring(conn, inning_start, as_of_date, season)` → PatternResult
5. `summarize_true_offense_mismatch_cases(conn, as_of_date, season)` → PatternResult

## as_of_date safety rule
All queries include `WHERE g.game_date < :as_of_date` (strictly before, not ≤).
Default: today's date.

## Kalshi hook (placeholder)
```python
def get_nearest_market_snapshots(conn, market_ticker, event_time_utc, window_seconds=60) -> dict:
    """Stub: returns empty dict until Kalshi correlation is built in v2."""
    return {"pre_snapshot": None, "post_snapshot": None}
```

## API endpoint
```
GET /api/mlb/historical-patterns/summary
  ?pattern_type=noisy_inning
  &team=NYY
  &inning=3
  &runs_scored=3
  &as_of_date=2025-09-01
  &season=2025
  &limit=200
```
Returns: `PatternResult` serialized as dict.

## Step sequence (TDD)
1. Tests (all failing) → `mlb/historical_patterns.py` (GREEN) → router → register → full suite
