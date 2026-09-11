@echo off
setlocal
cd /d "%~dp0"
rem Arguments are forwarded to app.exe as-is (e.g. start.bat --mini, used by
rem the sign-in auto-start task to launch straight into mini mode; see
rem register_autostart.bat).
if exist "frontend\src-tauri\target\debug\app.exe" (
  powershell.exe -NoProfile -Command "$p = Get-Process app -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path (Get-Location) 'frontend\src-tauri\target\debug\app.exe') }; if ($p) { exit 10 }"
  if errorlevel 10 (
    start "" "frontend\src-tauri\target\debug\app.exe" %*
    exit /b 0
  )
)

rem When starting from a stopped state, safely fast-forward main before build.
rem If the repository changes, restart this batch once so an updated start.bat
rem is also used. Offline/dirty/non-main states only skip update; startup continues.
if defined KAGUYA_UPDATE_CHECKED goto after_update
call "%~dp0update_repo.bat"
set "UPDATE_RESULT=%ERRORLEVEL%"
if not "%UPDATE_RESULT%"=="5" goto after_update
set "KAGUYA_UPDATE_CHECKED=1"
call "%~f0" %*
exit /b

:after_update
echo Preparing Kaguya AI. No packages will be downloaded.
"backend\.venv\Scripts\python.exe" -B "backend\migrate.py"
if errorlevel 1 goto failed
call build.bat nopause
if errorlevel 1 goto failed
start "" "frontend\src-tauri\target\debug\app.exe" %*
exit /b 0
:failed
echo Startup failed. See the message above and the user manual.
pause
exit /b 1
