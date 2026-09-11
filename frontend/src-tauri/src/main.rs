// Suppresses the console window on Windows in every build, debug included —
// this app always runs via start.bat's debug build, never `cargo run` from a
// terminal, so a visible console only leaks tauri_plugin_log output that
// nobody is meant to read interactively (see lib.rs's log file target).
#![windows_subsystem = "windows"]

fn main() {
  app_lib::run();
}
