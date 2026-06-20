@echo off
cd /d "%~dp0"
py cf_t16_finish.py > cf_t16_output.txt 2>&1
echo Done. Output in cf_t16_output.txt
