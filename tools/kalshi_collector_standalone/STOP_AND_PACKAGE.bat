@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Kalshi MLB Standalone Collector - STOP + PACKAGE
echo ============================================
echo.

echo Stopping collector...
taskkill /FI "WINDOWTITLE eq KalshiCollector*" /T /F >nul 2>&1
timeout /t 2 /nobreak >nul

for /f %%D in ('python -c "import datetime; print(datetime.datetime.now().strftime('%%Y-%%m-%%d_%%H%%M'))"') do set "STAMP=%%D"
set "ARCHIVE=kalshi_tape_export_%STAMP%.zip"

echo.
echo Packaging output\ into %ARCHIVE% ...
tar -a -c -f "%ARCHIVE%" output

echo.
echo ============================================
echo  DONE
echo ============================================
echo Upload this file to Google Drive:
echo   %CD%\%ARCHIVE%
echo.
echo The output\ folder itself was NOT deleted -- collected data stays on
echo this machine until you confirm the import worked on the main computer.
echo.
pause
