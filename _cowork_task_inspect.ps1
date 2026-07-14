$log = 'C:\Users\charl\GitHub\mbm-review-receiver\_cowork_task_inspect.log'
try {
    $n = 'MBM-Dashboard-Members-Export'
    $t = Get-ScheduledTask -TaskName $n -ErrorAction Stop
    $out = foreach ($a in $t.Actions) {
        "Execute:   $($a.Execute)"
        "Arguments: $($a.Arguments)"
        "WorkDir:   $($a.WorkingDirectory)"
    }
    $out | Out-File $log
    $info = Get-ScheduledTaskInfo -TaskName $n
    "LastRunTime=$($info.LastRunTime) LastTaskResult=$($info.LastTaskResult) ($('0x{0:X8}' -f $info.LastTaskResult))" | Out-File $log -Append
    "DONE" | Out-File $log -Append
}
catch {
    "ERROR: $_" | Out-File $log -Append
}
