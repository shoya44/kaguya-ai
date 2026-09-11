@echo off
setlocal
cd /d "%~dp0backend"
".venv\Scripts\python.exe" -B -m unittest discover -s tests -v
if errorlevel 1 goto failed
cd ..\frontend
node --test tests\main.test.cjs tests\living.test.cjs tests\avatar.test.cjs
if errorlevel 1 goto failed
call npm.cmd run build
if errorlevel 1 goto failed
cd src-tauri
cargo check --offline --locked --features tauri/custom-protocol
if errorlevel 1 goto failed
echo All local checks passed. No live Gemini, weather API or production DB calls were made.
pause
exit /b 0
:failed
echo A check failed. See the error above.
pause
exit /b 1
