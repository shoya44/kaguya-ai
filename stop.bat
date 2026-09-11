@echo off
setlocal
cd /d "%~dp0"
if not exist "frontend\src-tauri\target\debug\app.exe" exit /b 0
echo Stopping Kaguya AI. Saved memories remain. Unsaved answers may be lost.
start "" /wait "frontend\src-tauri\target\debug\app.exe" --quit
