@echo off
cd /d "%~dp0"
echo.
echo  ============================================
echo   Cycles Trading -- MONTHLY SCAN
echo   Finds stocks near monthly S/R levels
echo   Auto-adds to watchlist with MONTHLY tag
echo  ============================================
echo.
python cycles_trading_scanner.py monthly %*
echo.
pause
