@echo off
setlocal

:: ── Root: run from repo directory ────────────────────────────────────────────
set "ROOT=%~dp0"
cd /d "%ROOT%"

:: ── Today's date via Python (reliable YYYY-MM-DD on all locales) ─────────────
for /f %%D in ('python -c "import datetime; print(datetime.date.today().isoformat())"') do set "TODAY=%%D"

echo.
echo =====================================================
echo  MLB/Kalshi Slate Launcher
echo  Paper/review mode only. No trades placed.
echo =====================================================
echo.
echo  Detected today's date: %TODAY%
echo.

set "SLATE_DATE="
set /p "SLATE_DATE=  Press Enter to use %TODAY%, or type another date (YYYY-MM-DD): "

if "%SLATE_DATE%"=="" set "SLATE_DATE=%TODAY%"

echo.
echo  Starting MLB/Kalshi slate stack for %SLATE_DATE%
echo.

call "%ROOT%dev.bat" slate %SLATE_DATE%
