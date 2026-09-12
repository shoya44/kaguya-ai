@echo off
rem Tools such as git print UTF-8, which is garbled on the default cp932
rem console. Changing the code page while a batch file is running makes cmd
rem lose its place in the file (the rem lines below are multi-byte), so the
rem page is switched once here and this file is re-run in a child cmd.
rem Everything above ":main" is ASCII on purpose. Do not add non-ASCII here.
if defined KAGUYA_CP goto main
for /f "tokens=2 delims=:" %%C in ('chcp') do set "KAGUYA_CP=%%C"
set "KAGUYA_CP=%KAGUYA_CP: =%"
chcp 65001 >nul
cmd /d /c ""%~f0" %*"
set "KAGUYA_RESULT=%errorlevel%"
chcp %KAGUYA_CP% >nul
exit /b %KAGUYA_RESULT%

:main
rem 開発・保守用のコマンドをここ1本にまとめる。日常の起動・終了は start.bat / stop.bat。
setlocal
cd /d "%~dp0"

rem start.batなど、画面が閉じてしまう呼び出し元はnopauseを付ける。
if /i "%~2"=="nopause" set "KAGUYA_NO_PAUSE=1"
if /i "%~3"=="nopause" set "KAGUYA_NO_PAUSE=1"

set "COMMAND=%~1"
rem 引数なし（エクスプローラーからのダブルクリック）はメニューを出す。
if "%COMMAND%"=="" goto menu
if /i "%COMMAND%"=="update" goto update
if /i "%COMMAND%"=="build" goto build
if /i "%COMMAND%"=="check" goto check
if /i "%COMMAND%"=="check-db" goto checkdb
if /i "%COMMAND%"=="autostart" goto autostart
if /i "%COMMAND%"=="help" goto usage
echo Unknown command: %COMMAND%
echo.
goto usage

:update
rem 更新の唯一の入口。終了 → mainを--ff-only更新 → ビルド → 起動 を順に行う。
rem 終了 → mainを--ff-only更新 → ビルド → 起動 を順に行い、失敗はその場に表示する。
echo Stopping Kaguya AI...
call "%~dp0stop.bat"
rem Tauriは通常すぐ終了する。実行ファイルのロックが外れるまで最大20秒待つ。
rem ロックされたままビルドすると失敗するため、ここで待ってから進める。
for /l %%i in (1,1,20) do (
  tasklist /fi "imagename eq app.exe" /nh | find /i "app.exe" >nul || goto ready
  timeout /t 1 /nobreak >nul
)
echo Kaguya AI is still running. Close it from the tray, then run this again.
goto failed

:ready
call "%~dp0tools\update_repo.bat"
set "UPDATE_RESULT=%errorlevel%"
rem スキップされたまま起動すると、更新できたように見えてしまう。ここで止める。
if "%UPDATE_RESULT%"=="10" goto blocked
if "%UPDATE_RESULT%"=="20" goto offline
if "%UPDATE_RESULT%"=="21" goto update_failed
if not "%UPDATE_RESULT%"=="0" if not "%UPDATE_RESULT%"=="5" goto update_failed
rem start.bat がマイグレーション・ビルド・起動をまとめて行う。
call "%~dp0start.bat"
if errorlevel 1 goto failed
echo Update finished. Kaguya AI is starting.
goto done

:blocked
echo.
echo   The update was SKIPPED. Nothing was downloaded.
echo   See the reason above, then run "git status" to check for local changes.
echo   Kaguya AI was NOT started. Fix the cause and run update again.
goto failed

:offline
echo.
echo   Fetch from origin/main failed.
echo   Check the Git error above for network, access or repository problems.
echo   Start the current version with start.bat if you want to use it now.
goto failed

:update_failed
echo.
echo   The repository could not be updated. See the Git error above.
echo   Check local changes with "git status" and "git diff".
echo   Kaguya AI was NOT started. Fix the cause and run update again.
goto failed

:build
rem 起動したままだとapp.exeを掴んでいて、cargoが置き換えられずos error 5で落ちる。
tasklist /fi "imagename eq app.exe" /nh | find /i "app.exe" >nul || goto build_start
echo.
echo   Kaguya AI is running, so the app file cannot be replaced.
echo   Close it from the tray, or use "update" which stops it first.
goto failed

:build_start
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
node --test tests\main.test.cjs tests\living.test.cjs tests\avatar.test.cjs tests\manual.test.cjs tests\controls.test.cjs
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
rem Windowsサインイン時に簡易表示で起動する。読み上げエンジンも一緒に常駐させ、
rem 通話のたびに手で起動しなくて済むようにする。
call :autostart_vars
if /i "%~2"=="on" goto autostart_on
if /i "%~2"=="off" goto autostart_off
echo Usage: kaguya.bat autostart on^|off
goto failed

