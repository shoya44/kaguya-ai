@echo off
setlocal
cd /d "%~dp0"
rem Arguments are forwarded to app.exe as-is (e.g. start.bat --mini, used by
rem the sign-in auto-start task to launch straight into mini mode).
if exist "frontend\src-tauri\target\debug\app.exe" (
  powershell.exe -NoProfile -Command "$p = Get-Process app -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path (Get-Location) 'frontend\src-tauri\target\debug\app.exe') }; if ($p) { exit 10 }"
  if errorlevel 10 (
    start "" "frontend\src-tauri\target\debug\app.exe" %*
    exit /b 0
  )
)

rem Stability-first: normal startup never changes the Git working tree.
rem Repository updates are performed only by the explicit "最新版を反映" action.
echo Preparing Kaguya AI. No packages will be downloaded and Git will not be changed.
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
