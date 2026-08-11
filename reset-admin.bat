@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset-admin.ps1"
if errorlevel 1 (
  echo.
  echo Password reset failed. See the error above.
  pause
  exit /b 1
)
echo.
pause
