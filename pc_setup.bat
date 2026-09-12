@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0tools\pc_setup.ps1"
if errorlevel 1 echo Setup failed. See the message above.
pause
