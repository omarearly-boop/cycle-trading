@echo off
cd /d "%~dp0"

:: ── Sunday pipeline — weekly review + scans (+ monthly S/R on 1st Sunday) ──
:: ct_pipeline.py decides the task list by date. Wrapped in a .bat because
:: inline `cmd /c "cd ... && python ..."` in schtasks got mangled by quote
:: escaping and the task silently never ran.

set CT_PORTFOLIO_SIZE=25000
set CT_TICKER_INPUT=
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs
python ct_pipeline.py >> logs\sunday_pipeline.log 2>&1
