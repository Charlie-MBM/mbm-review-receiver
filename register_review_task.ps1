# Registers (or updates) the MBM-Review-Poller scheduled task.
# Daily at 10:00am local. Runs run_review_poller.bat, which cd's to the repo and
# invokes `py send_review_requests.py` so the launcher resolves on PATH. A bare
# `py` as the task action fails with 0x80070002 ("file not found") in the Task
# Scheduler run context - that silently killed this poller for ~13 days in June
# 2026. Always run the poller via the .bat, never a bare `py` action.
# -StartWhenAvailable catches up after the laptop boots.
#
# Run once:  powershell -ExecutionPolicy Bypass -File register_review_task.ps1
#
# ROLLBACK (disable):  schtasks /change /tn "MBM-Review-Poller" /disable
# ROLLBACK (remove):   schtasks /delete  /tn "MBM-Review-Poller" /f

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_review_poller.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Daily -At 10:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "MBM-Review-Poller" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "MBM review-request poller. Texts/emails active members a Google review ask after a real member appointment (Hint /appointments trigger; free consult excluded). Writes review_cron.log + patient_state.json." `
    -Force

Write-Host "Registered MBM-Review-Poller. Current state:"
schtasks /query /tn "MBM-Review-Poller" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
