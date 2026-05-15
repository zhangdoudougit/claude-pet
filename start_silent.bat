@echo off
REM Start Foamo in the background (no terminal window)
cd /d "%~dp0"
start "" pythonw foamo_pet.py
