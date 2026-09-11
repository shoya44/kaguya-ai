@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -B -m unittest discover -s tests -v
set "test_result=%errorlevel%"
if "%test_result%"=="0" (
  echo Backend tests passed. No live DB or Gemini calls were made.
) else (
  echo Backend tests failed or Python could not start. See the error above.
)
popd
pause
exit /b %test_result%
