@echo off
setlocal
cd /d "%~dp0backend"
echo Testing with a disposable local PostgreSQL cluster. The application DB is not used.
".venv\Scripts\python.exe" -B "tests\run_db_checks.py"
set "test_result=%errorlevel%"
if "%test_result%"=="0" (echo Database integration checks passed.) else (echo Database test failed. See the error above.)
pause
exit /b %test_result%
