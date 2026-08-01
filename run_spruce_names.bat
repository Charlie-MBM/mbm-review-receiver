@echo off
REM MBM Spruce contact naming - hourly runner.
REM New consults arrive in Spruce as a bare phone number because the booking
REM flow only ever sends an outbound SMS; the patient name never travels.
REM _fix_spruce_links.py fixes that: --mode name PATCHes givenName/familyName
REM onto the Spruce contact, --mode link POSTs the hint integration link so the
REM contact is tied to the Hint chart. Both are idempotent and skip contacts
REM that already look correct.
REM Run from cmd so the `py` launcher resolves on PATH (a bare `py` as the task
REM action fails with 0x80070002 in the Task Scheduler run context).
cd /d "%~dp0"
echo ====== run %DATE% %TIME% ======>> spruce_names.log
py _fix_spruce_links.py --mode name --apply >> spruce_names.log 2>&1
py _fix_spruce_links.py --mode link --apply >> spruce_names.log 2>&1
