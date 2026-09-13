@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
rem Require disposable PostgreSQL checks; never connect to the application DB.
set "KAGUYA_REQUIRE_DB=1"
".venv\Scripts\python.exe" -B -m unittest discover -s tests -p test_living_prompt.py -v
set "test_result=%errorlevel%"
if not "%test_result%"=="0" goto done
".venv\Scripts\python.exe" -B -m unittest discover -s tests -p test_llm_schema.py -v
set "test_result=%errorlevel%"
:done
if "%test_result%"=="0" (
  echo Living prompt, disposable DB and LLM schema checks passed.
) else (
  echo Checks failed. See the error above.
)
popd
pause
exit /b %test_result%
