## Goal
Replace the universal 7:05 PM game-time fallback with actual scheduled start times from `mlb_games`, setting `weather_time_estimated=0` when real data is available.

## Architecture
`mlb_games.game_start_time_utc` (new) → `get_games_for_date()` → `fetch_and_upsert_weather()` → `mlb_weather_reference.weather_time_estimated`

## Tech Stack
MLB Stats API `gameDate` field (UTC ISO), `zoneinfo` for 7PM fallback (unchanged).

## Files

| File | Change |
|------|--------|
| `db/schema.py` | Add `game_start_time_utc TEXT` to `mlb_games` DDL + migration |
| `mlb/game_store.py` | `_upsert_game()` + `fetch_and_store_schedule()` store `gameDate[:16]` |
| `mlb/weather_auto_fetch.py` | `get_games_for_date()` returns new field; `fetch_and_upsert_weather()` uses actual time, returns actual/estimated counts |
| `weather_auto_fetch.py` | CLI shows actual/estimated per row + summary totals |
| `mlb/live_capture_monitor.py` | `weather_time_actual_count` + `weather_time_estimated_count` |
| `live_capture_monitor.py` | Print new weather time fields |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | Update weather section |

## Steps (TDD)

1. Write failing tests → RED
2. Add `game_start_time_utc` to schema + migration
3. Update `_upsert_game()` and `fetch_and_store_schedule()` to store the field
4. Update `get_games_for_date()` to return it
5. Update `fetch_and_upsert_weather()` to use actual vs estimated, track counts
6. Update `weather_auto_fetch.py` CLI output
7. Update live capture monitor (function + CLI)
8. Update runbook
9. Verify: full test suite GREEN
