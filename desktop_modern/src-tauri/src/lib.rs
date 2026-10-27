pub mod core_process;
pub mod discovery;
pub mod process_job;
pub mod protected_runtime;
pub mod protected_process;
pub mod protected_desktop;
pub mod agent_runtime;
pub mod agent_workspace;

use core_process::{migrate_database_if_absent, resolve_core_launch, CoreBridge, CoreRequestInput, DeltaPayload};
use serde_json::Value;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, Manager, State, WebviewWindow};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

pub fn parse_hotkey(value: &str) -> Result<Shortcut, String> {
    match value {
        "Alt+Space" | "Ctrl+Alt+Space" | "Ctrl+Shift+Space" => value
            .parse::<Shortcut>()
            .map_err(|_| "invalid_hotkey".to_string()),
        _ => Err("unsupported_hotkey".into()),
    }
}

#[tauri::command]
async fn core_request(
    bridge: State<'_, Arc<CoreBridge>>,
    request: CoreRequestInput,
) -> Result<Value, String> {
    agent_runtime::validate_ui_core_action(&request.action)?;
    let bridge = Arc::clone(bridge.inner());
    tauri::async_runtime::spawn_blocking(move || bridge.call(request, |_| {}))
        .await
        .map_err(|error| format!("core_task_failed: {error}"))?
}

#[tauri::command]
async fn core_stream(
    window: WebviewWindow,
    bridge: State<'_, Arc<CoreBridge>>,
    request: CoreRequestInput,
) -> Result<Value, String> {
    agent_runtime::validate_ui_core_action(&request.action)?;
    let bridge = Arc::clone(bridge.inner());
    tauri::async_runtime::spawn_blocking(move || {
        bridge.call(request, |delta: DeltaPayload| {
            let _ = window.emit("core-delta", delta);
        })
    })
    .await
    .map_err(|error| format!("core_task_failed: {error}"))?
}

#[tauri::command]
async fn discovery_request(runtime: State<'_, Arc<discovery::DiscoveryRuntime>>, action: String, params: Value) -> Result<Value,String> {
    discovery::validate_action(&action)?;
    let runtime=Arc::clone(runtime.inner());
    tauri::async_runtime::spawn_blocking(move || runtime.0.as_ref().map_err(|_|"discovery_runtime_unavailable".to_string())?.request(&action,params)).await.map_err(|_| "discovery_task_failed".to_string())?
}

