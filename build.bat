@echo off
setlocal
cd /d "%~dp0frontend"
call npm.cmd run build
if errorlevel 1 goto failed
cd src-tauri
cargo build --offline --locked --features tauri/custom-protocol
if errorlevel 1 goto failed
echo Build completed.
if /i not "%~1"=="nopause" pause
exit /b 0
:failed
echo Build failed. Existing dependencies are required. No install was attempted.
if /i not "%~1"=="nopause" pause
exit /b 1
