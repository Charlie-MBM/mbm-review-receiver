@echo off
REM MBM Poller Watchdog - alerts Charlie if the review poller has gone stale.
REM Independent of the review poller so it fires even when that poller is dead.
REM Run from cmd so the `py` launcher resolves on PATH (a bare `py` as a task
REM action fails with 0x80070002 in the Task Scheduler run context).
cd /d "%~dp0"
py check_poller_health.py >> watchdog_cron.log 2>&1
