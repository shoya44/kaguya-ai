@echo off
setlocal
cd /d "%~dp0"
rem Registers a task that starts Kaguya AI in mini mode on Windows sign-in.
rem No admin rights needed (creates a task in the current user's own session).
set TASK_NAME=KaguyaAI_AutoStart
schtasks /Create /TN "%TASK_NAME%" /TR "\"%~dp0start.bat\" --mini" /SC ONLOGON /RL LIMITED /F
if errorlevel 1 goto failed
echo Registered: KaguyaAI will start minimized on next Windows sign-in.
echo To undo this, run unregister_autostart.bat.
pause
exit /b 0
:failed
echo Failed to register the auto-start task. See the message above.
pause
exit /b 1
