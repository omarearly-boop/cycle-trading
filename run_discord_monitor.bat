@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ===============================================
echo   Cycles Discord Learning Monitor - Pull + Run
echo ===============================================

echo.
echo [1/3] Pulling latest code from git...
git pull
if errorlevel 1 (
    echo.
    echo *** git pull failed - check your connection / credentials. Aborting. ***
    exit /b 1
)

rem ── Compute today's date (DD-MM-YY) via Python ───────────────────────────────
for /f "tokens=*" %%D in ('python -c "import datetime; print(datetime.date.today().strftime('%%d-%%m-%%y'))"') do set TODAY=%%D

rem ── Args: optional  <messages.json>  <from-date>  <to-date>  ─────────────────
rem    If no dates are given, default to TODAY for both.
set MSGFILE=%~1
set FROMDATE=%~2
set TODATE=%~3
if "%FROMDATE%"=="" set FROMDATE=%TODAY%
if "%TODATE%"=="" set TODATE=%TODAY%

echo    Today:  %TODAY%
echo    Range:  %FROMDATE%  -->  %TODATE%

if not "%MSGFILE%"=="" (
    echo.
    echo [2/3] Processing messages file: %MSGFILE%
    python discord_monitor.py process "%MSGFILE%"
) else (
    echo.
    echo [2/3] No messages file given - using existing pending_lessons.json
)

echo.
echo [3/3] Generating report for %FROMDATE% to %TODATE%...
python discord_monitor.py report --from %FROMDATE% --to %TODATE%

endlocal
