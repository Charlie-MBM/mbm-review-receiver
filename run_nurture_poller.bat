@echo off
REM MBM Nurture Poller — daily runner (task T5b).
REM Sends the Day 0/7/21 nurture text sequence to Spruce contacts tagged
REM `nurture-prospect`. Respects DRY_RUN in .env (set DRY_RUN=false for live).
REM This is the SIBLING of run for the review poller; it never touches the
REM review poller's script, state, or schedule.
cd /d "%~dp0"
py send_nurture_sequence.py >> nurture_cron.log 2>&1
