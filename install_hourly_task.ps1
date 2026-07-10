# DEPRECATED — this script was a broken mix of two Task Scheduler APIs and
# never registered the market-hours repetition correctly.
# Use setup_hourly_task.bat instead (run once as Administrator).
# It registers three tasks:
#   CyclesTrading_WatchChecker   — hourly, guarded to Mon-Fri 16:30-23:30
#   CyclesTrading_EveningScan    — Mon-Fri 23:15 full scan
#   CyclesTrading_SundayPipeline — Sunday 08:00 weekly pipeline
Write-Host "This script is deprecated. Run setup_hourly_task.bat as Administrator instead."
