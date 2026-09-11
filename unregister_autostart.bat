@echo off
setlocal
rem Removes the auto-start task created by register_autostart.bat.
set TASK_NAME=KaguyaAI_AutoStart
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
  echo No auto-start task found, or it could not be removed.
) else (
  echo Removed: KaguyaAI will no longer start automatically at sign-in.
)
pause
