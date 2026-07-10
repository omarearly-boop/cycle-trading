@echo off
cd /d "%~dp0"

:: ── Evening FULL SCAN — once daily Mon-Fri at 23:15 (after US close) ──
:: Cycles Report + Momentum + Watch Checker on final weekly-bar data of the day.
:: Sunday's full pipeline (weekly review, monthly S/R) runs via ct_pipeline.py.

set CT_PORTFOLIO_SIZE=25000
set CT_TICKER_INPUT=
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs

:: Weekday guard (skip Sat/Sun — Sunday pipeline covers the weekend)
python -c "import datetime,sys; sys.exit(0 if datetime.datetime.now().weekday()<5 else 1)"
if errorlevel 1 (
    echo [%date% %time%] Skipped — weekend >> logs\evening_scan.log 2>&1
    exit /b 0
)

echo [%date% %time%] ===== EVENING SCAN START ===== >> logs\evening_scan.log 2>&1

python cycles_trading_scanner.py          >> logs\evening_scan.log 2>&1
python cycles_trading_scanner.py momentum >> logs\evening_scan.log 2>&1
python ct_watch_checker.py                >> logs\evening_scan.log 2>&1

:: Calibration tracker — ingest today's signals, resolve old ones, report
python ct_calibration.py                  >> logs\evening_scan.log 2>&1

echo [%date% %time%] ===== EVENING SCAN DONE  ===== >> logs\evening_scan.log 2>&1
echo. >> logs\evening_scan.log 2>&1
