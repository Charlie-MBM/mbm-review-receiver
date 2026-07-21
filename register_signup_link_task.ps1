# Registers (or updates) the MBM-SignupLink-Check scheduled task.
# Weekly, Monday 8:00am local. Runs run_signup_link_check.bat, which cd's to the
# repo and invokes `py check_signup_links.py --alert` so the launcher resolves on
# PATH. A bare `py` as the task action fails with 0x80070002 ("file not found")
# in the Task Scheduler run context — always run the check via the .bat, never a
# bare `py` action.
# The check imports PLAN_SIGNUP_URLS from nurture_engine (single source of truth)
# and GETs each Hint signup URL; on any dead link it Spruce-texts ALERT_PHONE.
# -StartWhenAvailable catches up after the laptop boots.
#
# Run once:  powershell -ExecutionPolicy Bypass -File register_signup_link_task.ps1
#
# ROLLBACK (disable):  schtasks /change /tn "MBM-SignupLink-Check" /disable
# ROLLBACK (remove):   schtasks /delete  /tn "MBM-SignupLink-Check" /f

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_signup_link_check.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 8:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName "MBM-SignupLink-Check" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "MBM Hint signup-link checker. Weekly (Mon 8am) verifies every membership-signup URL in nurture_engine.PLAN_SIGNUP_URLS still resolves (HTTP 200). Spruce-texts ALERT_PHONE if a slug 404s so the nurture poller never sends a patient a dead enrollment link. Writes signup_link_check.log." `
    -Force

Write-Host "Registered MBM-SignupLink-Check. Current state:"
schtasks /query /tn "MBM-SignupLink-Check" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
