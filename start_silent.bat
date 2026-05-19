@echo off
REM Start Claude Pet in the background (no terminal window)
cd /d "%~dp0"
start "" pythonw claude_pet.py
