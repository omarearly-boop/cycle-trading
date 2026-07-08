@echo off
cd /d "%~dp0"
echo.
echo  ============================================
echo   Cycles Trading -- MOMENTUM MODE
echo   Finds trending stocks in a rally week
echo  ============================================
echo.
python cycles_trading_scanner.py momentum
echo.
pause
