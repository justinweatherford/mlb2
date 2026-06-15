# Tomorrow Slate Runbook

Paper-trading / manual review mode only. No auto-trading. No order execution.

This runbook covers what to start, in what order, and how to verify each component is alive before a game-day slate.

---

## Quick Start (one command)

From the repo root, run:

```
dev.bat slate 2026-06-15
```

Or for today's date:

```
dev.bat slate
```

This will:
1. Run Kalshi discovery synchronously (blocks until done, fails hard on error)
2. Fetch weather for the date synchronously via Open-Meteo (blocks; warns on error)
3. Open 7 named terminal windows for the slate stack:
   - **MLB2 API** — FastAPI server on port 8000
   - **MLB2 Frontend** — Vite dev server on port 5173
   - **MLB2 Orderbook Recorder** — Kalshi tape snapshots (runs 600 min, covers pregame + full slate)
   - **MLB2 MLB Poller** — MLB game state polling every 30s, pinned to the slate date
   - **MLB2 Live Watcher** — Candidate event generation every 60s
   - **MLB2 Paper Sync** — One-shot sync/settle; re-run with Up+Enter during and after games
   - **MLB2 Slate Health** — One-shot health check for the date
4. Open three browser tabs: frontend root, Live Dashboard, and slate health endpoint

Dev-only mode (API + frontend, no live data scripts):

```
dev.bat
```

### What healthy output looks like

- **MLB2 API window**: `Application startup complete.` on port 8000
- **MLB2 Frontend window**: `Local: http://localhost:5173/`
- **MLB2 Orderbook Recorder window**: `Cycle 1 done — polled=N written=N`
- **MLB2 MLB Poller window**: Logs game state rows every 30s for the slate date
- **MLB2 Live Watcher window**: Logs candidate events as they fire
- **MLB2 Paper Sync window**: Shows sync/settle summary; re-run with Up+Enter after games
- **MLB2 Slate Health window**: JSON with `"readiness": "ready"` or `"partial"`
- **Live Dashboard tab** (`/live-dashboard`): auto-refreshes every 30s; shows capture readiness

### What to do if a window fails

- **API fails**: Check Python environment and that port 8000 is free
- **Frontend fails**: Run `npm install` in the `frontend/` folder first
- **Orderbook Recorder fails**: Check Kalshi credentials (`.env` or environment variables)
- **MLB Poller fails**: Check network access to MLB Stats API
- **Live Watcher fails**: Usually an import error — check for uninstalled dependencies
- **Paper Sync fails**: Check DB path; run `python paper_sync.py --date YYYY-MM-DD` manually
- **Slate Health shows `stale`**: Poller or watcher may not be running yet; give it 1-2 minutes
- **Weather step fails**: Network issue; re-run `python weather_auto_fetch.py --date YYYY-MM-DD` when online

### How to stop everything

Close each terminal window individually. Each process stops when its window closes.

To stop all at once: close all `MLB2 *` terminal windows.

### REMINDER: Orderbook Recorder timing

The Orderbook Recorder must be running **during live games** for market tape context to populate in the Slate Review UI. If it was not running when candidates fired, those candidates will show `no_tape`. Start it before first pitch and let it run until games end. The 600-minute duration covers pregame through the end of a full evening slate.

### REMINDER: Paper Sync

The **MLB2 Paper Sync** window runs `paper_sync.py` once on launch. Re-run it (press **Up+Enter** in that window) periodically during games and once more after games end to settle final outcomes.

---

## Prerequisites

- `kalshi_mlb.db` exists and is accessible
- Python environment activated (same env as the main project)
- Kalshi credentials configured (env vars or config file used by `kalshi_discover.py`)

---

## Startup Sequence

Run each command in its own terminal (they run continuously).

### A — Discover Kalshi markets for the day

Run once per day before games, or whenever new markets are listed:

```bash
python kalshi_discover.py --date YYYY-MM-DD
```

Or to discover all open markets:

```bash
python kalshi_discover.py --all
```

**What to expect:** Logs each market found and inserted. DB should now have rows in `kalshi_markets` for today's games.

---

### B — Start the MLB poller

Polls the MLB Stats API for live game state (scores, inning, outs, runners).

```bash
python mlb_poller.py
```