:autostart_vars
rem スタートアップフォルダーに起動用のcmdを置くだけにする。管理者権限も
rem 資格情報も要らない。schtasks /SC ONLOGON は「どのユーザーのサインインでも」
rem という全ユーザー向けの登録になり、昇格していないと Access is denied で失敗する。
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "APP_LINK=%STARTUP%\Kaguya AI.cmd"
set "TTS_LINK=%STARTUP%\Kaguya AI Voice.cmd"
exit /b 0

:autostart_on
if not exist "%STARTUP%" goto autostart_nofolder
rem 起動用のcmdは自分で終わる。start.bat を待たないので窓は残らない。
rem echo は ERRORLEVEL を 0 に戻さない。メニューの choice が Y で 1 を残すため、
rem echo の直後に「if errorlevel 1」を書くと必ず失敗と判定してしまう。
rem 書けたかどうかは、消してから書き直してファイルの有無で見る。
del /f /q "%APP_LINK%" >nul 2>&1
(echo @echo off)> "%APP_LINK%"
(echo start "" "%~dp0start.bat" --mini)>> "%APP_LINK%"
if not exist "%APP_LINK%" goto autostart_nowrite
echo Registered: Kaguya AI starts in mini mode at sign-in.
rem よくある置き場だけを見る。見つからなければ手動の手順を出す。
set "TTS_EXE=%LOCALAPPDATA%\Programs\AivisSpeech\AivisSpeech.exe"
if exist "%TTS_EXE%" goto autostart_tts
set "TTS_EXE=%ProgramFiles%\AivisSpeech\AivisSpeech.exe"
if exist "%TTS_EXE%" goto autostart_tts
set "TTS_EXE=%ProgramFiles(x86)%\AivisSpeech\AivisSpeech.exe"
if exist "%TTS_EXE%" goto autostart_tts
echo.
echo   AivisSpeech was not found, so it was NOT registered.
echo   Press Win+R, run "shell:startup", and put a shortcut to AivisSpeech there.
echo   Without it, calls fall back to the Gemini voice.
goto done

:autostart_tts
del /f /q "%TTS_LINK%" >nul 2>&1
(echo @echo off)> "%TTS_LINK%"
(echo start "" "%TTS_EXE%")>> "%TTS_LINK%"
if not exist "%TTS_LINK%" goto autostart_nowrite
echo Registered: AivisSpeech starts at sign-in.
goto done

:autostart_nofolder
echo The Startup folder was not found:
echo   "%STARTUP%"
goto failed

:autostart_nowrite
echo Could not write to the Startup folder:
echo   "%STARTUP%"
echo Security software may be blocking writes to this folder.
echo Press Win+R, run "shell:startup", and check that you can create a file there.
goto failed

:autostart_off
del /f /q "%APP_LINK%" >nul 2>&1
del /f /q "%TTS_LINK%" >nul 2>&1
rem 旧版はタスクスケジューラに登録していた。残っていれば消すが、無くても失敗にしない。
schtasks /Delete /TN "KaguyaAI_AutoStart" /F >nul 2>&1
schtasks /Delete /TN "KaguyaAI_Voice_AutoStart" /F >nul 2>&1
if exist "%APP_LINK%" goto autostart_stuck
if exist "%TTS_LINK%" goto autostart_stuck
echo Removed: Kaguya AI and AivisSpeech no longer start at sign-in.
goto done

:autostart_stuck
echo Could not remove the Startup entries in:
echo   "%STARTUP%"
echo Press Win+R, run "shell:startup", and delete them by hand.
goto failed

:menu
rem このファイルはUTF-8なので、echoする文字はASCIIに限る（cp932のコンソールで化けるため）。
rem 「if COND cmd1 ^& cmd2」は条件に関わらずcmd2が走るため、分岐は1行1ジャンプにする。
echo.
echo   Kaguya AI
echo.
echo     1  update      Update to the latest version and start again
echo     2  check       Run all local tests and the build
echo     3  build       Rebuild only
echo     4  check-db    Database checks on a disposable local PostgreSQL
echo     5  autostart   Start Kaguya AI and AivisSpeech at sign-in
echo     0  exit
echo.
rem set /p ではなくchoiceを使う。Enter不要で、空入力や想定外の文字が入らない。
rem errorlevelは「以上」で判定されるため、必ず大きい方から見る。
choice /c 123450 /n /m "Select: "
if errorlevel 6 goto quit
if errorlevel 5 goto autostart_menu
if errorlevel 4 goto checkdb
if errorlevel 3 goto build
if errorlevel 2 goto check
if errorlevel 1 goto update
goto quit

:autostart_menu
call :autostart_vars
choice /c yn /m "Start Kaguya AI and AivisSpeech at Windows sign-in"
if errorlevel 2 goto autostart_off
goto autostart_on

:quit
exit /b 0

:usage
echo Usage: kaguya.bat ^<command^>
echo.
echo   update           Stop, update main, rebuild and start again.
echo   build            Rebuild the frontend and the desktop app.
echo   check            Run all local tests, the build and cargo check.
echo   check-db         Run database checks against a disposable local PostgreSQL.
echo   autostart on     Start Kaguya AI (mini) and AivisSpeech at sign-in.
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
