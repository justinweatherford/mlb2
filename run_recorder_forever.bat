@echo off
setlocal
:restart
echo [%date% %time%] Starting Kalshi Orderbook Recorder...
python kalshi_orderbook_recorder.py --sport mlb --batch --interval-seconds 30 --verbose
echo [%date% %time%] Recorder stopped (exit code %ERRORLEVEL%). Restarting in 30s...
timeout /t 30 /nobreak >nul
goto :restart
