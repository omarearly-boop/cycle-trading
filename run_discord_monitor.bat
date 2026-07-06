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

rem Usage:
rem   run_discord_monitor.bat                              (report only, default range)
rem   run_discord_monitor.bat messages.json                 (process file, default range)
rem   run_discord_monitor.bat messages.json 01-04-26 01-07-26  (process file, custom range)
rem   run_discord_monitor.bat "" 01-04-26 01-07-26           (report only, custom range)

set MSGFILE=%~1
set FROMDATE=%~2
set TODATE=%~3
if "%FROMDATE%"=="" set FROMDATE=01-04-26
if "%TODATE%"=="" set TODATE=01-07-26

if not "%MSGFILE%"=="" (
    echo.
    echo [2/3] Processing messages file: %MSGFILE%
    python discord_monitor.py process "%MSGFILE%"
) else (
    echo.
    echo [2/3] No messages file given - skipping process step, using existing pending_lessons.json
)

echo.
echo [3/3] Generating report for %FROMDATE% to %TODATE%...
python discord_monitor.py report --from %FROMDATE% --to %TODATE%

endlocal
