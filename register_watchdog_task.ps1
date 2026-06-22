# Registers (or updates) the MBM-Poller-Watchdog scheduled task.
# Daily at 6:00pm local. Runs run_poller_watchdog.bat -> check_poller_health.py,
# which texts/emails Charlie (via the daily-summary Spruce/Resend config) if the
# review poller's last_run_at has gone stale (>26h by default). Runs at 6pm so the
# 10am poller (and any StartWhenAvailable catch-up) has had all day to complete.
# Independent of the review poller so it fires even when that poller is dead -
# the failure mode that went unnoticed for ~13 days in June 2026.
# -StartWhenAvailable catches up after the laptop boots.
#
# Run once:  powershell -ExecutionPolicy Bypass -File register_watchdog_task.ps1
#
# ROLLBACK (disable):  schtasks /change /tn "MBM-Poller-Watchdog" /disable
# ROLLBACK (remove):   schtasks /delete  /tn "MBM-Poller-Watchdog" /f

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_poller_watchdog.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Daily -At 6:00pm
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName "MBM-Poller-Watchdog" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Watchdog for MBM-Review-Poller. Alerts Charlie via Spruce SMS / email if the review poller's last_run_at is stale (>26h). Independent task so it fires when the poller is down." `
    -Force

Write-Host "Registered MBM-Poller-Watchdog. Current state:"
schtasks /query /tn "MBM-Poller-Watchdog" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
