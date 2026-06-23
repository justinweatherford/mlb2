@echo off
setlocal

:: ── Root: always run from repo directory ──────────────────────────────────────
set "ROOT=%~dp0"
cd /d "%ROOT%"

:: ── Date: accept as 2nd arg, else default to today (via Python) ───────────────
if not "%~2"=="" (
    set "DATE=%~2"
) else (
    for /f %%D in ('python -c "import datetime; print(datetime.date.today().isoformat())"') do set "DATE=%%D"
)

:: ── Mode: first arg; no arg = dev mode ───────────────────────────────────────
set "MODE=%~1"
if /i "%MODE%"=="slate" goto :slate_mode
goto :dev_mode

:: ══════════════════════════════════════════════════════════════════════════════
:dev_mode
echo MLB2 Dev Mode (API + Frontend only)
echo   API docs: http://localhost:8000/docs
echo   Frontend: http://localhost:5173
echo.
echo Usage for live slate: dev.bat slate [YYYY-MM-DD]
echo.

start "MLB2 API"      cmd /k "cd /d "%ROOT%" && python -m uvicorn api.main:app --reload --port 8000 || pause"
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
echo MLB2 Live Slate Mode -- date=%DATE%
echo Paper/review mode only. No trades are placed automatically.
echo.

echo Step 1/5: Running Kalshi Discovery (blocking)...
python kalshi_discover.py --sport mlb
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] kalshi_discover failed. Check credentials and network, then retry.
    pause
    exit /b 1
)
echo [OK] Discovery complete.
echo.

echo Step 2/5: Fetching weather for date=%DATE% (blocking)...
python weather_auto_fetch.py --date %DATE%
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] weather_auto_fetch failed ^(exit code %errorlevel%^).
    echo   Re-run manually: python weather_auto_fetch.py --date %DATE%
    echo.
    echo Press any key to continue launching the stack anyway,
    echo or close this window to abort and fix weather first.
    pause
) else (
    echo [OK] Weather fetch complete.
)
echo.

echo Step 3/6: Rebuilding 2026 team context (blocking)...
python historical_team_context_preview_v2.py --season 2026 --regular-start 2026-03-27 >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] historical_team_context_preview_v2 failed -- pregame brain may be stale.
    echo   Re-run manually: python historical_team_context_preview_v2.py --season 2026 --regular-start 2026-03-27
    echo.
) else (
    echo [OK] Team context rebuilt.
)
echo.

echo Step 4/6: Seeding today's schedule for date=%DATE% (blocking)...
python seed_tonight.py --date %DATE%
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] seed_tonight failed -- brain scoring may find 0 games.
    echo   Re-run manually: python seed_tonight.py --date %DATE%
    echo.
) else (
    echo [OK] Schedule seeded.
)
echo.

echo Step 5a/6: Scoring pregame brain for date=%DATE% (blocking)...
python score_today_slate.py --date %DATE%
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] score_today_slate failed -- Slate Monitor brain panel will be empty.
    echo   Re-run manually: python score_today_slate.py --date %DATE%
    echo.
) else (
    echo [OK] Pregame brain scored.
)
echo.

echo Step 5b/6: Generating opp_weak pregame report for date=%DATE% (blocking)...
python opp_weak_pregame_report.py --date %DATE% --no-live-fetch
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] opp_weak_pregame_report failed -- Slate Monitor opp_weak panel will be empty.
    echo   Re-run manually: python opp_weak_pregame_report.py --date %DATE%
    echo.
) else (
    echo [OK] Opp weak pregame report generated.
)
echo.

echo Step 6/6: Launching slate stack...
echo.

start "MLB2 API"                cmd /k "cd /d "%ROOT%" && python -m uvicorn api.main:app --reload --port 8000 || pause"
timeout /t 2 /nobreak >nul

if not exist "%ROOT%frontend\package.json" (
    echo [WARNING] frontend\package.json not found. Skipping frontend window.
    goto :slate_after_frontend
)
start "MLB2 Frontend"           cmd /k "cd /d "%ROOT%frontend" && npm run dev || pause"
:slate_after_frontend

