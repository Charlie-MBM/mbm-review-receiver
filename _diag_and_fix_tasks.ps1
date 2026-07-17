# MBM scheduled-task diagnostic + optional fix.
#
# WHY: On 2026-07-15 every MBM poller silently missed its run (review, nurture,
# watchdog), and MBM-Dashboard-Members-Export has no log at all — suggesting it
# was never registered. All four tasks were registered with -StartWhenAvailable
# but WITHOUT -WakeToRun, so a sleeping laptop at 9:15-10:00am means no run.
#
# USAGE
#   Diagnose only (safe, read-only, DEFAULT):
#     powershell -ExecutionPolicy Bypass -File _diag_and_fix_tasks.ps1
#
#   Apply fixes (adds -WakeToRun to existing tasks; registers the members export
#   if missing). Re-run the diagnostic afterwards to confirm:
#     powershell -ExecutionPolicy Bypass -File _diag_and_fix_tasks.ps1 -Fix
#
# The -Fix path only touches SETTINGS (wake/start-when-available) and the
# missing members-export registration. It does not change triggers, actions,
# or any poller logic.

param([switch]$Fix)

$ErrorActionPreference = "Continue"
$repo  = "C:\Users\charl\GitHub\mbm-review-receiver"
$tasks = @(
    "MBM-Review-Poller",
    "MBM-Nurture-Poller",
    "MBM-Poller-Watchdog",
    "MBM-Dashboard-Members-Export"
)

Write-Host "=========================================================="
Write-Host " MBM SCHEDULED TASK DIAGNOSTIC   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "=========================================================="

foreach ($name in $tasks) {
    Write-Host ""
    Write-Host "--- $name" -ForegroundColor Cyan
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "  STATUS      : *** NOT REGISTERED ***" -ForegroundColor Red
        continue
    }
    $i = Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
    $lastResult = if ($i) { "0x{0:X}" -f $i.LastTaskResult } else { "?" }
    Write-Host "  State       : $($t.State)"
    Write-Host "  Last run    : $($i.LastRunTime)"
    Write-Host "  Last result : $lastResult   (0x0 = success, 0x80070002 = file not found)"
    Write-Host "  Next run    : $($i.NextRunTime)"
    Write-Host "  WakeToRun          : $($t.Settings.WakeToRun)"
    Write-Host "  StartWhenAvailable : $($t.Settings.StartWhenAvailable)"
    Write-Host "  Action      : $($t.Actions[0].Execute)"
}

Write-Host ""
Write-Host "--- Power / wake-timer config"
powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>$null | Select-String "Current AC Power Setting Index"
Write-Host "  (0x0 = wake timers DISABLED -> WakeToRun will not work on AC)"

if (-not $Fix) {
    Write-Host ""
    Write-Host "Diagnostic only. Re-run with -Fix to apply changes." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "=========================================================="
Write-Host " APPLYING FIXES"
Write-Host "=========================================================="

# 1) Register the members export if it is missing.
if (-not (Get-ScheduledTask -TaskName "MBM-Dashboard-Members-Export" -ErrorAction SilentlyContinue)) {
    Write-Host "Registering MBM-Dashboard-Members-Export (was missing)..." -ForegroundColor Green
    & powershell -ExecutionPolicy Bypass -File (Join-Path $repo "register_members_export_task.ps1")
}

# 2) Add WakeToRun to every task that has it off.
foreach ($name in $tasks) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "SKIP $name (not registered)" -ForegroundColor Red; continue }
    if ($t.Settings.WakeToRun -and $t.Settings.StartWhenAvailable) {
        Write-Host "OK   $name (wake already on)"
        continue
    }
    $t.Settings.WakeToRun          = $true
    $t.Settings.StartWhenAvailable = $true
    Set-ScheduledTask -TaskName $name -Settings $t.Settings | Out-Null
    Write-Host "FIXED $name -> WakeToRun=True, StartWhenAvailable=True" -ForegroundColor Green
}

# 3) Allow wake timers so WakeToRun can actually fire.
Write-Host "Enabling wake timers (AC + battery)..."
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setactive SCHEME_CURRENT

Write-Host ""
Write-Host "Done. Re-run without -Fix to confirm the new state." -ForegroundColor Green
