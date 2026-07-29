@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Kalshi MLB Standalone Collector - START
echo ============================================
echo.
echo Launching collector in its own window (auto-restarts if it crashes,
echo automatically rolls to a new output file each UTC day).
echo.
echo Leave that window running. Do NOT close it by hand -- when you're
echo done, double-click STOP_AND_PACKAGE.bat instead.
echo.

start "KalshiCollector" cmd /k collector_loop.bat

echo Started. This window will close in 5 seconds.
timeout /t 5 /nobreak >nul
