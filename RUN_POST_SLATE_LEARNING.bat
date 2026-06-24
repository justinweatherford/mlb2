@echo off
cd /d "C:\Users\justi\OneDrive\Desktop\github\mlb2 - Copy"

echo.
echo === POST-SLATE LEARNING PIPELINE ===
echo.

echo [1/4] Polling actuals...
python mlb_poller.py --sport mlb --once
if errorlevel 1 (echo FAILED: mlb_poller.py & pause & exit /b 1)

echo.
echo [2/4] Enriching pregame cards with actuals...
python pregame_actuals_enrichment.py
if errorlevel 1 (echo FAILED: pregame_actuals_enrichment.py & pause & exit /b 1)

echo.
echo [3/4] Updating probability calibration...
python pregame_probability_calibration.py
if errorlevel 1 (echo FAILED: pregame_probability_calibration.py & pause & exit /b 1)

echo.
echo [4/5] Generating daily learning report...
python pregame_daily_learning_report.py
if errorlevel 1 (echo FAILED: pregame_daily_learning_report.py & pause & exit /b 1)

echo.
echo [5/5] Reconciling fill prices and outcomes...
for /f "tokens=*" %%i in ('python -c "import datetime; print(datetime.date.today() - datetime.timedelta(days=1))"') do set SLATE_DATE=%%i
python ev_fill_reconciler.py --date %SLATE_DATE%
if errorlevel 1 (echo WARNING: ev_fill_reconciler.py failed - check shadow log & continue)

echo.
echo === DONE ===
echo Check: outputs\pregame_daily_learning_report\
echo Check: outputs\kalshi_ev_overlay_preview\moneyline_near_miss_history.csv
echo Check: outputs\ev_fill_reconciler\fill_reconciliation_summary.md
echo.
pause
