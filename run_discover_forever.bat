@echo off
setlocal
:restart
echo [%date% %time%] Running Kalshi market discovery...
python kalshi_discover.py --sport mlb
echo [%date% %time%] Discovery complete. Next run in 2 hours.
timeout /t 7200 /nobreak >nul
goto :restart
