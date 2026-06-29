## Goal
Add a read-only Slate Monitor page at `/slate-monitor` that aggregates collector health,
pregame brain candidates, and EV overlay into one view for same-day live slate validation.

## Architecture
- New API router reads pre-generated output CSVs (no DB writes, no candidate generation)
- New React page calls the endpoint and renders 6 sections
- No changes to paper trading, model scoring, or candidate logic

## Tech Stack
FastAPI (CSV read via stdlib `csv`), React + TanStack Query, Tailwind (existing dark theme)

---

## Files Created / Modified

### New
- `api/routers/slate_monitor.py` — `GET /api/mlb/slate-monitor?date=` — reads output CSVs
- `frontend/src/pages/SlateMonitor.tsx` — 6-section read-only observer page

### Modified
- `api/main.py` — import + include_router for slate_monitor
- `frontend/src/App.tsx` — `/slate-monitor` route
- `frontend/src/components/Layout.tsx` — nav entry "Slate Monitor"
- `frontend/src/api/client.ts` — `slateMonitor()` method + param type
- `frontend/src/types/api.ts` — `SlateMonitorResponse` type

---

## Step-by-Step Tasks

### Task 1 — API router
File: `api/routers/slate_monitor.py`
- `_read_csv(path)` → `(rows, error_str)`; returns `([], "file not found")` if missing
- `_build_health_summary(rows)` → dict with counts + by_type breakdown
- `GET /mlb/slate-monitor?date=` reads 3 source directories, filters brain/EV by game_date,
  returns `{date, snapshot_health, snapshot_health_rows, brain_candidates, ev_overlay,
  ev_source_date, errors}`
- All reads are `open(path, newline="", encoding="utf-8")` — no writes

### Task 2 — Wire API
File: `api/main.py`
- Add `from api.routers import slate_monitor`
- Add `app.include_router(slate_monitor.router, prefix=PREFIX, tags=["slate-monitor"])`

### Task 3 — Frontend types
File: `frontend/src/types/api.ts`
- Add `SlateMonitorResponse` interface

### Task 4 — API client method
File: `frontend/src/api/client.ts`
- Add `slateMonitor: (date?: string) => apiFetch<SlateMonitorResponse>(...)`

### Task 5 — SlateMonitor page
File: `frontend/src/pages/SlateMonitor.tsx`
- Date picker + 60s auto-refresh
- Status banner (date, HEALTHY/DEGRADED/WARNING/ERROR badge, last refresh, stale warning)
- Collector health panel (fresh_pct, counts, by-type table)
- Brain candidates (7 tabs: Leans | Fades | 4plus | 5plus Avoid | F5 | Live | Full Avoid)
- EV overlay table (status filter + badge per row)
- Empty states for missing CSVs

### Task 6 — Wire frontend
Files: `App.tsx`, `Layout.tsx`
- Add route `/slate-monitor` → `<SlateMonitor />`
- Add nav entry "Slate Monitor"

---

## Constraints
- Zero writes — all reads
- No paper entries, no order actions
- No changes to candidate generation or model scoring
- Brain candidates filtered by `game_date == selectedDate`
- Health panel always shows the latest snapshot health (not date-filtered)
