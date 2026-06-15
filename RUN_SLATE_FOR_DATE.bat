@echo off
setlocal

:: ── Root: run from repo directory ────────────────────────────────────────────
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo =====================================================
echo  MLB/Kalshi Slate Launcher (Manual Date)
echo  Paper/review mode only. No trades placed.
echo =====================================================
echo.

:prompt
set "SLATE_DATE="
set /p "SLATE_DATE=  Enter slate date (YYYY-MM-DD): "

if "%SLATE_DATE%"=="" (
    echo  [ERROR] Date cannot be blank. Please enter a date like 2026-06-15.
    echo.
    goto :prompt
)

echo.
echo  Starting MLB/Kalshi slate stack for %SLATE_DATE%
echo.

call "%ROOT%dev.bat" slate %SLATE_DATE%
