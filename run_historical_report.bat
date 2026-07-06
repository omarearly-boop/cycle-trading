@echo off
cd /d "%~dp0"
call run_discord_monitor.bat messages.json 01-04-26 01-07-26
echo.
echo Done. Press any key to close this window.
pause >nul
