@echo off
rem 開発・保守用のコマンドをここ1本にまとめる。日常の起動・終了は start.bat / stop.bat。
setlocal
cd /d "%~dp0"

rem start.batなど、画面が閉じてしまう呼び出し元はnopauseを付ける。
if /i "%~2"=="nopause" set "KAGUYA_NO_PAUSE=1"
if /i "%~3"=="nopause" set "KAGUYA_NO_PAUSE=1"

set "COMMAND=%~1"
if "%COMMAND%"=="" goto usage
if /i "%COMMAND%"=="build" goto build
if /i "%COMMAND%"=="check" goto check
if /i "%COMMAND%"=="check-db" goto checkdb
if /i "%COMMAND%"=="autostart" goto autostart
if /i "%COMMAND%"=="help" goto usage
echo Unknown command: %COMMAND%
echo.
goto usage

:build
rem 依存関係の自動インストールはしない。既存のnode_modulesとcargoキャッシュを使う。
cd frontend
call npm.cmd run build
if errorlevel 1 goto failed
cd src-tauri
cargo build --offline --locked --features tauri/custom-protocol
if errorlevel 1 goto failed
echo Build completed.
goto done

:check
rem ライブGemini・天気API・本番DBへは接続しない。PRごとにGitHub Actionsでも回る。
cd backend
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
goto done

:checkdb
rem 使い捨てのローカルPostgreSQLで試す。アプリのDBは使わない。
cd backend
".venv\Scripts\python.exe" -B "tests\run_db_checks.py"
if errorlevel 1 goto failed
echo Database integration checks passed.
goto done

:autostart
rem Windowsサインイン時に簡易表示で起動するタスク。管理者権限は不要。
set "TASK_NAME=KaguyaAI_AutoStart"
if /i "%~2"=="on" (
  schtasks /Create /TN "%TASK_NAME%" /TR "\"%~dp0start.bat\" --mini" /SC ONLOGON /RL LIMITED /F
  if errorlevel 1 goto failed
  echo Registered: KaguyaAI will start minimized on next Windows sign-in.
  goto done
)
if /i "%~2"=="off" (
  schtasks /Delete /TN "%TASK_NAME%" /F
  if errorlevel 1 (
    echo No auto-start task found, or it could not be removed.
  ) else (
    echo Removed: KaguyaAI will no longer start automatically at sign-in.
  )
  goto done
)
echo Usage: kaguya.bat autostart on^|off
goto failed

:usage
echo Usage: kaguya.bat ^<command^>
echo.
echo   build            Rebuild the frontend and the desktop app.
echo   check            Run all local tests, the build and cargo check.
echo   check-db         Run database checks against a disposable local PostgreSQL.
echo   autostart on     Start Kaguya AI in mini mode at Windows sign-in.
echo   autostart off    Remove that auto-start task.
echo.
echo   Day to day, use start.bat and stop.bat instead.
if not defined KAGUYA_NO_PAUSE pause
exit /b 1

:done
if not defined KAGUYA_NO_PAUSE pause
exit /b 0

:failed
echo The command failed. See the message above.
if not defined KAGUYA_NO_PAUSE pause
exit /b 1
