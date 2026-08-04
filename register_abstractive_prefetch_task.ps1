# Registers (or updates) the MBM-Abstractive-Prefetch scheduled task.
# Daily at 7:00pm local: pulls outside records for every patient on TOMORROW's
# Hint schedule so they're waiting in Drive before the visit.
#
# Runs via run_abstractive_prefetch.bat - never a bare `py` action. A bare `py`
# fails with 0x80070002 in the Task Scheduler run context (that silently killed
# the review poller for ~13 days in June 2026).
#
# Execution time limit is 3 hours, not 30 minutes: each patient takes ~5 minutes
# of polling plus a 60s pause, so a heavy night (first run pulls everyone) can
# legitimately run over an hour.
#
# Run once:  powershell -ExecutionPolicy Bypass -File register_abstractive_prefetch_task.ps1
#
# ROLLBACK (disable):  schtasks /change /tn "MBM-Abstractive-Prefetch" /disable
# ROLLBACK (remove):   schtasks /delete  /tn "MBM-Abstractive-Prefetch" /f
# KILL SWITCH:         set ABSTRACTIVE_PREFETCH_ENABLED=false in .env

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_abstractive_prefetch.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Daily -At 7:00pm
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask -TaskName "MBM-Abstractive-Prefetch" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Pre-visit outside-records pull. For each patient on tomorrow's Hint schedule, searches Carequality via Abstractive and writes the summary, source-document ZIP and a paste-ready .md into the patient's Drive folder. Skips anyone pulled in the last 30 days. Gated by ABSTRACTIVE_ENABLED + ABSTRACTIVE_PREFETCH_ENABLED. Writes abstractive_prefetch.log (counts + hashed ids only, no PHI)." `
    -Force

Write-Host "Registered MBM-Abstractive-Prefetch. Current state:"
schtasks /query /tn "MBM-Abstractive-Prefetch" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
