@echo off
cd /d "%~dp0"
py abstractive_prefetch.py >> abstractive_prefetch_task.log 2>&1
