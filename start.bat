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
