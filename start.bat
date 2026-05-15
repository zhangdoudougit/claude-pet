@echo off
title Foamo Desktop Pet

cd /d "%~dp0"

echo ============================================
echo   Foamo Desktop Pet - Starting
echo ============================================
echo.

echo [1/2] Checking dependencies...
if exist ".deps_installed" goto :start_pet

echo Installing PyQt6 (first run, may take a minute)...
python -m pip install -r requirements.txt
if errorlevel 1 goto :pip_failed
echo done > .deps_installed
echo Done.
echo.

:start_pet
echo [2/2] Starting Foamo...
echo.
echo Look for the bubble character on your desktop.
echo Look for the system tray icon (bottom-right corner).
echo Right-click the character or tray icon for options.
echo.
echo Close this window with Ctrl+C, or quit from the tray menu.
echo ============================================
echo.

python foamo_pet.py

echo.
echo Foamo stopped.
pause
goto :eof

:pip_failed
echo.
echo [ERROR] Failed to install PyQt6.
echo Try manually:
echo   python -m pip install --upgrade pip
echo   python -m pip install PyQt6
echo.
pause
goto :eof
