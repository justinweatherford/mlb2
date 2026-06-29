@echo off
setlocal

:: ── Root: run from repo directory ────────────────────────────────────────────
set "ROOT=%~dp0"
cd /d "%ROOT%"

:: ── Date ─────────────────────────────────────────────────────────────────────
for /f %%D in ('python -c "import datetime; print(datetime.date.today().isoformat())"') do set "TODAY=%%D"

if not "%~1"=="" (
    set "SLATE_DATE=%~1"
) else (
    set "SLATE_DATE=%TODAY%"
)

echo.
echo =====================================================
echo  Full Slate Orderbook Collection
echo  *** READ-ONLY. No orders. No paper trades. ***
echo.
echo  Slate date : %SLATE_DATE%
echo  Duration   : 915 minutes  (12:00 UTC to 03:00 UTC)
echo  Interval   : 30 seconds
echo  Mode       : batch (100 tickers/call)
echo  Filter     : slate markets for %SLATE_DATE% only
echo.
echo  Usage: RUN_FULL_SLATE_ORDERBOOK.bat [YYYY-MM-DD]
echo         (no argument = today, %TODAY%)
echo.
echo  This window launches:
echo    MLB2 Orderbook [%SLATE_DATE%]  -- the collector
echo    MLB2 Health Check [%SLATE_DATE%]  -- live health, refreshes every 5 min
echo =====================================================
echo.

:: ── Check discovery has been run ─────────────────────────────────────────────
echo Checking kalshi_markets for %SLATE_DATE%...
python check_slate_markets.py %SLATE_DATE%
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Discovery check failed. Run kalshi_discover.py first.
    echo   python kalshi_discover.py --sport mlb
    echo.
    pause
    exit /b 1
)

:: ── Brain scoring (pregame) ───────────────────────────────────────────────────
echo.
echo Scoring pregame brain for %SLATE_DATE%...
python historical_team_context_preview_v2.py --season 2026 --regular-start 2026-03-27 >nul 2>&1
python score_today_slate.py --date %SLATE_DATE%
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Brain scoring failed -- Slate Monitor brain panel will be empty.
    echo   Run manually: python score_today_slate.py --date %SLATE_DATE%
    echo.
)

echo.
echo Starting collector...
echo.

:: ── Full slate orderbook collector ───────────────────────────────────────────
start "MLB2 Orderbook [%SLATE_DATE%]" cmd /k "cd /d "%ROOT%" && echo. && echo [Collector] Slate=%SLATE_DATE%  Duration=915min  Interval=30s && echo. && python kalshi_orderbook_recorder.py --sport mlb --batch --slate-date %SLATE_DATE% --interval-seconds 30 --duration-minutes 915 --jsonl data/kalshi_orderbook_%SLATE_DATE%.jsonl --verbose || (echo. && echo [COLLECTOR STOPPED - check errors above] && pause)"

timeout /t 15 /nobreak >nul

:: ── Health check (auto-refreshing every 5 minutes) ───────────────────────────
start "MLB2 Health Check [%SLATE_DATE%]" cmd /k "cd /d "%ROOT%" && python kalshi_snapshot_collection_health.py --slate-date %SLATE_DATE% && for /l %%%%i in (1,1,1000) do (timeout /t 300 /nobreak >nul && python kalshi_snapshot_collection_health.py --slate-date %SLATE_DATE%)"

:: ── EV overlay (waits 60 min for snapshots, then refreshes every 30 min) ─────
start "MLB2 EV Overlay [%SLATE_DATE%]" cmd /k "cd /d "%ROOT%" && echo Waiting 60 min before first EV overlay run... && timeout /t 3600 /nobreak >nul && python kalshi_ev_overlay_preview.py --date %SLATE_DATE% && for /l %%%%i in (1,1,1000) do (timeout /t 1800 /nobreak >nul && python kalshi_ev_overlay_preview.py --date %SLATE_DATE%)"

echo.
echo =====================================================
echo  Collector running. Windows open:
echo.
echo    "MLB2 Orderbook [%SLATE_DATE%]"
echo       polls every 30s for 915 min (until ~03:00 UTC)
echo       JSONL: data/kalshi_orderbook_%SLATE_DATE%.jsonl
echo.
echo    "MLB2 Health Check [%SLATE_DATE%]"
echo       refreshes every 5 min
echo       outputs: outputs/kalshi_snapshot_collection_health/
echo.
echo    "MLB2 EV Overlay [%SLATE_DATE%]"
echo       first run after 60 min, then every 30 min
echo       outputs: outputs/kalshi_ev_overlay_preview/
echo.
echo  To run EV overlay immediately (skip wait):
echo    python kalshi_ev_overlay_preview.py --date %SLATE_DATE%
echo.
echo  To run health check manually:
echo    python kalshi_snapshot_collection_health.py --slate-date %SLATE_DATE%
echo.
echo  Close "MLB2 Orderbook" window to stop collection.
echo =====================================================
echo.
