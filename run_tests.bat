@echo off
cd /d "%~dp0"
:: Regression suite — run after ANY code change. All offline, ~5 seconds.
:: If a change was INTENDED and checks fail, re-baseline with:
::   python run_regression_tests.py --update
python run_regression_tests.py
pause