start "MLB2 Orderbook Recorder" cmd /k "cd /d "%ROOT%" && python kalshi_orderbook_recorder.py --sport mlb --batch --slate-date %DATE% --interval-seconds 30 --duration-minutes 915 --jsonl data/kalshi_orderbook_%DATE%.jsonl --verbose || pause"
start "MLB2 Kalshi WebSocket"   cmd /k "cd /d "%ROOT%" && python kalshi_ws.py || pause"
start "MLB2 MLB Poller"         cmd /k "cd /d "%ROOT%" && python mlb_poller.py --sport mlb --date %DATE% --interval 30 || pause"
start "MLB2 Focused Tape"       cmd /k "cd /d "%ROOT%" && python focused_tape_watcher.py || pause"
start "MLB2 Live Watcher"       cmd /k "cd /d "%ROOT%" && python live_watcher.py --sport mlb --interval 60 || pause"
start "MLB2 Paper Sync"         cmd /k "cd /d "%ROOT%" && echo [paper_sync] Run once now; re-run after games end. Up+Enter to repeat. && echo. && python paper_sync.py --date %DATE% & echo. & echo [Done -- press Up+Enter to sync again, or close to exit] & pause"
start "MLB2 Health Check"       cmd /k "cd /d "%ROOT%" && python kalshi_snapshot_collection_health.py --slate-date %DATE% && for /l %%%%i in (1,1,1000) do (timeout /t 300 /nobreak >nul && python kalshi_snapshot_collection_health.py --slate-date %DATE%)"
start "MLB2 EV Overlay"         cmd /k "cd /d "%ROOT%" && echo Waiting 60 min before first EV overlay run... && timeout /t 3600 /nobreak >nul && python kalshi_ev_overlay_preview.py --date %DATE% && for /l %%%%i in (1,1,1000) do (timeout /t 1800 /nobreak >nul && python kalshi_ev_overlay_preview.py --date %DATE%)"

echo Waiting 10s for API to start, then opening health check...
timeout /t 10 /nobreak >nul

start "MLB2 Slate Health"       cmd /k "cd /d "%ROOT%" && curl -s "http://localhost:8000/api/mlb/slate-health?date=%DATE%" & echo. & pause"

timeout /t 2 /nobreak >nul
start "" "http://localhost:5173"
start "" "http://localhost:5173/live-dashboard"
start "" "http://localhost:8000/api/mlb/slate-health?date=%DATE%"

echo.
echo =====================================================
echo  MLB2 Slate Stack running -- date=%DATE%
echo.
echo  Frontend:       http://localhost:5173
echo  Live Dashboard: http://localhost:5173/live-dashboard
echo  Slate health:   http://localhost:8000/api/mlb/slate-health?date=%DATE%
echo  API docs:       http://localhost:8000/docs
echo.
echo  Windows open:
echo    MLB2 API
echo    MLB2 Frontend
echo    MLB2 Orderbook Recorder  ^(slate-date=%DATE%, batch, 30s heartbeat, 915 min^)
echo    MLB2 Kalshi WebSocket    ^(live ticker/orderbook updates^)
echo    MLB2 MLB Poller          ^(date=%DATE%, interval=30s^)
echo    MLB2 Focused Tape
echo    MLB2 Live Watcher
echo    MLB2 Paper Sync          ^(one-shot; Up+Enter to re-run^)
echo    MLB2 Health Check        ^(auto-refresh every 5 min^)
echo    MLB2 EV Overlay          ^(first run after 60 min, then every 30 min^)
echo    MLB2 Slate Health
echo.
echo  REMINDER: Orderbook Recorder runs for 915 minutes (12:00-03:00 UTC window).
echo  Start before first pitch; covers pregame + full slate.
echo.
echo  REMINDER: Re-run paper sync in MLB2 Paper Sync window
echo  periodically during games and again after games end.
echo.
echo  Close each window to stop that service.
echo =====================================================
goto :eof
