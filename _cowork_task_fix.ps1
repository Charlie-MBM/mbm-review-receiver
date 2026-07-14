# One-shot fix run by Claude (Cowork) 2026-07-10, with Charlie present.
# 1) Log all MBM* scheduled tasks  2) Enable missed-start catch-up + wake on the
# members-export task, preserving its other settings  3) Start it now.
$log = 'C:\Users\charl\GitHub\mbm-review-receiver\_cowork_task_fix.log'
try {
    Get-ScheduledTask | Where-Object { $_.TaskName -like '*MBM*' } |
        Select-Object TaskName, State | Format-Table -AutoSize | Out-String |
        Out-File $log

    $n = 'MBM-Dashboard-Members-Export'
    $t = Get-ScheduledTask -TaskName $n -ErrorAction Stop
    $t.Settings.StartWhenAvailable = $true
    $t.Settings.WakeToRun = $true
    Set-ScheduledTask -TaskName $n -Settings $t.Settings | Out-Null
    "Settings updated: StartWhenAvailable=true, WakeToRun=true" | Out-File $log -Append

    Start-ScheduledTask -TaskName $n
    "Task started at $(Get-Date -Format o)" | Out-File $log -Append

    Start-Sleep -Seconds 5
    $info = Get-ScheduledTaskInfo -TaskName $n
    "State after start: LastRunTime=$($info.LastRunTime) LastTaskResult=$($info.LastTaskResult)" | Out-File $log -Append
    "DONE" | Out-File $log -Append
}
catch {
    "ERROR: $_" | Out-File $log -Append
}
