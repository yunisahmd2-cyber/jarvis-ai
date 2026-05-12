use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, WindowEvent};

#[derive(Serialize)]
struct ScreenshotCapture {
    data_url: String,
    mime_type: String,
    width: Option<u32>,
    height: Option<u32>,
    captured_at_ms: u128,
}

#[tauri::command]
fn capture_screenshot() -> Result<ScreenshotCapture, String> {
    #[cfg(target_os = "macos")]
    {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|err| err.to_string())?
            .as_millis();

        let mut path = std::env::temp_dir();
        path.push(format!("jarvis-screenshot-{timestamp}.png"));

        let status = Command::new("screencapture")
            .args(["-x", path.to_string_lossy().as_ref()])
            .status()
            .map_err(|err| format!("Failed to run screencapture: {err}"))?;

        if !status.success() {
            return Err(
                "Screenshot capture failed. macOS may require Screen Recording permission for this app."
                    .to_string(),
            );
        }

        let bytes = fs::read(&path).map_err(|err| format!("Failed to read screenshot: {err}"))?;
        let dimensions = png_dimensions(&bytes);
        let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);

        let _ = fs::remove_file(path);

        return Ok(ScreenshotCapture {
            data_url: format!("data:image/png;base64,{encoded}"),
            mime_type: "image/png".to_string(),
            width: dimensions.map(|(width, _)| width),
            height: dimensions.map(|(_, height)| height),
            captured_at_ms: timestamp,
        });
    }

    #[cfg(not(target_os = "macos"))]
    {
        Err("Native screenshot capture is currently implemented only for macOS.".to_string())
    }
}

fn png_dimensions(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < 24 {
        return None;
    }

    let png_header = [137, 80, 78, 71, 13, 10, 26, 10];
    if bytes[0..8] != png_header {
        return None;
    }

    let width = u32::from_be_bytes(bytes[16..20].try_into().ok()?);
    let height = u32::from_be_bytes(bytes[20..24].try_into().ok()?);
    Some((width, height))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_websocket::init())
        .invoke_handler(tauri::generate_handler![capture_screenshot])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Show Jarvis", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Hide Jarvis", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &hide, &quit])?;

            let mut tray = TrayIconBuilder::with_id("jarvis-tray")
                .tooltip("Jarvis")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| {
                    if let Some(window) = app.get_webview_window("main") {
                        match event.id().as_ref() {
                            "show" => {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                            "hide" => {
                                let _ = window.hide();
                            }
                            "quit" => {
                                app.exit(0);
                            }
                            _ => {}
                        }
                    } else if event.id().as_ref() == "quit" {
                        app.exit(0);
                    }
                });

            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }

            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
