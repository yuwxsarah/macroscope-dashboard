@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Installing MacroScope weekday updates for 11:40, 15:20, 16:40 and 19:20 local time...
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\install_daily_task.ps1" -ProjectRoot "%CD%"
if errorlevel 1 (
  echo The daily task could not be installed. Try right-clicking this file and choosing Run as administrator.
  pause
  exit /b 1
)
echo Automatic refresh tasks installed successfully.
echo They run on weekdays at 11:40, 15:20, 16:40 and 19:20, including catch-up after a missed schedule.
pause
