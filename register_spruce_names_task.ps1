# Registers (or updates) the MBM-Spruce-Contact-Names scheduled task.
# Hourly. Runs run_spruce_names.bat, which cd's to the repo and invokes
# `py _fix_spruce_links.py` twice (--mode name, then --mode link) with --apply.
# A bare `py` as the task action fails with 0x80070002 ("file not found") in the
# Task Scheduler run context - always run via the .bat, never a bare `py` action.
#
# Why: the booking flow creates a NAMED patient in Hint but only ever sends
# Spruce an SMS addressed to a phone number, so Spruce auto-creates the contact
# with no name and staff cannot search for the person. This sweep names them.
# Both modes are idempotent and skip contacts that already look correct.
# -StartWhenAvailable catches up after the laptop boots.
#
# DRY RUN FIRST:  py _fix_spruce_links.py --mode name
# Run once:  powershell -ExecutionPolicy Bypass -File register_spruce_names_task.ps1
#
# ROLLBACK (disable):  schtasks /change /tn "MBM-Spruce-Contact-Names" /disable
# ROLLBACK (remove):   schtasks /delete  /tn "MBM-Spruce-Contact-Names" /f

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_spruce_names.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Once -At 7:20am -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName "MBM-Spruce-Contact-Names" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "MBM Spruce contact naming. Hourly sweep: names Spruce contacts that arrived as bare phone numbers from the booking flow (givenName/familyName from the matching Hint chart) and writes the hint integration link. Idempotent; skips contacts that already have a real name or an ambiguous phone match. Writes spruce_names.log." `
    -Force

Write-Host "Registered MBM-Spruce-Contact-Names. Current state:"
schtasks /query /tn "MBM-Spruce-Contact-Names" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