#[tauri::command]
fn protected_runtime_start(runtime:State<'_,Arc<protected_desktop::DesktopRuntime>>)->Result<protected_desktop::RuntimeStatus,String>{runtime.start()}
#[tauri::command]
fn protected_runtime_status(runtime:State<'_,Arc<protected_desktop::DesktopRuntime>>)->protected_desktop::RuntimeStatus{runtime.status()}
#[tauri::command]
fn protected_runtime_stop(runtime:State<'_,Arc<protected_desktop::DesktopRuntime>>)->Result<protected_desktop::RuntimeStatus,String>{runtime.stop()}
#[tauri::command]
async fn agent_task_start(runtime:State<'_,Arc<agent_runtime::AgentRuntime>>,conversation_id:String,goal:String,model:Option<String>)->Result<Value,String>{let runtime=runtime.inner().clone();tauri::async_runtime::spawn_blocking(move||runtime.start(conversation_id,goal,model)).await.map_err(|_|"agent_native_task_failed".to_owned())?}
#[tauri::command]
async fn agent_task_status(runtime:State<'_,Arc<agent_runtime::AgentRuntime>>,conversation_id:Option<String>)->Result<Value,String>{let runtime=runtime.inner().clone();tauri::async_runtime::spawn_blocking(move||runtime.status_for(conversation_id.as_deref())).await.map_err(|_|"agent_native_task_failed".to_owned())}
#[tauri::command]
fn agent_task_decide(runtime:State<'_,Arc<agent_runtime::AgentRuntime>>,task_id:String,proposal_id:String,approved:bool)->Result<Value,String>{runtime.decide(&task_id,&proposal_id,approved)}
#[tauri::command]
fn agent_task_cancel(runtime:State<'_,Arc<agent_runtime::AgentRuntime>>,task_id:String)->Result<Value,String>{runtime.cancel(&task_id)}
#[tauri::command]
async fn agent_workspace_get(runtime:State<'_,Arc<agent_runtime::AgentRuntime>>,conversation_id:Option<String>)->Result<agent_workspace::WorkspaceStatus,String>{let runtime=runtime.inner().clone();tauri::async_runtime::spawn_blocking(move||runtime.workspace_get(conversation_id.as_deref())).await.map_err(|_|"agent_native_task_failed".to_owned())}
#[tauri::command]
async fn agent_workspace_select(runtime:State<'_,Arc<agent_runtime::AgentRuntime>>,conversation_id:String,path:String)->Result<agent_workspace::WorkspaceStatus,String>{let runtime=runtime.inner().clone();tauri::async_runtime::spawn_blocking(move||runtime.workspace_select(&conversation_id,&path)).await.map_err(|_|"agent_native_task_failed".to_owned())?}

#[tauri::command]
fn open_companion(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("companion") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }
    tauri::WebviewWindowBuilder::new(app, "companion", tauri::WebviewUrl::App("index.html?companion=1".into()))
        .title("AI")
        .inner_size(420.0, 520.0)
        .always_on_top(true)
        .build()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn show_companion(app: tauri::AppHandle) -> Result<(), String> {
    open_companion(&app)
}

#[tauri::command]
fn show_main(app: tauri::AppHandle) -> Result<(), String> {
    let window = app.get_webview_window("main").ok_or("main_window_unavailable")?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn register_global_hotkey(app: tauri::AppHandle, hotkey: String) -> Result<(), String> {
    let shortcut = parse_hotkey(&hotkey)?;
    app.global_shortcut().unregister_all().map_err(|error| error.to_string())?;
    app.global_shortcut().register(shortcut).map_err(|error| error.to_string())
}

#[tauri::command]
fn release_global_hotkey(app: tauri::AppHandle) -> Result<(), String> {
    app.global_shortcut().unregister_all().map_err(|error| error.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new()
            .with_handler(|app, _shortcut, event| {
                if event.state == ShortcutState::Pressed {
                    let app = app.clone();
                    tauri::async_runtime::spawn(async move {
                        let _ = open_companion(&app);
                    });
                }
            })
            .build())
        .setup(|app| {
            let instance=Arc::new(protected_process::CompatGuard::acquire().map_err(|error| ->Box<dyn std::error::Error>{error.0.into()})?);
            app.manage(instance.clone());
            let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
            let project_root = manifest.parent().and_then(|path| path.parent()).ok_or("project_root_unavailable")?;
            let executable = std::env::current_exe()?;
            app.manage(Arc::new(protected_desktop::DesktopRuntime::new(&executable,instance.clone()).map_err(|error| ->Box<dyn std::error::Error>{error.into()})?));
            let packaged = !cfg!(debug_assertions);
            let database = if packaged {
                let data_dir = std::env::var_os("AGENT_DATA_DIR")
                    .map(PathBuf::from)
                    .unwrap_or(app.path().app_data_dir()?);
                let destination = data_dir.join("conversations.db");
                if let Some(legacy) = std::env::var_os("AGENT_LEGACY_DB") {
                    migrate_database_if_absent(&PathBuf::from(legacy), &destination)
                        .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
                }
                destination
            } else {
                project_root.join("data").join("conversations.db")
            };
            let sidecar = executable.parent().unwrap_or(project_root).join("agent-core.exe");
            let launch = resolve_core_launch(&executable, &sidecar, &database, packaged);
            let bridge = CoreBridge::spawn(launch).map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            let discovery_data=std::env::var_os("AGENT_DATA_DIR").map(PathBuf::from).unwrap_or(app.path().app_data_dir()?).join("discovery");
            let runtime=discovery::DiscoveryBridge::start(&executable,project_root,&discovery_data,packaged);
            app.manage(Arc::new(discovery::DiscoveryRuntime(runtime)));
            app.manage(Arc::new(agent_runtime::AgentRuntime::new(&executable,bridge.clone(),instance,database.parent().ok_or("agent_data_dir_unavailable")?)));
            app.manage(bridge);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            core_request, core_stream, discovery_request, protected_runtime_start, protected_runtime_status, protected_runtime_stop, show_companion, show_main,
            agent_task_start,agent_task_status,agent_task_decide,agent_task_cancel,
            agent_workspace_get,agent_workspace_select,
            register_global_hotkey, release_global_hotkey
        ])
        .build(tauri::generate_context!())
        .expect("failed to build desktop app")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                app.state::<Arc<agent_runtime::AgentRuntime>>().shutdown();
                app.state::<Arc<protected_desktop::DesktopRuntime>>().shutdown();
                if let Ok(runtime)=&app.state::<Arc<discovery::DiscoveryRuntime>>().0 { runtime.bridge.shutdown(); }
                app.state::<Arc<CoreBridge>>().shutdown();
                app.state::<Arc<agent_runtime::AgentRuntime>>().join();
            }
        });
}
