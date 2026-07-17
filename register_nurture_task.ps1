# Registers (or updates) the MBM-Nurture-Poller scheduled task.
# Daily at 9:30am local: Day 0 reaches a prospect the morning AFTER their consult
# (the engine never texts the same calendar day as the membership was created).
# Safe to run before the 10:00 review poller because that poller is now member-only,
# so a nurture prospect (a non-member) can never get both on the same day.
# -StartWhenAvailable mirrors the review poller: catches up after the laptop boots.
#
# Run once:  powershell -ExecutionPolicy Bypass -File register_nurture_task.ps1
# Or just double-click install_nurture_task.bat
#
# ROLLBACK (disable in one step):
#   schtasks /change /tn "MBM-Nurture-Poller" /disable
# ROLLBACK (remove entirely):
#   schtasks /delete  /tn "MBM-Nurture-Poller" /f

$ErrorActionPreference = "Stop"
$repo = "C:\Users\charl\GitHub\mbm-review-receiver"
$runner = Join-Path $repo "run_nurture_poller.bat"

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Daily -At 9:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "MBM-Nurture-Poller" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "MBM post-consult nurture sequence (T5b). Day 0/7/21 texts to Spruce nurture-prospect contacts. Sibling of MBM-Review-Poller; does not touch it." `
    -Force

Write-Host "Registered MBM-Nurture-Poller. Current state:"
schtasks /query /tn "MBM-Nurture-Poller" /v /fo LIST | Select-String "TaskName","Next Run Time","Status","Schedule Type","Start Time"
