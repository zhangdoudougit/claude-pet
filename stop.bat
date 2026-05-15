@echo off
chcp 65001 >nul
title 关闭泡沫桌面陪伴

echo.
echo === 关闭泡沫 ===
echo.

REM 只杀 python/pythonw 进程, 且命令行包含 foamo 相关脚本
REM 这样不会误伤 bash / cmd / 其他 IDE 里的 python
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and ($_.CommandLine -like '*foamo_pet.py*' -or $_.CommandLine -like '*chat_window.py*' -or $_.CommandLine -like '*permission_dialog.py*' -or $_.CommandLine -like '*mcp_manager.py*') } | ForEach-Object { Write-Host '  killing PID' $_.ProcessId '-' $_.Name; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo.
echo done.
echo.
timeout /t 2 >nul
exit
