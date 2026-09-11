@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0restart_update.ps1" -WaitPid %~1
exit /b %errorlevel%
