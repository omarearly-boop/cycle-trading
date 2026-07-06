@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ===============================================
echo   Cycles Trading Scanner - Pull + Run
echo ===============================================

echo.
echo [1/2] Pulling latest code from git...
git pull
if errorlevel 1 (
    echo.
    echo *** git pull failed - check your connection / credentials. Aborting. ***
    goto :end
)

rem Usage:
rem   run_cycles_scanner.bat                 (full scan, all watchlists, $1000 portfolio, non-interactive)
rem   run_cycles_scanner.bat positions       (list open positions)
rem   run_cycles_scanner.bat positions --all (list open + closed positions)
rem   run_cycles_scanner.bat check           (check open positions against current prices)
rem   run_cycles_scanner.bat check --email   (same, plus email a summary)
rem   run_cycles_scanner.bat add TICKER DIR ENTRY STOP TP1 TP2 TP3 UNITS "notes"
rem   run_cycles_scanner.bat close POSITION_ID [exit_price]

echo.
echo [2/2] Running scanner...
if "%~1"=="" (
    rem No args: run the default full scan. Redirect stdin from NUL so the
    rem interactive prompts (portfolio size / specific ticker) fall back to
    rem their defaults ($1000, scan ALL watchlists) instead of hanging.
    python cycles_trading_scanner.py < nul
) else (
    python cycles_trading_scanner.py %*
)

:end
echo.
echo Done. Press any key to close this window.
pause >nul
endlocal
