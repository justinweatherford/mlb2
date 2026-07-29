@echo off
setlocal
cd /d "%~dp0"

:restart
echo [%date% %time%] Starting collector...
python collector.py --interval-seconds 30
echo [%date% %time%] Collector stopped (exit code %ERRORLEVEL%^). Restarting in 15s...
timeout /t 15 /nobreak >nul
goto :restart
