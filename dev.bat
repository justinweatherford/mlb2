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
echo MLB2 Live Slate Mode -- date=%DATE%
echo Paper/review mode only. No trades are placed automatically.
echo.
echo Step 1/2: Running Kalshi Discovery (blocking — wait for it to finish)...
python kalshi_discover.py --sport mlb
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] kalshi_discover failed. Check credentials and network, then retry.
    pause
    exit /b 1
)
echo [OK] Discovery complete.
echo.
echo Step 2/2: Launching slate stack...
echo.

start "MLB2 API"                cmd /k "cd /d "%ROOT%" && uvicorn api.main:app --reload --port 8000 || pause"
timeout /t 2 /nobreak >nul

if not exist "%ROOT%frontend\package.json" (
    echo [WARNING] frontend\package.json not found. Skipping frontend window.
    goto :slate_after_frontend
)
start "MLB2 Frontend"           cmd /k "cd /d "%ROOT%frontend" && npm run dev || pause"
:slate_after_frontend

start "MLB2 Orderbook Recorder" cmd /k "cd /d "%ROOT%" && python kalshi_orderbook_recorder.py --sport mlb --interval-seconds 10 --duration-minutes 240 --jsonl data/kalshi_orderbook_%DATE%.jsonl --verbose || pause"
start "MLB2 MLB Poller"         cmd /k "cd /d "%ROOT%" && python mlb_poller.py --sport mlb --interval 30 || pause"
start "MLB2 Live Watcher"       cmd /k "cd /d "%ROOT%" && python live_watcher.py --sport mlb --interval 60 || pause"

echo Waiting 10s for API to start, then opening slate health...
timeout /t 10 /nobreak >nul

start "MLB2 Slate Health"       cmd /k "cd /d "%ROOT%" && curl -s http://localhost:8000/api/mlb/slate-health?date=%DATE% & echo. & pause"

timeout /t 2 /nobreak >nul
start "" "http://localhost:5173"
start "" "http://localhost:8000/api/mlb/slate-health?date=%DATE%"

echo.
echo =====================================================
echo  MLB2 Slate Stack running -- date=%DATE%
echo.
echo  Frontend:     http://localhost:5173
echo  Slate health: http://localhost:8000/api/mlb/slate-health?date=%DATE%
echo  API docs:     http://localhost:8000/docs
echo.
echo  Windows open:
echo    MLB2 API
echo    MLB2 Frontend
echo    MLB2 Orderbook Recorder
echo    MLB2 MLB Poller
echo    MLB2 Live Watcher
echo    MLB2 Slate Health
echo.
echo  REMINDER: Orderbook Recorder must be running DURING games
echo  for tape context to populate. Runs for 240 minutes.
echo.
echo  Close each window to stop that service.
echo =====================================================
goto :eof
