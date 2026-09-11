// Milestone 3: resident character window. Owns exactly one backend child
// process for the lifetime of the app; only this process's own child is ever
// stopped, and only from the tray Quit action or app exit (spec section 10:
// never kill a pre-existing process on the fixed port).
use std::net::TcpListener;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
// Stage 6 (home Wi-Fi only): uvicorn itself listens on every interface so an
// iPhone on the same LAN can reach it, while all of this process's OWN
// health/port checks still target 127.0.0.1 only — nothing here needs to
// reach the LAN address, and probing 0.0.0.0 would not reflect what the
// iPhone actually sees anyway.
const BACKEND_BIND_HOST: &str = "0.0.0.0";

struct BackendState {
    child: Option<Child>,
    instance: String,
    error: Option<String>,
}

struct BackendProcess(Mutex<BackendState>);

#[tauri::command]
fn app_quit(app: AppHandle) {
    quit_app(&app);
}

/// 予約した声かけの時刻になったとき、隠れているウィンドウを前に出す。
#[tauri::command]
fn show_window(app: AppHandle) {
    show_character(&app);
}

#[tauri::command]
fn window_topmost(app: AppHandle, enabled: bool) -> Result<(), String> {
    app.get_webview_window("main")
        .ok_or("画面がありません。")?
        .set_always_on_top(enabled)
        .map_err(|_| "最前面設定を変更できません。".into())
}

#[cfg(windows)]
fn input_desktop_active() -> bool {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentThreadId() -> u32;
    }
    #[link(name = "user32")]
    extern "system" {
        fn GetThreadDesktop(id: u32) -> *mut c_void;
        fn GetUserObjectInformationW(
            handle: *mut c_void,
            index: i32,
            info: *mut c_void,
            size: u32,
            needed: *mut u32,
        ) -> i32;
    }
    // UOI_IO: query only; never switch desktops or request focus.
    unsafe {
        let mut active: i32 = 0;
        let mut needed = 0;
        let desktop = GetThreadDesktop(GetCurrentThreadId());
        !desktop.is_null()
            && GetUserObjectInformationW(
                desktop,
                6,
                &mut active as *mut _ as *mut c_void,
                4,
                &mut needed,
            ) != 0
            && active != 0
    }
}

#[cfg(not(windows))]
fn input_desktop_active() -> bool {
    false
}

// Windowsログイン時の自動起動タスクは `app.exe --mini` で呼ぶ想定。フロント
// エンド側はこれを起動時に一度だけ読み、簡易表示から開始するかどうかを
// 決める（永続的な設定ではなく、その起動限りのフラグ）。
#[tauri::command]
fn start_mini() -> bool {
    std::env::args().any(|arg| arg == "--mini")
}

#[tauri::command]
fn desktop_visible(app: AppHandle) -> bool {
    app.get_webview_window("main")
        .map(|window| {
            window.is_visible().unwrap_or(false)
                && !window.is_minimized().unwrap_or(true)
                && input_desktop_active()
        })
        .unwrap_or(false)
}

#[tauri::command]
fn backend_status(state: tauri::State<'_, BackendProcess>) -> Result<String, String> {
    let mut backend = state.0.lock().unwrap();
    if let Some(error) = &backend.error {
        return Err(error.clone());
    }
    let child = backend
        .child
        .as_mut()
        .ok_or("サーバーが起動していません。")?;
    match child.try_wait() {
        Ok(None) => Ok(backend.instance.clone()),
        _ => Err("サーバーが終了しました。DBの起動状態とPython環境を確認してから、アプリを起動し直してください。".into()),
    }
}

