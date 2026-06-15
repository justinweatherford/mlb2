@echo off
setlocal

:: ── Root: run from repo directory ─────────────────────────────────────────────
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo =====================================================
echo  Kalshi Executed-Trade Capture (one-shot)
echo  Capture-only. No trades placed. No scoring changes.
echo =====================================================
echo.
echo Fetching executed trades for all open MLB markets...
echo Press Up+Enter to re-run after games progress.
echo.

python fetch_trades_once.py --verbose %*

echo.
echo [Done] Press any key to close, or Up+Enter to run again.
pause
