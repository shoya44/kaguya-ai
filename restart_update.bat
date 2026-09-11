@echo off
setlocal
cd /d "%~dp0"
set "WAIT_PID=%~1"

echo Waiting for Kaguya AI to close...
if defined WAIT_PID powershell.exe -NoProfile -Command "Wait-Process -Id %WAIT_PID% -ErrorAction SilentlyContinue"

echo Restarting Kaguya AI with the latest repository state...
call "%~dp0start.bat"
exit /b %errorlevel%
