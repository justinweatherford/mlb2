@echo off
setlocal

:: ── MLB2 Collection-Only Launcher ────────────────────────────────────────────
:: Run this on a dedicated collection machine.
::
:: What it starts:
::   1. Kalshi Market Discovery (runs every 2h to register new game markets)
::   2. Kalshi Orderbook Recorder (auto-restarts on crash)
::   3. MLB Stats Poller (game outcomes for grading, auto-restarts on crash)
::
:: First-time setup on a new machine:
::   1. Copy .env from your main machine (KALSHI_API_KEY_ID + KALSHI_API_PRIVATE_KEY)
::   2. pip install -r requirements.txt
::   3. python -c "from db.schema import init_db; init_db('kalshi_mlb.db')"
::   4. Run this file.
::
:: Maintenance:
::   - Run prune_snapshots.py --execute weekly to keep DB under 5 GB
::   - On the main analysis machine, copy kalshi_mlb.db over periodically
::
:: Does NOT start: API server, frontend, EV overlay, health check, WebSocket.
:: Run dev.bat on your main machine for full analysis stack.

set ROOT=%~dp0

echo.
echo ========================================================
echo  MLB2 Collection Stack
echo ========================================================
echo.

:: ── Step 1: Init DB schema (safe to run repeatedly) ───────────────────────────
echo Initializing DB schema...
python -c "from db.schema import init_db; init_db('kalshi_mlb.db')" || echo [WARNING] DB init failed - check Python/deps

:: ── Step 2: Run discovery once now (populates kalshi_markets for today) ────────
echo Running initial market discovery...
python kalshi_discover.py --sport mlb || echo [WARNING] Discovery failed - recorder will retry on first 2h cycle
echo.

:: ── Step 3: Launch the three collection processes ─────────────────────────────
echo Starting collection processes...
echo.

start "MLB2 Market Discovery"    cmd /k "cd /d "%ROOT%" && run_discover_forever.bat"
timeout /t 2 /nobreak >nul
start "MLB2 Orderbook Recorder"  cmd /k "cd /d "%ROOT%" && run_recorder_forever.bat"
timeout /t 2 /nobreak >nul
start "MLB2 MLB Poller"          cmd /k "cd /d "%ROOT%" && run_mlb_poller_forever.bat"

echo.
echo All three collectors launched in separate windows.
echo This window can be closed.
echo.
echo REMINDER: Run prune_snapshots.py --execute once a week to keep DB small.
echo.
pause
