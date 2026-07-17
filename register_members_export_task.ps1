# Registers (or updates) the MBM-Dashboard-Members-Export scheduled task.
# Daily at 9:15am local: refreshes members_feed.json (the dashboard's Hint feed)
# BEFORE the MBM dashboard daily-fast Cowork task reads it at 9:30am, so member
# and consult counts stay current instead of drifting. Until this task existed the
# feed only updated when the script was run by hand (it went 9 days stale in June 2026).
#
# Runs run_members_export.bat, which cd's to the repo and invokes
# `py export_dashboard_members.py` so the launcher resolves on PATH. A bare `py`
# as the task action fails with 0x80070002 ("file not found") in the Task Scheduler
# run context — that silently killed the review poller for ~13 days in June 2026.
# Always run via the .bat, never a bare `py` action.
# -StartWhenAvailable catches up after the laptop boots (the usual cause of a miss).
#
# The export is READ-ONLY (HTTP GETs to Hint) and writes AGGREGATE COUNTS ONLY —
# no patient names, no PHI. The Hint key stays in this repo's .env on this laptop.
#
# Run once:  powershell -ExecutionPolicy Bypass -File register_members_export_task.ps1
#
# ROLLBACK (disable):  schtasks /change /tn "MBM-Dashboard-Members-Export" /disable
# ROLLBACK (remove):   schtasks /delete  /tn "MBM-Dashboard-Members-Export" /f

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_members_export.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Daily -At 9:15am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "MBM-Dashboard-Members-Export" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "MBM dashboard members feed. READ-ONLY Hint pull of this-month new memberships (paid vs pending) + lead source + consults; writes members_feed.json (aggregate counts only, no PHI) to the dashboard artifact folder. Runs at 9:15am so the 9:30am dashboard daily-fast task reads fresh data. Writes members_export.log." `
    -Force

Write-Host "Registered MBM-Dashboard-Members-Export. Current state:"
schtasks /query /tn "MBM-Dashboard-Members-Export" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
