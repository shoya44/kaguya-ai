@echo off
setlocal
set "BRANCH="
set "BEFORE="
set "AFTER="
rem tools/ に置くので、Git操作の対象は1つ上のリポジトリルート。
cd /d "%~dp0.."
set "GIT_TERMINAL_PROMPT=0"

where git.exe >nul 2>&1
if errorlevel 1 (
  echo [Update] Git was not found. Update stopped.
  exit /b 21
)

rem どのブランチにいるのか、どう直すのかまで出す。名前が分からないと直しようがない。
rem 入れ子の括弧は解釈を誤りやすいので、1行1処理で書く。
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "BRANCH=%%B"
if /i "%BRANCH%"=="main" goto branch_ok
if not defined BRANCH set "BRANCH=none (detached HEAD)"
echo [Update] Current branch is "%BRANCH%", not main. Update was skipped.
echo [Update] Run "git status" to check for local changes, then "git checkout main".
exit /b 10

:branch_ok
rem 見るのは追跡中のファイルの変更だけ。置いただけの未追跡ファイル（設定や
rem 作業メモ）で更新が永久に止まるのを避ける。取り込むファイルと衝突する
rem 場合は下の merge が自分で失敗するので、消えることはない。
for /f "delims=" %%S in ('git status --porcelain --untracked-files=no 2^>nul') do (
  echo [Update] Tracked files have local changes. Update was skipped to protect them.
  echo [Update] Run "git status" to see them, then keep or undo them and run update again.
  exit /b 10
)

for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%H"
if not defined BEFORE (
  echo [Update] This folder is not a usable Git repository. Update stopped.
  exit /b 21
)

echo [Update] Checking origin/main...
git fetch origin main
if errorlevel 1 (
  echo [Update] Could not fetch origin/main. Update stopped.
  exit /b 20
)
git merge --ff-only FETCH_HEAD
if errorlevel 1 (
  echo [Update] Fetch succeeded, but the local merge failed. Update stopped.
  exit /b 21
)

for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%H"
if /i not "%BEFORE%"=="%AFTER%" (
  echo [Update] Repository updated.
  exit /b 5
)

echo [Update] Already up to date.
exit /b 0