/// Starts backend/.venv's uvicorn as a child of this process, resolved
/// relative to the app's install/dev directory so it works both in `tauri
/// dev` (run from frontend/) and a packaged build.
fn spawn_backend(app: &AppHandle, instance: &str) -> std::io::Result<Child> {
    let resource_dir = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap());
    // In dev, resource_dir doesn't apply; fall back to ../../backend from
    // frontend/src-tauri (i.e. C:\kaguya-ai\backend).
    let dev_backend = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../backend");
    let backend_dir = if dev_backend.join(".venv").exists() {
        dev_backend
    } else {
        resource_dir.join("backend")
    };
    let python = backend_dir.join(".venv").join("Scripts").join("python.exe");

    log::info!("starting backend: {:?} in {:?}", python, backend_dir);
    let mut command = Command::new(python);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    command
        .args([
            "-m",
            "uvicorn",
            "app.main:app",
            "--no-access-log",
            "--host",
            BACKEND_BIND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
        ])
        .current_dir(backend_dir)
        .env("BACKEND_INSTANCE", instance)
        .spawn()
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<BackendProcess>();
    let taken = state.0.lock().unwrap().child.take();
    if let Some(mut child) = taken {
        log::info!("stopping backend child process");
        // Windows venv launchers can own another Python process. Stop only our live tree.
        #[cfg(windows)]
        if matches!(child.try_wait(), Ok(None)) {
            let _ = Command::new("taskkill.exe")
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .creation_flags(0x08000000)
                .output();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn hide_character(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

fn show_character(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn quit_app(app: &AppHandle) {
    stop_backend(app);
    app.exit(0);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            // Another launch happened; bring the existing window forward
            // instead of starting a second instance (spec section 10).
            if args.iter().any(|arg| arg == "--quit") { quit_app(app); }
            else { show_character(app); }
        }))
        .manage(BackendProcess(Mutex::new(BackendState {
            child: None,
            instance: format!("{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()),
            error: None,
        })))
        .invoke_handler(tauri::generate_handler![backend_status, app_quit, window_topmost, desktop_visible, start_mini, show_window])
        .setup(|app| {
            if std::env::args().any(|arg| arg == "--quit") {
                app.handle().exit(0);
                return Ok(());
            }
            if cfg!(debug_assertions) {
                // The console window is suppressed in every build (see
                // main.rs), so stdout is no longer a usable log target here
                // — write to the app's log dir instead so debugging still has
                // somewhere to look (%LOCALAPPDATA%\kaguya-ai\logs on Windows).
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .target(tauri_plugin_log::Target::new(
                            tauri_plugin_log::TargetKind::LogDir { file_name: None },
                        ))
                        .build(),
                )?;
            }

            let handle = app.handle().clone();
            {
                let state = app.state::<BackendProcess>();
                let mut backend = state.0.lock().unwrap();
                // Do not attach to or terminate a process already using our port.
                match TcpListener::bind((BACKEND_HOST, BACKEND_PORT)) {
                    Ok(listener) => {
                        drop(listener);
                        match spawn_backend(&handle, &backend.instance) {
                            Ok(child) => backend.child = Some(child),
                            Err(err) => {
                                log::error!("failed to start backend: {err}");
                                backend.error = Some("サーバーを起動できませんでした。Python環境を確認してください。".into());
                            }
                        }
                    }
                    Err(_) => {
                        backend.error = Some("ポート8765を使用できません。別のアプリが使用している場合は終了してから起動し直してください。".into());
                    }
                }
            }

            let open_item = MenuItem::with_id(app, "open", "表示", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "終了", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "非表示", true, None::<&str>)?;
            let quiet_item = MenuItem::with_id(app, "quiet", "静音／声かけ再開", true, None::<&str>)?;
            let settings_item = MenuItem::with_id(app, "settings", "設定", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let tray_menu = Menu::with_items(app, &[&open_item, &hide_item, &quiet_item, &settings_item, &separator, &quit_item])?;

            TrayIconBuilder::new()
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .icon(app.default_window_icon().unwrap().clone())
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => show_character(app),
                    "quit" => quit_app(app),
                    "hide" => hide_character(app),
                    "quiet" => { let _ = app.emit("ui.action", "quiet"); },
                    "settings" => { show_character(app); let _ = app.emit("ui.action", "settings"); },
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        show_character(app);
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Closing the window only hides it (spec 3: "closing the
                // window hides it; Quit from the tray stops the app"). The
                // backend keeps running until tray Quit.
                api.prevent_close();
                hide_character(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event { stop_backend(app); }
        });
}
