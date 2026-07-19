@echo off
cd /d "%~dp0"

:: ── Hourly WATCH CHECKER — US market hours only (Israel time 16:30-23:30) ──
:: The full Cycles/Momentum scans moved to run_evening_scan.bat (once daily)
:: and the Sunday pipeline. Running full scans hourly burned Yahoo's rate
:: limit and is unnecessary for a weekly-timeframe methodology.

set CT_PORTFOLIO_SIZE=25000
set CT_TICKER_INPUT=ALL
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs

:: Market-hours guard: Mon-Fri, 16:00-23:59 Israel time only
python -c "import datetime,sys; n=datetime.datetime.now(); sys.exit(0 if (n.weekday()<5 and 16<=n.hour<=23) else 1)"
if errorlevel 1 (
    echo [%date% %time%] Skipped — outside US market hours >> logs\hourly_scan.log 2>&1
    exit /b 0
)

echo [%date% %time%] ===== HOURLY WATCH CHECK START ===== >> logs\hourly_scan.log 2>&1

:: Housekeeping: trim reports older than 48h and keep the log under 5 MB
python -c "import time; from pathlib import Path; base=Path('.'); now=time.time(); rep=base/'REPORTS'; [f.unlink() for f in rep.iterdir() if f.suffix in ('.html','.csv','.pine') and now-f.stat().st_mtime>172800] if rep.exists() else None; lf=base/'logs'/'hourly_scan.log'; lines=lf.read_text(encoding='utf-8',errors='replace').splitlines() if lf.exists() and lf.stat().st_size>5*1024*1024 else None; lf.write_text(chr(10).join(lines[-2000:])+chr(10),encoding='utf-8') if lines else None" >> logs\hourly_scan.log 2>&1

:: Watch checker — sends GO email when a watchlist ticker hits its level
for /f %%t in ('python -c "import time;print(int(time.time()))"') do set CT_T0=%%t
python ct_watch_checker.py >> logs\hourly_scan.log 2>&1
set CT_RC=%ERRORLEVEL%
python ct_record_run.py daily "Watch Checker" %CT_T0% %CT_RC% >> logs\hourly_scan.log 2>&1

echo [%date% %time%] ===== HOURLY WATCH CHECK DONE  ===== >> logs\hourly_scan.log 2>&1
echo. >> logs\hourly_scan.log 2>&1

:: NOTE: the old hourly git auto-commit/push was removed. watch_alerts.json is
:: gitignored so it never actually committed anything, but a git push here
:: could accidentally publish unrelated staged work. Commit manually instead.
