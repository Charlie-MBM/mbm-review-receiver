@echo off
REM ============================================================
REM  MBM weekly archive digest - runner (Monday scheduled task).
REM  Emails James + Charlie the records ready to archive by hand in Hint.
REM  LOCAL + BAA-safe: reads the two archive-queue CSVs, sends via Gmail SMTP.
REM  Nothing routes through Cowork/Anthropic. No email on an empty week.
REM ============================================================
set HINT_ENV=production
cd /d "%~dp0"

set "PYEXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do set "PYEXE=%%D\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo ====== archive-digest run %DATE% %TIME% ======>> "%~dp0archive_digest.log"
"%PYEXE%" "%~dp0send_archive_digest.py">> "%~dp0archive_digest.log" 2>&1
