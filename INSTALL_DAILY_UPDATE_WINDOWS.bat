@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Installing the MacroScope daily refresh task for 19:20 local time...
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\install_daily_task.ps1" -ProjectRoot "%CD%"
if errorlevel 1 (
  echo The daily task could not be installed. Try right-clicking this file and choosing Run as administrator.
  pause
  exit /b 1
)
echo Daily refresh installed successfully.
echo It will run every day at 19:20 and also run after a missed schedule when Windows becomes available.
pause
