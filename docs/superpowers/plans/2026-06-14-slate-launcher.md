## Goal
Rewrite `dev.bat` with two modes (dev / slate), date parameter, correct commands, named windows, and browser auto-open so tomorrow's evidence run starts with one command.

## Architecture
- `dev.bat` — entry point, mode/date detection, opens named windows
- `tests/test_launcher.py` — content checks (commands, labels, no-trade)
- `docs/TOMORROW_SLATE_RUNBOOK.md` — add one-click section at top

## Tech Stack
Windows batch (cmd.exe), Python subprocess (health check via curl), existing scripts

---

## Corrections from research

- `kalshi_discover.py` has NO `--all` flag; correct command is `--sport mlb` (open markets default)
- `kalshi_ws.py` is NOT in the required process list; drop it
- recorder args: `--sport mlb --interval-seconds 10 --duration-minutes 240 --jsonl data/kalshi_orderbook_DATE.jsonl --verbose`
- `data/` dir already exists

---

## Files

| File | Action |
|------|--------|
| `dev.bat` | Rewrite |
| `tests/test_launcher.py` | Create |
| `docs/TOMORROW_SLATE_RUNBOOK.md` | Update (prepend one-click section) |

---

## Step 1 — Write failing tests

`tests/test_launcher.py` — content-only checks, no shell execution:

Groups:
- `TestLauncherExists` — dev.bat present at repo root
- `TestDevMode` — API + frontend commands present
- `TestSlateMode` — all 6 slate processes present in commands
- `TestWindowTitles` — "MLB2 API", "MLB2 Frontend", etc.
- `TestDateHandling` — DATE variable set, used in jsonl path + health URL
- `TestNoTradeExecution` — no order/trade/execute commands
- `TestDiscoverCommand` — uses --sport mlb, NOT --all
- `TestRunbookOneClick` — runbook references `dev.bat slate`, lists window names

---

## Step 2 — Rewrite `dev.bat`

```batch
@echo off
setlocal

:: ── root ──────────────────────────────────────────────────────────────────────
set "ROOT=%~dp0"
cd /d "%ROOT%"

:: ── date: accept as 2nd arg or default to today ───────────────────────────────
if not "%~2"=="" (
    set "DATE=%~2"
) else (
    for /f %%D in ('python -c "import datetime; print(datetime.date.today().isoformat())"') do set "DATE=%%D"
)

:: ── mode: first arg; default = dev ────────────────────────────────────────────
set "MODE=%~1"
if /i "%MODE%"=="slate" goto :slate_mode
goto :dev_mode

:: ══════════════════════════════════════════════════════════════════════════════
:dev_mode
echo MLB2 Dev Mode — API + Frontend
echo   API:      http://localhost:8000/docs
echo   Frontend: http://localhost:5173
echo.

start "MLB2 API"      cmd /k "cd /d "%ROOT%" && uvicorn api.main:app --reload --port 8000 || pause"
timeout /t 3 /nobreak >nul

if not exist "%ROOT%frontend\package.json" (
    echo [WARNING] frontend\package.json not found. Skipping frontend window.
    goto :dev_open_browser
)
start "MLB2 Frontend" cmd /k "cd /d "%ROOT%frontend" && npm run dev || pause"

:dev_open_browser
timeout /t 5 /nobreak >nul
start "" "http://localhost:5173"
echo.
echo Dev services launched. Close each window to stop.
goto :eof

:: ══════════════════════════════════════════════════════════════════════════════
:slate_mode
echo MLB2 Live Slate Mode  date=%DATE%
echo.
echo Step 1/2: Running Kalshi Discovery (blocking, wait for it to finish)...
python kalshi_discover.py --sport mlb
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] kalshi_discover failed. Check credentials/network then retry.
    pause
    exit /b 1
)
echo [OK] Discovery complete.
echo.
echo Step 2/2: Starting slate stack...

start "MLB2 API"                cmd /k "cd /d "%ROOT%" && uvicorn api.main:app --reload --port 8000 || pause"
timeout /t 3 /nobreak >nul

if not exist "%ROOT%frontend\package.json" (
    echo [WARNING] frontend\package.json not found. Skipping frontend window.
    goto :slate_after_frontend
)
start "MLB2 Frontend"           cmd /k "cd /d "%ROOT%frontend" && npm run dev || pause"
:slate_after_frontend

start "MLB2 Orderbook Recorder" cmd /k "cd /d "%ROOT%" && python kalshi_orderbook_recorder.py --sport mlb --interval-seconds 10 --duration-minutes 240 --jsonl data/kalshi_orderbook_%DATE%.jsonl --verbose || pause"
start "MLB2 MLB Poller"         cmd /k "cd /d "%ROOT%" && python mlb_poller.py --sport mlb --interval 30 || pause"
start "MLB2 Live Watcher"       cmd /k "cd /d "%ROOT%" && python live_watcher.py --sport mlb --interval 60 || pause"

echo Waiting 10s for API to start then checking slate health...
timeout /t 10 /nobreak >nul
start "MLB2 Slate Health"       cmd /k "cd /d "%ROOT%" && curl -s http://localhost:8000/api/mlb/slate-health?date=%DATE% & echo. & pause"

timeout /t 2 /nobreak >nul
start "" "http://localhost:5173"
start "" "http://localhost:8000/api/mlb/slate-health?date=%DATE%"

echo.
echo MLB2 Slate Stack running — date=%DATE%
echo.
echo   http://localhost:5173
echo   http://localhost:8000/api/mlb/slate-health?date=%DATE%
echo   http://localhost:8000/docs
echo.
echo Windows opened:
echo   MLB2 API
echo   MLB2 Frontend
echo   MLB2 Orderbook Recorder
echo   MLB2 MLB Poller
echo   MLB2 Live Watcher
echo   MLB2 Slate Health
echo.
echo Close each window to stop that service.
goto :eof
```

---

## Step 3 — Update runbook

Prepend a "Quick Start" section to `docs/TOMORROW_SLATE_RUNBOOK.md`:

```markdown
## Quick Start

Run one command from the repo root:

    dev.bat slate 2026-06-15

Or for today's date:

    dev.bat slate

This opens 6 named terminal windows (MLB2 API, MLB2 Frontend, MLB2 Orderbook
Recorder, MLB2 MLB Poller, MLB2 Live Watcher, MLB2 Slate Health) plus two
browser tabs (frontend + health).

Dev-only mode (API + frontend, no live data scripts):

    dev.bat
```

---

## Step 4 — Run all tests, verify

```bash
python -m pytest tests/ -q
```

Expected: 1779+ passing, no regressions.
Dry-check: open dev.bat in a text editor, confirm no typos on key commands.