**What to expect:** Logs game state updates every ~30 seconds during live games. Rows appear in `mlb_game_states` with `checked_at` timestamps.

---

### C — Start the Kalshi orderbook recorder

Snapshots Kalshi order books at regular intervals. Must be running during games for tape context to work.

```bash
python kalshi_orderbook_recorder.py
```

**What to expect:** Logs each snapshot batch. Rows appear in `kalshi_orderbook_snapshots` with `snapped_at` timestamps. If this is not running during games, tape context will show `no_tape` for all candidates.

---

### D — Start the live watcher

Watches MLB game state and fires candidate events when conditions match.

```bash
python live_watcher.py
```

**What to expect:** Logs each candidate event generated. Rows appear in `candidate_events` with `created_at` timestamps.

---

### E — Start the API server

```bash
uvicorn api.main:app --reload --port 8000
```

**What to expect:** FastAPI starts on port 8000. Check `/docs` for the interactive endpoint list.

---

### F — Start the frontend

```bash
cd frontend && npm run dev
```

**What to expect:** Vite dev server starts on port 5173. Open `http://localhost:5173` in a browser.

---

### G — Verify slate health (pre-game)

Check that all components are producing data:

```bash
curl "http://localhost:8000/api/mlb/slate-health?date=YYYY-MM-DD"
```

Or from Python:

```python
import sqlite3, json
from api.deps import DB_PATH
from mlb.slate_health import get_slate_health

conn = sqlite3.connect(DB_PATH)
print(json.dumps(get_slate_health(conn, "YYYY-MM-DD"), indent=2))
conn.close()
```

---

### H — Auto-Fetch Weather (recommended, before first pitch)

Automatically fetch weather for all slate games from Open-Meteo (no API key required):

```bash
python weather_auto_fetch.py --date 2026-06-15
```

**What it does:**
- Reads today's games from `mlb_games`
- Uses actual scheduled start time (`game_start_time_utc`) when available; falls back to 7 PM local when not
- Looks up each venue's lat/lon/timezone from built-in metadata
- Fetches hourly forecast from Open-Meteo near the game start time
- Computes Weather Run Environment v1 and stores as `source=open_meteo`
- Dome venues (Tropicana Field, Globe Life Field, Rogers Centre) are skipped — marked `not_applicable`

**Expected output:**
```
[weather_auto_fetch] date=2026-06-15  db=kalshi_mlb.db
  provider: Open-Meteo (public, no API key)

  Games found:      N
  Fetched:          N
  Skipped (dome):   N
  Missing venue:    0
  Errors:           0

  Game time source:
    Actual (from DB):   N
    Estimated (7PM tz): N

  WRE label breakdown: run_friendly=2, neutral=4, not_applicable=1
```

The `weather_time_estimated` flag is stored per row: `0` = actual start time from MLB API, `1` = 7 PM local fallback. Once the MLB Poller has run for the date, all games will have actual times (`Actual (from DB): N`, `Estimated (7PM tz): 0`).

**Good signs:**
- `Fetched` + `Skipped (dome)` = `Games found`
- `Missing venue` = 0
- `Errors` = 0
- WRE labels show a mix (not all neutral/unknown)

**Bad signs:**
- `Errors` > 0: network issue, retry or use manual CSV fallback
- `Missing venue` > 0: new team abbreviation not in venue metadata

**During the slate (if forecasts change):**
```bash
python weather_auto_fetch.py --date 2026-06-15
```
Re-running is idempotent — it updates existing rows, no duplicates.

**Optional manual overlay** (betting-board weather with wind direction text):
```bash
python weather_reference_import.py --date 2026-06-15 --file data/weather.csv
```
Manual rows use `source=manual` and coexist with auto-fetch rows.
Manual rows get full wind in/out scoring if `wind_direction_text` is provided (e.g., "Out to center").

**Via API** (if API is running):
```bash
curl "http://localhost:8000/api/mlb/weather-reference?date=2026-06-15"
```

---

### H-old — Import Weather Reference CSV (manual fallback)

Import public MLB weather-board data for context during your paper review.
Weather data is evidence only — it does not affect candidate generation or scoring.

```bash
python weather_reference_import.py --date 2026-06-15 --file data/weather.csv
```

