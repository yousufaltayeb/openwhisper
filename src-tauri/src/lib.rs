mod engine;

use engine::{EngineCommand, EngineError, EngineSupervisor};
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::menu::{Menu, MenuItem};
use tauri::plugin::PermissionState;
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_notification::NotificationExt;
use zbus::names::BusName;

struct TrayAvailability(AtomicBool);
struct NotificationPreference(Arc<AtomicBool>);

#[tauri::command]
async fn engine_request(
    method: String,
    params: Value,
    app: tauri::AppHandle,
    supervisor: tauri::State<'_, Arc<EngineSupervisor>>,
    notification_preference: tauri::State<'_, NotificationPreference>,
) -> Result<Value, EngineError> {
    let supervisor = Arc::clone(supervisor.inner());
    let preference = Arc::clone(&notification_preference.0);
    let method_for_request = method.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        supervisor.request(&method_for_request, params)
    })
    .await
    .map_err(|_| EngineError {
        code: "INTERNAL".into(),
        message: "The OpenWhisper host could not complete the request.".into(),
    })??;
    if let Some(enabled) = notification_setting(&method, &result) {
        sync_notification_preference(&app, &preference, enabled);
    }
    Ok(result)
}

fn notification_setting(method: &str, response: &Value) -> Option<bool> {
    match method {
        "app.bootstrap" | "app.restartEngine" => {
            response.pointer("/settings/notifications")?.as_bool()
        }
        "settings.get" | "settings.update" => response.get("notifications")?.as_bool(),
        _ => None,
    }
}

fn sync_notification_preference(app: &tauri::AppHandle, preference: &AtomicBool, enabled: bool) {
    if !enabled {
        preference.store(false, Ordering::Release);
        return;
    }
    let permission = app.notification().permission_state().ok();
    let permission = if matches!(
        permission,
        Some(PermissionState::Prompt | PermissionState::PromptWithRationale)
    ) {
        app.notification().request_permission().ok()
    } else {
        permission
    };
    preference.store(
        permission == Some(PermissionState::Granted),
        Ordering::Release,
    );
}

fn notification_copy(event: &Value) -> Option<(&'static str, &'static str)> {
    match event.get("event")?.as_str()? {
        "dictation.completed" => Some(("OpenWhisper", "Dictation completed.")),
        "notice" => Some((
            "OpenWhisper needs attention",
            "Open the Capture window for details.",
        )),
        _ => None,
    }
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn has_status_notifier_watcher() -> bool {
    let Ok(connection) = zbus::blocking::Connection::session() else {
        return false;
    };
    let Ok(proxy) = zbus::blocking::fdo::DBusProxy::new(&connection) else {
        return false;
    };
    let Ok(name) = BusName::try_from("org.kde.StatusNotifierWatcher") else {
        return false;
    };
    proxy.name_has_owner(name).unwrap_or(false)
}

fn should_start_visible(tray_available: bool, arguments: impl IntoIterator<Item = String>) -> bool {
    !tray_available || arguments.into_iter().any(|argument| argument == "--show")
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app);
        }))
        .plugin(tauri_plugin_notification::init());
    #[cfg(feature = "e2e")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());
    let app = builder
        .manage(TrayAvailability(AtomicBool::new(false)))
        .setup(|app| {
            let app_handle = app.handle().clone();
            let notification_preference = Arc::new(AtomicBool::new(false));
            app.manage(NotificationPreference(Arc::clone(&notification_preference)));
            let supervisor = EngineSupervisor::new(
                EngineCommand::resolve(),
                Arc::new(move |event| {
                    let _ = app_handle.emit("engine-event", &event);
                    if notification_preference.load(Ordering::Acquire) {
                        if let Some((title, body)) = notification_copy(&event) {
                            let _ = app_handle
                                .notification()
                                .builder()
                                .title(title)
                                .body(body)
                                .show();
                        }
                    }
                }),
            );
            app.manage(Arc::clone(&supervisor));

            let show_item = MenuItem::with_id(app, "show", "Show OpenWhisper", true, None::<&str>)?;
            let record_item =
                MenuItem::with_id(app, "record", "Start dictation", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &record_item, &quit_item])?;
            let tray_result = if has_status_notifier_watcher() {
                app.default_window_icon().cloned().map(|icon| {
                    TrayIconBuilder::with_id("openwhisper-main")
                        .icon(icon)
                        .menu(&menu)
                        .show_menu_on_left_click(true)
                        .on_menu_event(|app, event| match event.id().as_ref() {
                            "show" => show_main_window(app),
                            "record" => {
                                let supervisor =
                                    Arc::clone(app.state::<Arc<EngineSupervisor>>().inner());
                                let method = if supervisor.dictation_state() == "recording" {
                                    "dictation.stop"
                                } else {
                                    "dictation.start"
                                };
                                tauri::async_runtime::spawn_blocking(move || {
                                    let _ = supervisor.request(method, serde_json::json!({}));
                                });
                            }
                            "quit" => {
                                let app = app.clone();
                                let supervisor =
                                    Arc::clone(app.state::<Arc<EngineSupervisor>>().inner());
                                tauri::async_runtime::spawn_blocking(move || {
                                    supervisor.shutdown();
                                    app.exit(0);
                                });
                            }
                            _ => {}
                        })
                        .build(app)
                })
            } else {
                None
            };

            let tray_available = matches!(tray_result, Some(Ok(_)));
            app.state::<TrayAvailability>()
                .0
                .store(tray_available, Ordering::Release);
            if should_start_visible(tray_available, std::env::args()) {
                show_main_window(app.handle());
            }

            tauri::async_runtime::spawn_blocking(move || {
                if supervisor.start().is_err() {
                    // The browser bootstrap request will surface the same bounded error.
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let tray_available = window.state::<TrayAvailability>().0.load(Ordering::Acquire);
                if tray_available {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![engine_request])
        .build(tauri::generate_context!())
        .expect("failed to build the OpenWhisper desktop host");

    app.run(|app, event| {
        if matches!(event, RunEvent::ExitRequested { .. }) {
            if let Some(supervisor) = app.try_state::<Arc<EngineSupervisor>>() {
                supervisor.shutdown();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{notification_copy, notification_setting, should_start_visible};
    use serde_json::json;

    #[test]
    fn notification_preference_is_read_only_from_settings_responses() {
        assert_eq!(
            notification_setting(
                "app.bootstrap",
                &json!({"settings": {"notifications": true}}),
            ),
            Some(true)
        );
        assert_eq!(
            notification_setting("settings.update", &json!({"notifications": false})),
            Some(false)
        );
        assert_eq!(notification_setting("dictation.start", &json!({})), None);
    }

    #[test]
    fn notification_copy_never_contains_transcript_content() {
        let private_text = "CANARY_PRIVATE_TRANSCRIPT_مرحبا";
        let event = json!({
            "event": "dictation.completed",
            "payload": {"text": private_text}
        });
        let (title, body) = notification_copy(&event).expect("completion notification");
        assert!(!title.contains(private_text));
        assert!(!body.contains(private_text));
    }

    #[test]
    fn show_argument_and_missing_tray_keep_the_single_instance_reachable() {
        assert!(should_start_visible(false, Vec::<String>::new()));
        assert!(should_start_visible(true, vec!["--show".into()]));
        assert!(!should_start_visible(true, vec!["--background".into()]));
    }

    #[test]
    fn restart_bootstrap_updates_notification_preference_like_bootstrap() {
        let response = json!({"settings": {"notifications": true}});
        assert_eq!(
            notification_setting("app.restartEngine", &response),
            Some(true)
        );
    }
}
