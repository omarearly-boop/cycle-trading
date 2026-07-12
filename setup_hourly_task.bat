@echo off
:: ── Register Cycles Trading scheduled tasks ──
:: Run ONCE as Administrator (right-click → Run as administrator).
:: Replaces the old 24/7 hourly full-scan task with three sane tasks:
::   1. Watch Checker  — hourly at :30, guarded to Mon-Fri 16:30-23:30 Israel
::                       (US market hours). Light: checks watchlist only.
::   2. Evening Scan   — Mon-Fri 23:15, full Cycles + Momentum + Checker
::                       (after US close, on final weekly-bar data).
::   3. Sunday Pipeline — Sunday 08:00, weekly review + monthly S/R + scans
::                       (ct_pipeline.py decides by date).

set BAT_DIR=%~dp0

echo.
echo === Removing old tasks ===
schtasks /delete /tn "CyclesTrading_HourlyScan"     /f >nul 2>&1
schtasks /delete /tn "CyclesTrading-HourlyScan"     /f >nul 2>&1
schtasks /delete /tn "CyclesTrading_WatchChecker"   /f >nul 2>&1
schtasks /delete /tn "CyclesTrading_EveningScan"    /f >nul 2>&1
schtasks /delete /tn "CyclesTrading_SundayPipeline" /f >nul 2>&1

echo.
echo === 1/3 Watch Checker (hourly, guarded to US session) ===
schtasks /create ^
  /tn "CyclesTrading_WatchChecker" ^
  /tr "cmd /c \"%BAT_DIR%run_hourly_scan.bat\"" ^
  /sc MINUTE /mo 60 /st 16:30 /f
if errorlevel 1 goto :err

echo.
echo === 2/3 Evening Full Scan (Mon-Fri 23:15) ===
schtasks /create ^
  /tn "CyclesTrading_EveningScan" ^
  /tr "cmd /c \"%BAT_DIR%run_evening_scan.bat\"" ^
  /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 23:15 /f
if errorlevel 1 goto :err

echo.
echo === 3/3 Sunday Pipeline (Sunday 08:00) ===
schtasks /create ^
  /tn "CyclesTrading_SundayPipeline" ^
  /tr "cmd /c \"%BAT_DIR%run_sunday_pipeline.bat\"" ^
  /sc WEEKLY /d SUN /st 08:00 /f
if errorlevel 1 goto :err

echo.
echo === Wake-from-sleep so scans run even if the PC is asleep ===
:: 1. Allow wake timers in the active power plan
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETACTIVE SCHEME_CURRENT
:: 2. Wake-to-run on the two scans only. NOT the watch checker — its trigger
::    fires hourly around the clock (the in-bat guard skips off-hours), so
::    wake-to-run there would wake the PC every hour all night.
::    Note: wakes from Sleep/Hibernate only — a shut-down PC stays off.
powershell -NoProfile -Command ^
  "$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;" ^
  "Set-ScheduledTask -TaskName 'CyclesTrading_EveningScan'    -Settings $s | Out-Null;" ^
  "Set-ScheduledTask -TaskName 'CyclesTrading_SundayPipeline' -Settings $s | Out-Null;" ^
  "Write-Host '  Wake-to-run enabled on EveningScan + SundayPipeline'"
if errorlevel 1 echo   WARNING: wake-to-run not applied (tasks still run when PC is awake)

echo.
echo SUCCESS! Three tasks registered:
schtasks /query /tn "CyclesTrading_WatchChecker"   | findstr /i "CyclesTrading Ready Running"
schtasks /query /tn "CyclesTrading_EveningScan"    | findstr /i "CyclesTrading Ready Running"
schtasks /query /tn "CyclesTrading_SundayPipeline" | findstr /i "CyclesTrading Ready Running"
echo.
pause
exit /b 0

:err
echo.
echo ERROR: Failed to create task. Run this file as Administrator.
pause
exit /b 1
