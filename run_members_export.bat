@echo off
REM MBM Members Export — daily runner for the dashboard feed.
REM READ-ONLY Hint pull -> writes members_feed.json (AGGREGATE COUNTS ONLY, no PHI)
REM to the dashboard artifact folder, which the daily/weekly dashboard tasks read.
REM Mirrors run_review_poller.bat / run_nurture_poller.bat.
REM Run from cmd so the `py` launcher resolves on PATH (a bare `py` as the task
REM action fails with 0x80070002 in the Task Scheduler run context).
cd /d "%~dp0"
py export_dashboard_members.py >> members_export.log 2>&1
