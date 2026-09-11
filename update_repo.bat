@echo off
setlocal
cd /d "%~dp0"
set "GIT_TERMINAL_PROMPT=0"

where git.exe >nul 2>&1
if errorlevel 1 (
  echo [Update] Git was not found. Starting with the current version.
  exit /b 20
)

for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "BRANCH=%%B"
if /i not "%BRANCH%"=="main" (
  echo [Update] Current branch is not main. Update was skipped.
  exit /b 10
)

for /f "delims=" %%S in ('git status --porcelain --untracked-files=normal 2^>nul') do (
  echo [Update] Local changes exist. Update was skipped to protect them.
  exit /b 10
)

for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%H"
if not defined BEFORE (
  echo [Update] This folder is not a usable Git repository. Starting with the current version.
  exit /b 20
)

echo [Update] Checking origin/main...
git pull --ff-only origin main
if errorlevel 1 (
  echo [Update] Could not update from origin/main. Starting with the current version.
  exit /b 20
)

for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%H"
if /i not "%BEFORE%"=="%AFTER%" (
  echo [Update] Repository updated.
  exit /b 5
)

echo [Update] Already up to date.
exit /b 0
