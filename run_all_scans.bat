@echo off
cd /d "%~dp0"
title Cycles Trading -- Full Sunday Analysis

echo.
echo  ================================================================
echo   CYCLES TRADING -- FULL SUNDAY ANALYSIS
echo   1. Main Scan   (retest setups -- GREEN signals)
echo   2. Momentum    (rally continuation plays)
echo   3. Weekly Review (what did we miss last week?)
echo  ================================================================
echo.

:: Ask portfolio size once
set /p CT_PORTFOLIO_SIZE="  Portfolio size in $ (press ENTER for 25000): "
if "%CT_PORTFOLIO_SIZE%"=="" set CT_PORTFOLIO_SIZE=25000

:: Auto-scan all tickers (no ticker filter)
set CT_TICKER_INPUT=

echo.
echo  Portfolio: $%CT_PORTFOLIO_SIZE%
echo  Starting all 3 scans...
echo.
pause

:: ────────────────────────────────────────────────────────────
echo.
echo  ================================================================
echo   [1/3]  MAIN SCANNER -- Retest Setups
echo  ================================================================
echo.
python cycles_trading_scanner.py
if errorlevel 1 (
    echo.
    echo  [WARNING] Main scanner finished with errors. Continuing...
    echo.
)

:: ────────────────────────────────────────────────────────────
echo.
echo  ================================================================
echo   [2/3]  MOMENTUM SCANNER -- Rally Week Plays  ^(SPY ^>2%%^)
echo  ================================================================
echo.
python cycles_trading_scanner.py momentum
if errorlevel 1 (
    echo.
    echo  [WARNING] Momentum scanner finished with errors. Continuing...
    echo.
)

:: ────────────────────────────────────────────────────────────
echo.
echo  ================================================================
echo   [3/3]  WEEKLY REVIEW -- Missed Opportunities
echo  ================================================================
echo.
python ct_weekly_review.py

:: ────────────────────────────────────────────────────────────
echo.
echo  ================================================================
echo   ALL DONE!
echo.
echo   Reports saved in REPORTS\ folder:
echo     cycles_report_*.html     -- main scan
echo     momentum_report_*.html   -- momentum plays
echo     weekly_review_*.html     -- what we missed
echo.
echo   Your GO candidates are in cycles_report + momentum_report.
echo  ================================================================
echo.
pause
