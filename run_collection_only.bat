@echo off
setlocal

:: ── MLB2 Collection-Only Launcher ────────────────────────────────────────────
:: Run this on a dedicated collection machine.
:: Starts only the two processes needed for continuous data collection:
::   1. Kalshi Orderbook Recorder (auto-restarts on crash)
::   2. MLB Stats Poller (game outcomes for grading)
::
:: Does NOT start: API server, frontend, EV overlay, health check, WebSocket.
:: Run dev.bat on your main machine for full analysis stack.

set ROOT=%~dp0

echo Starting MLB2 collection stack (recorder + poller)...
echo.
echo Logs: watch the two windows for errors.
echo To stop: close both windows (or Ctrl+C in each).
echo.

start "MLB2 Orderbook Recorder" cmd /k "cd /d "%ROOT%" && run_recorder_forever.bat"
timeout /t 3 /nobreak >nul
start "MLB2 MLB Poller"         cmd /k "cd /d "%ROOT%" && run_mlb_poller_forever.bat"

echo.
echo Both collectors launched. This window can be closed.
pause
