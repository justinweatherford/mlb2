@echo off
setlocal
:restart
echo [%date% %time%] Starting MLB Poller...
python mlb_poller.py --sport mlb --interval 30
echo [%date% %time%] Poller stopped (exit code %ERRORLEVEL%). Restarting in 30s...
timeout /t 30 /nobreak >nul
goto :restart
