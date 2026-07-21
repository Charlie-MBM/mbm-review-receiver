@echo off
REM MBM signup-link checker — weekly runner.
REM Verifies every Hint membership-signup URL in nurture_engine.PLAN_SIGNUP_URLS
REM still resolves (HTTP 200). --alert Spruce-texts ALERT_PHONE if any go dead,
REM so a renamed Hint plan slug is caught before a patient hits a broken link.
REM Read-only: loads public signup pages, submits nothing.
REM Run from cmd so the `py` launcher resolves on PATH (a bare `py` as the task
REM action fails with 0x80070002 in the Task Scheduler run context).
cd /d "%~dp0"
py check_signup_links.py --alert >> signup_link_check.log 2>&1