**CSV format** (see `data/sample_weather_2099-01-01.csv` as a template — uses fake date 2099-01-01):
```
game_date, away_abbr, home_abbr, game_time_et, venue_name,
temperature_f, wind_speed_mph, wind_direction_text, wind_direction_degrees,
humidity_pct, precip_probability_pct, condition_text, roof_type, source
```

**What to expect:** Each row gets a Weather Run Environment (WRE) label and score:
- `run_friendly` (+20 or higher): hot + wind out, or high elevation
- `run_suppressing` (-20 or lower): cold + wind in
- `volatile`: rain risk, thunderstorms, or high wind with unknown direction
- `neutral`: mild weather, no strong signal
- `not_applicable`: indoor dome venue
- `unknown`: missing weather data

**Via API** (if API is running):
```bash
curl "http://localhost:8000/api/mlb/weather-reference?date=2026-06-15"
```

**Notes:**
- Wind direction is determined from text only (e.g., "Out to center", "In from right") — degrees column is stored but NOT used to infer direction.
- Import is idempotent — running it twice updates existing rows rather than duplicating.
- The Live Capture Monitor will show `weather_rows` count after import.

---

### I — Live Capture Monitor (during games, every 20–30 min)

During live games, run this to confirm the pipeline is capturing useful learning data:

```bash
python live_capture_monitor.py --date 2026-06-15
```

**Good signs:**
- `Status: ready`
- `Snapshots in window` increasing each check
- `Latest snapshot` is recent (within last 2 minutes)
- `Candidates` count increasing once games go live
- `Paper setups` count increasing after each `paper_sync.py` run
- `With entry price` count increasing (tape is attaching to candidates)
- `Good entry labels` breakdown appearing (labels being assigned)
- `No entry price` count is low or zero if recorder is live

**Bad signs:**
- `Status: stale_recorder` — Kalshi Orderbook Recorder window may have crashed
- `Status: candidates_without_tape` — Candidates firing but recorder not snapshotting
- `Status: paper_not_synced` — Run `python paper_sync.py` to create paper setups
- `Status: stale_mlb` — MLB Poller window may have crashed
- All paper setups are `no_entry_price` despite recorder being live — check tape overlap timing

**Via API** (if API is running):
```bash
curl "http://localhost:8000/api/mlb/live-capture-monitor?date=2026-06-15"
```

**What each Status means:**

| Status | Meaning | Action |
|---|---|---|
| `ready` | All data flowing | Keep collecting |
| `waiting_for_games` | No games yet for this date | Normal before first pitch |
| `stale_recorder` | No Kalshi snapshots in slate window | Check Orderbook Recorder window |
| `stale_mlb` | No MLB game states today | Check MLB Poller window |
| `no_candidates_yet` | Games live but no candidates fired yet | Live Watcher should fire soon |
| `paper_not_synced` | Candidates exist but no paper setups | Run `python paper_sync.py` |
| `candidates_without_tape` | Paper setups all have no entry price | Recorder may have been offline |
| `blocked` | DB error | Check database |

---

## Pre-Slate Pipeline Smoke Test (optional, recommended)

Before launching the slate stack, run a quick end-to-end dry run to confirm the learning pipeline is wired correctly:

```
python pre_slate_dry_run.py --date 2026-06-15
```

This inserts synthetic data under an isolated test namespace (2099-01-01), runs the full pipeline, and cleans up. It does NOT touch real slate data.

**Expected output (all PASS):**

```
=== Pre-Slate Dry Run  slate=2026-06-15  test_ns=2099-01-01 ===

  [PASS]  DB connection
  [PASS]  Candidate inserted
  [PASS]  Market tape matched
  [PASS]  Paper setup created
  [PASS]  Entry price attached
  [PASS]  Good Entry evaluated
  [PASS]  Weather context present
  [PASS]  Live capture monitor reads it
  [PASS]  Post-slate report reads it
  [PASS]  Cleanup complete

  Result: ALL PASS — pipeline is wired end-to-end.
```

**If any step fails:** Do not start the live slate until the failing step is checked.

- `DB connection FAIL` — check DB path and permissions
- `Market tape matched FAIL` — tape correlation module issue
- `Paper setup created FAIL` — paper lifecycle sync issue
- `Entry price attached FAIL` — tape not being wired to paper setup
- `Good Entry evaluated FAIL` — good_entry_eval or paper_lifecycle issue
- `Weather context present FAIL` — weather insert or DB schema issue
- `Live capture monitor reads it FAIL` — monitor query issue
- `Post-slate report reads it FAIL` — post_slate_report module issue

