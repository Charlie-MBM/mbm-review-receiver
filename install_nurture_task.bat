@echo off
REM One-click installer for the MBM-Nurture-Poller scheduled task (T5b).
REM Double-click this file once to register the daily 11:00 job.
powershell -ExecutionPolicy Bypass -File "%~dp0register_nurture_task.ps1"
echo.
echo Done. To DISABLE later (one step):  schtasks /change /tn "MBM-Nurture-Poller" /disable
pause
