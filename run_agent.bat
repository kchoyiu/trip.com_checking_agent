@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
python main.py >> logs\agent.log 2>&1

