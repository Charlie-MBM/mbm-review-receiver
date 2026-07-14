$log = 'C:\Users\charl\GitHub\mbm-review-receiver\_cowork_task_fix2.log'
try {
    # Reference: how the (working) review poller launches
    $ref = Get-ScheduledTask -TaskName 'MBM-Review-Poller' -ErrorAction SilentlyContinue
    if ($ref) {
        "MBM-Review-Poller action (reference):" | Out-File $log
        foreach ($a in $ref.Actions) {
            "  Execute:   $($a.Execute)"    | Out-File $log -Append
            "  Arguments: $($a.Arguments)"  | Out-File $log -Append
            "  WorkDir:   $($a.WorkingDirectory)" | Out-File $log -Append
        }
    } else { "MBM-Review-Poller not found" | Out-File $log }

    # Locate the Python launcher explicitly
    $py = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
    if (-not $py) {
        foreach ($c in @("$env:WINDIR\py.exe", "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe")) {
            if (Test-Path $c) { $py = $c; break }
        }
    }
    if (-not $py) { throw "py.exe not found anywhere" }
    "Resolved py.exe: $py" | Out-File $log -Append

    $n = 'MBM-Dashboard-Members-Export'
    $action = New-ScheduledTaskAction -Execute $py `
        -Argument 'C:\Users\charl\GitHub\mbm-review-receiver\export_dashboard_members.py' `
        -WorkingDirectory 'C:\Users\charl\GitHub\mbm-review-receiver'
    Set-ScheduledTask -TaskName $n -Action $action | Out-Null
    "Action updated: full py path + working directory set" | Out-File $log -Append

    Start-ScheduledTask -TaskName $n
    "Task started at $(Get-Date -Format o)" | Out-File $log -Append
    Start-Sleep -Seconds 25
    $info = Get-ScheduledTaskInfo -TaskName $n
    "After 25s: LastRunTime=$($info.LastRunTime) LastTaskResult=$($info.LastTaskResult) ($('0x{0:X8}' -f $info.LastTaskResult))" | Out-File $log -Append
    "(0x00041301 = still running, 0x0 = success)" | Out-File $log -Append
    "DONE" | Out-File $log -Append
}
catch {
    "ERROR: $_" | Out-File $log -Append
}
