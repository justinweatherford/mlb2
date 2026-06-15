## Goal
Automatically fetch MLB game weather from Open-Meteo (no API key) using local schedule + venue metadata, compute WRE v1, and upsert into mlb_weather_reference with source=open_meteo — making manual CSV optional.

## Architecture
- `mlb/venue_metadata.py` — static MLB venue registry by home_abbr (lat/lon/tz/roof_type) with alias normalization
- `mlb/weather_auto_fetch.py` — core logic: fetch_open_meteo, parse_open_meteo_hourly, get_games_for_date, fetch_and_upsert_weather
- `weather_auto_fetch.py` — root CLI (mirrors live_capture_monitor.py pattern)
- `db/schema.py` — 7 new columns on mlb_weather_reference: wind_gust_mph, pressure_hpa, weather_code, weather_for_time_utc, fetched_at_utc, weather_time_estimated, provider_url
- `mlb/live_capture_monitor.py` — add weather_rows_open_meteo, weather_rows_manual, games_weather_missing
- `live_capture_monitor.py` (root CLI) — print new weather breakdown
- `docs/TOMORROW_SLATE_RUNBOOK.md` — update §H with auto-fetch steps

## Tech Stack
- Open-Meteo: https://api.open-meteo.com/v1/forecast — no key, hourly forecast by lat/lon
- urllib.request (stdlib) — no requests dependency
- zoneinfo (stdlib Python 3.9+) — local→UTC game time estimation
- Existing: mlb_weather_reference table, compute_weather_run_environment(), init_db()

## Critical Constraints
- No API key
- No scraping
- No trade labels, no candidate generation changes, no Good Entry changes
- Wind direction text = None for auto-fetch rows (degrees stored but not used for in/out)
  → high wind (≥15mph) always sets high_wind_unknown_direction flag (conservative/volatile)
- Dome games: skip fetch, upsert not_applicable record
- Retractable: fetch weather, flag retractable_unknown if roof is retractable
- fetcher= injectable for tests — no real HTTP in test suite
- All game times estimated (no actual start time in DB) → weather_time_estimated=1

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `mlb/venue_metadata.py` | CREATE | MLB_VENUE_BY_ABBR dict (30+ abbrs), TEAM_ABBR_ALIASES, resolve_venue() |
| `mlb/weather_auto_fetch.py` | CREATE | fetch_open_meteo(), parse_open_meteo_hourly(), _estimate_game_time_utc(), get_games_for_date(), fetch_and_upsert_weather() |
| `weather_auto_fetch.py` | CREATE | root CLI: argparse --date, prints summary |
| `db/schema.py` | MODIFY | 7 new columns in DDL + 7 migration entries |
| `mlb/live_capture_monitor.py` | MODIFY | weather_rows_open_meteo, weather_rows_manual, games_weather_missing |
| `live_capture_monitor.py` | MODIFY | print weather open_meteo/manual/missing breakdown |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | MODIFY | §H: python weather_auto_fetch.py --date YYYY-MM-DD |
| `tests/test_venue_metadata.py` | CREATE | 25+ tests: completeness, aliases, lat/lon, dome, tz |
| `tests/test_weather_auto_fetch.py` | CREATE | 35+ tests: fetch, parse, dome-skip, upsert, idempotency, errors, counts |

## Open-Meteo Hourly Variables
temperature_2m (°F), relative_humidity_2m (%), precipitation_probability (%),
precipitation (inch), wind_speed_10m (mph), wind_direction_10m (°),
wind_gusts_10m (mph), weather_code (WMO), surface_pressure (hPa)

Fetch start_date=game_date, end_date=game_date+1 (covers UTC-shifted West Coast games)
Pick nearest hourly index to estimated UTC game time.

## Game Time Estimation
Use 7:05 PM local (venue tz) → UTC. Always set weather_time_estimated=1.
For NYY (ET): 7:05 PM EDT = 23:05 UTC same day.
For LAD (PT): 7:05 PM PDT = 02:05 UTC next day (within end_date+1 window).

## New DB Columns (mlb_weather_reference)
wind_gust_mph REAL
pressure_hpa REAL
weather_code INTEGER
weather_for_time_utc TEXT
fetched_at_utc TEXT
weather_time_estimated INTEGER NOT NULL DEFAULT 0
provider_url TEXT

## Live Capture Monitor New Fields
weather_rows_open_meteo: COUNT(*) WHERE source='open_meteo' AND game_date=?
weather_rows_manual: COUNT(*) WHERE source!='open_meteo' AND game_date=?
games_weather_missing: MAX(0, games_today - weather_games_covered)
  where weather_games_covered = COUNT(DISTINCT home_abbr||'|'||away_abbr) WHERE game_date=?

## WMO Weather Code → condition_text Mapping
0: "Clear sky" | 1-3: "Partly cloudy" | 45-48: "Foggy" | 51-67: "Rain"
71-77: "Snow" | 80-82: "Rain showers" | 85-86: "Snow showers"
95: "Thunderstorm" | 96-99: "Thunderstorm with hail"

## TDD Steps
1. Write tests/test_venue_metadata.py — all RED
2. Confirm RED (ModuleNotFoundError)
3. Implement mlb/venue_metadata.py — GREEN venue tests
4. Write tests/test_weather_auto_fetch.py — all RED
5. Confirm RED
6. Update db/schema.py (DDL + migrations)
7. Implement mlb/weather_auto_fetch.py
8. Implement weather_auto_fetch.py (CLI)
9. Update mlb/live_capture_monitor.py
10. Update live_capture_monitor.py CLI
11. Update runbook
12. Full test suite — 0 failures
13. Live CLI run: python weather_auto_fetch.py --date 2026-06-15 (network permitting)
