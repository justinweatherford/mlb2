## Goal
Manual CSV import of public MLB weather-board data, conservative weather flags, and Weather Run Environment v1 scoring stored as context/evidence on games/candidates — never affects candidate generation or Good Entry scoring.

## Architecture
- `mlb/weather_run_environment.py` — pure scoring function (no DB). Input: weather fields + venue metadata. Output: wre_score, wre_label, wre_flags, wre_confidence, wre_reasons.
- `db/schema.py` — new `mlb_weather_reference` table DDL + migrations.
- `weather_reference_import.py` (root CLI) — CSV parser + DB upsert with computed WRE stored at import time.
- `api/routers/weather_reference.py` — GET /api/mlb/weather-reference?date= endpoint.
- `api/main.py` — register weather router.
- `mlb/live_capture_monitor.py` — add weather_rows, candidates_with_weather counts to output.
- `data/sample_weather_2026-06-15.csv` — 4-row verification sample.
- `docs/TOMORROW_SLATE_RUNBOOK.md` — add §I weather import step.

## Tech Stack
- SQLite WAL, init_db() + _apply_migrations() pattern
- FastAPI router with Depends(get_db)
- Pure Python CSV (stdlib csv module)
- pytest via `python tests/<file>.py`

## Critical Constraints
- Context/evidence ONLY — no TAKE labels, no BUY/SELL, no order placement
- Do NOT change candidate generation or Good Entry scoring
- Wind direction determined from text ONLY (wind_direction_degrees accepted but NOT used for in/out detection)
- Labels: run_friendly, run_suppressing, volatile, neutral, not_applicable, unknown

## Scoring Rules (Weather Run Environment v1)
- Base: 0
- Dome/closed roof → not_applicable (short-circuit, return early)
- No weather data at all → unknown
- temp_carry_pct = (temp_f - 70) / 10; temp_score = carry_pct * 5, cap ±15
- elevation_carry_pct = elevation_ft / 800; elevation_score = carry_pct * 4, cap +25
- wind_out + high_wind (>=15 mph): +15; very_high_wind (>=25 mph): +25
- wind_in + high_wind: -15; very_high_wind: -25
- high_wind + unknown direction → volatile flag, no score
- rain_risk (precip>=30% or rain in condition): volatile flag
- heavy_rain_risk (precip>=60% or thunderstorm): volatile flag + low confidence
- Label: volatile flag → volatile; score>=+20 → run_friendly; score<=-20 → run_suppressing; else neutral

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `mlb/weather_run_environment.py` | CREATE | Pure scoring; VENUE_METADATA dict; WEATHER_RUN_ENVIRONMENT_LABELS frozenset |
| `db/schema.py` | MODIFY | mlb_weather_reference DDL + migration entries |
| `weather_reference_import.py` | CREATE | Root CLI: parse CSV, upsert rows, compute+store WRE at import |
| `api/routers/weather_reference.py` | CREATE | GET /api/mlb/weather-reference?date= |
| `api/main.py` | MODIFY | import + include_router for weather_reference |
| `mlb/live_capture_monitor.py` | MODIFY | Add weather_rows + candidates_with_weather to result dict |
| `data/sample_weather_2026-06-15.csv` | CREATE | 4-row sample: hot+out, cold+in, dome, missing |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | MODIFY | §I Weather Import step |
| `tests/test_weather_run_environment.py` | CREATE | 50+ tests across 10 groups |
| `tests/test_weather_reference.py` | CREATE | 25+ tests across 7 groups |

## TDD Steps

1. Write `tests/test_weather_run_environment.py` (all failing)
2. Confirm RED (ModuleNotFoundError)
3. Implement `mlb/weather_run_environment.py`
4. Confirm GREEN for scoring tests
5. Write `tests/test_weather_reference.py` (all failing)
6. Confirm RED
7. Add `mlb_weather_reference` DDL + migrations to `db/schema.py`
8. Implement `weather_reference_import.py` (CSV parser + upsert CLI)
9. Implement `api/routers/weather_reference.py`
10. Register router in `api/main.py`
11. Update `mlb/live_capture_monitor.py`
12. Create `data/sample_weather_2026-06-15.csv`
13. Update `docs/TOMORROW_SLATE_RUNBOOK.md`
14. Confirm all tests GREEN; run full suite
15. Run CLI against sample CSV; verify output
