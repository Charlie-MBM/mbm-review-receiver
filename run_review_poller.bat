@echo off
REM MBM Review Poller — daily runner.
REM Sends review-request SMS to eligible Hint patients via Spruce.
REM Respects DRY_RUN in .env (set DRY_RUN=false for live). Mirrors run_nurture_poller.bat.
REM Run from cmd so the `py` launcher resolves on PATH (a bare `py` as the task
REM action fails with 0x80070002 in the Task Scheduler run context).
cd /d "%~dp0"
py send_review_requests.py >> review_cron.log 2>&1