---

## Smoke Checklist (pre-slate)

Run these checks ~30 minutes before first pitch:

- [ ] `kalshi_markets_total > 0` — markets discovered for today's games
- [ ] `game_states_today > 0` — MLB poller is running and has data
- [ ] `snapshots_in_window > 0` — Kalshi recorder is running (slate window = today 00:00 to next day 12:00 UTC)
- [ ] `readiness` is `partial` or `ready` (not `stale` or `blocked`)
- [ ] UI loads without error at `http://localhost:5173`
- [ ] SlateReview page shows today's date in the date selector
- [ ] No unhandled exceptions in any terminal window

> **Note on UTC midnight:** Late US games can cross midnight UTC. Snapshots captured
> after midnight on the next UTC day (e.g., 00:30 UTC June 15 for a June 14 game) are
> counted in `snapshots_in_window` but **not** in `snapshots_today`. Use
> `snapshots_in_window` as the authoritative recorder health indicator.

---

## What to Expect During Live Games

| Component          | Normal state                                      |
|--------------------|---------------------------------------------------|
| MLB poller         | Logs every ~30s per live game                     |
| Kalshi recorder    | Logs snapshot batch every ~60s                    |
| Live watcher       | Logs candidates as conditions trigger             |
| Tape column in UI  | Shows `thin`, `usable`, or `strong` for candidates generated while recorder was live |
| Historical context | Shows fallback layer label when exact sample thin |

---

## Known Limitations

- **Tape requires recorder to be live during games.** If `kalshi_orderbook_recorder` was not running when a candidate fired, that candidate's tape context will show `no_tape`. This is expected — the tape is retrospective, not real-time.
- **Stale readiness after midnight.** After games end and before the next day's poller run starts, health may show `stale`. This is correct.
- **Historical context uses season-to-date data.** Thin sample warnings are normal early in the season.

---

### J — Export live-state snapshot (optional, during or after slate)

At any point during or after the slate, export a dashboard-friendly JSON snapshot:

```bash
python export_live_state.py --date 2026-06-15
```

Default output: `kalshi_output/live_state_output/live_state_mlb_2026-06-15.json`

Or to a custom path:

```bash
python export_live_state.py --date 2026-06-15 --out /path/to/file.json
```

The snapshot is written atomically and is decoupled from the live pipeline. Re-running it at any time refreshes the file without affecting candidate generation, scoring, or order execution.

**Via API** (if API is running):

```bash
curl "http://localhost:8000/api/mlb/live-state-snapshot?date=2026-06-15"
```

The snapshot uses `schema_version: "mlb_live_state_v1"` and can be consumed by any external dashboard tool that reads JSON files.

---

## After the Slate

Once games are complete (typically by 01:00–03:00 UTC), run:

```bash
python paper_sync.py --date 2026-06-15
```

Or for today's date:

```bash
python paper_sync.py
```

This will:
1. **Sync** — create `paper_setups` rows for any candidates that don't have one yet
2. **Settle** — resolve outcomes (won/lost/pushed) for games marked final
3. **Print** a status breakdown by `paper_status`

Expected output (example):

```
[paper_sync] date=2026-06-15  db=kalshi_mlb.db

  SYNC    processed=N  created=N  skipped=N
  SETTLE  checked=N  settled=N

  STATUS BREAKDOWN:
    paper_open                 0
    paper_closed               N
    no_entry_price             N
    blocked_observation        N
    not_trackable              N

[paper_sync] done.
```

Or use the API endpoint directly:

```bash
curl -X POST "http://localhost:8000/api/mlb/paper-setups/sync-and-settle?date=2026-06-15"
```

After sync/settle, open **Slate Review** in the UI to see outcomes in the Paper column.

---

## Review Mode Notes

This dashboard is for **paper trading and manual review only**.

- All candidate events are logged for review — not for execution.
- No orders are placed automatically.
- The "Tape" and "Historical Context" columns are evidence layers — they inform your judgment, not the system's.
- Record any trades you decide to make manually via the Manual Trade Journal.
