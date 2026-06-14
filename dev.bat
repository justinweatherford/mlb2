@echo off
set "ROOT=%~dp0"
cd /d "%ROOT%"
echo Starting MLB Kalshi dev services...

start "kalshi-discover"  cmd /k "cd /d "%ROOT%" && python kalshi_discover.py --all && echo [done] kalshi-discover finished - you can close this window"
start "kalshi-ws"        cmd /k "cd /d "%ROOT%" && python kalshi_ws.py"
start "mlb-poller"       cmd /k "cd /d "%ROOT%" && python mlb_poller.py --sport mlb --interval 30"
start "live-watcher"     cmd /k "cd /d "%ROOT%" && python live_watcher.py --sport mlb --interval 60"
start "api"              cmd /k "cd /d "%ROOT%" && python -m uvicorn api.main:app --reload --port 8000"
start "frontend"         cmd /k "cd /d "%ROOT%frontend" && npm run dev"

echo.
echo All 6 services launched in separate windows.
echo Close each window to stop that service.
