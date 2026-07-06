@echo off
:: Creates a Windows Scheduled Task to run the watch checker daily
:: US market: 16:45 Israel time (after NYSE opens at 16:30)
:: Israel market: 09:45 Israel time (after TASE opens at 09:30)

cd /d "%~dp0"
set SCRIPT=%~dp0run_watch_checker.bat

echo Creating scheduled tasks for watch checker...

:: US / NASDAQ / NYSE -- run at 16:45 daily
schtasks /create /tn "CyclesWatchUS" /tr "%SCRIPT%" /sc daily /st 16:45 /f
echo   Created: CyclesWatchUS at 16:45 (after NYSE open)

:: Israel TASE -- run at 09:45 daily
schtasks /create /tn "CyclesWatchISR" /tr "%SCRIPT%" /sc daily /st 09:45 /f
echo   Created: CyclesWatchISR at 09:45 (after TASE open)

echo.
echo Done! Tasks are active and will run every day.
echo To view tasks: open Task Scheduler and look for CyclesWatch*
echo To remove:     schtasks /delete /tn "CyclesWatchUS" /f
echo                schtasks /delete /tn "CyclesWatchISR" /f
pause
