@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
rem Tests create a disposable PostgreSQL cluster and never load backend/.env.
rem Do not allow missing PostgreSQL to turn the integration checks into skips.
set "KAGUYA_REQUIRE_DB=1"
".venv\Scripts\python.exe" -B -m unittest discover -s tests -p test_schema.py -v
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -B -m unittest discover -s tests -p test_migration.py -v
if errorlevel 1 goto failed
echo Migration checks passed. The application database was not used.
set "check_result=0"
goto done
:failed
echo Migration checks failed. See the error above.
set "check_result=1"
:done
popd
pause
exit /b %check_result%
