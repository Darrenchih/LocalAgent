use serde_json::json;
use windows_ollama_agent_desktop_lib::core_process::{
    encode_request, migrate_database_if_absent, resolve_core_launch, route_response, CoreLifecycle, CoreLaunch, RoutedResponse,
};
use windows_ollama_agent_desktop_lib::parse_hotkey;

#[test]
fn envelope_is_versioned_and_newline_delimited() {
    let encoded = encode_request("req-1", "health", json!({}));
    assert!(encoded.ends_with('\n'));
    assert_eq!(encoded.matches('\n').count(), 1);
    let value: serde_json::Value = serde_json::from_str(encoded.trim_end()).unwrap();
    assert_eq!(value, json!({"version":"1.0", "id":"req-1", "action":"health", "params":{}}));
}

#[test]
fn delta_and_final_responses_keep_their_request_id() {
    let delta = route_response(r#"{"version":"1.0","id":"a","ok":true,"event":{"type":"delta","content":"你"}}"#).unwrap();
    assert_eq!(delta, RoutedResponse::Delta { request_id: "a".into(), content: "你".into() });
    let final_value = route_response(r#"{"version":"1.0","id":"b","ok":true,"result":{"status":"ok"}}"#).unwrap();
    assert_eq!(final_value.request_id(), "b");
}

#[test]
fn malformed_or_unversioned_output_is_rejected() {
    assert!(route_response("not json").is_err());
    assert!(route_response(r#"{"id":"a","ok":true,"result":{}}"#).is_err());
}

#[test]
fn lifecycle_reaches_stopped_after_shutdown() {
    let mut lifecycle = CoreLifecycle::new();
    assert!(lifecycle.mark_running().is_ok());
    lifecycle.mark_stopped();
    assert!(lifecycle.is_stopped());
    assert!(lifecycle.mark_running().is_err());
}

#[test]
fn only_supported_global_hotkeys_are_parsed() {
    assert!(parse_hotkey("Alt+Space").is_ok());
    assert!(parse_hotkey("Ctrl+Alt+Space").is_ok());
    assert!(parse_hotkey("Ctrl+Shift+Space").is_ok());
    assert_eq!(parse_hotkey("Ctrl+R").unwrap_err(), "unsupported_hotkey");
}

#[test]
fn packaged_launch_uses_embedded_core_and_explicit_database() {
    let launch = resolve_core_launch(
        std::path::Path::new(r"C:\Program Files\LocalAgent\LocalAgent.exe"),
        std::path::Path::new(r"C:\Program Files\LocalAgent\agent-core.exe"),
        std::path::Path::new(r"C:\Users\test\AppData\Roaming\LocalAgent\conversations.db"),
        true,
    );
    assert_eq!(launch, CoreLaunch {
        program: r"C:\Program Files\LocalAgent\agent-core.exe".into(),
        args: vec!["--stdio".into(), "--db".into(), r"C:\Users\test\AppData\Roaming\LocalAgent\conversations.db".into()],
        working_dir: r"C:\Program Files\LocalAgent".into(),
    });
}

#[test]
fn development_launch_keeps_python_module_fallback() {
    let launch = resolve_core_launch(
        std::path::Path::new(r"D:\Agent\desktop-agent\desktop_modern\src-tauri\target\debug\app.exe"),
        std::path::Path::new(r"D:\Agent\desktop-agent\desktop_modern\src-tauri\binaries\agent-core.exe"),
        std::path::Path::new(r"D:\Agent\desktop-agent\data\conversations.db"),
        false,
    );
    assert_eq!(launch.program, "python");
    assert_eq!(launch.args, vec!["-u", "-m", "agent_core.server", "--stdio", "--db", r"D:\Agent\desktop-agent\data\conversations.db"]);
    assert_eq!(launch.working_dir, r"D:\Agent\desktop-agent");
}

#[test]
fn migration_copies_legacy_database_only_when_destination_is_absent() {
    let temp = tempfile::tempdir().unwrap();
    let legacy = temp.path().join("legacy.db");
    let destination = temp.path().join("user-data").join("conversations.db");
    std::fs::write(&legacy, b"legacy").unwrap();
    assert!(migrate_database_if_absent(&legacy, &destination).unwrap());
    assert_eq!(std::fs::read(&destination).unwrap(), b"legacy");

    std::fs::write(&legacy, b"changed").unwrap();
    assert!(!migrate_database_if_absent(&legacy, &destination).unwrap());
    assert_eq!(std::fs::read(&destination).unwrap(), b"legacy");
}
// Native control-plane fault fixtures; no Agent tools or model service.
#[cfg(windows)]
fn fault_core(script: &str) -> std::sync::Arc<windows_ollama_agent_desktop_lib::core_process::CoreBridge> {
    windows_ollama_agent_desktop_lib::core_process::CoreBridge::spawn(CoreLaunch {
        program: std::env::var("AGENT_PYTHON").unwrap_or_else(|_| "python".into()),
        args: vec!["-u".into(), "-c".into(), script.into()],
        working_dir: r"D:\Agent\desktop-agent\work\agent-integration\workspace".into(),
    }).unwrap()
}
#[cfg(windows)]
fn fault_request() -> windows_ollama_agent_desktop_lib::core_process::CoreRequestInput {
    windows_ollama_agent_desktop_lib::core_process::CoreRequestInput {
        version:"1.0".into(),request_id:None,action:"health".into(),params:json!({"fixture":"x".repeat(262144)}),
    }
}
#[cfg(windows)]
#[test]
fn stalled_core_pipe_cancel_returns_and_shutdown_finishes() {
    use std::{sync::{Arc,atomic::{AtomicBool,Ordering}},time::{Duration,Instant},thread};
    let bridge=fault_core("import time; time.sleep(60)");let cancel=Arc::new(AtomicBool::new(false));
    let trigger=cancel.clone();let signal=thread::spawn(move||{thread::sleep(Duration::from_millis(150));trigger.store(true,Ordering::Release);});
    let start=Instant::now();
    assert_eq!(bridge.call_cancellable(fault_request(),Duration::from_secs(10),&cancel).unwrap_err(),"agent_cancelled");
    signal.join().unwrap();bridge.shutdown();assert!(start.elapsed()<Duration::from_secs(5));
    assert_eq!(bridge.call(fault_request(),|_|{}).unwrap_err(),"core_stopped");
}
#[cfg(windows)]
#[test]
fn stalled_core_pipe_send_has_finite_deadline_without_cancel() {
    use std::{sync::atomic::AtomicBool,time::{Duration,Instant}};
    let bridge=fault_core("import time; time.sleep(60)");let start=Instant::now();
    assert_eq!(bridge.call_cancellable(fault_request(),Duration::from_secs(10),&AtomicBool::new(false)).unwrap_err(),"core_write_timeout");
    bridge.shutdown();assert!(start.elapsed()<Duration::from_secs(5));
}
#[cfg(windows)]
#[test]
fn completed_send_watchdog_cannot_later_kill_healthy_core() {
    use std::{sync::atomic::{AtomicBool,Ordering},time::Duration,thread};
    let bridge=fault_core("import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'version':'1.0','id':r['id'],'ok':True,'result':{'healthy':True}}),flush=True)");
    let cancel=AtomicBool::new(false);
    assert_eq!(bridge.call_cancellable(fault_request(),Duration::from_secs(10),&cancel).unwrap()["healthy"],true);
    cancel.store(true,Ordering::Release);thread::sleep(Duration::from_millis(2100));
    assert_eq!(bridge.call(fault_request(),|_|{}).unwrap()["healthy"],true);bridge.shutdown();
}
#[cfg(windows)]
#[test]
fn shutdown_during_stalled_send_is_released_by_scoped_watchdog() {
    use std::{sync::{Arc,atomic::{AtomicBool,Ordering}},time::{Duration,Instant},thread};
    let bridge=fault_core("import time; time.sleep(60)");let worker_core=bridge.clone();let cancel=Arc::new(AtomicBool::new(false));let worker_cancel=cancel.clone();
    let worker=thread::spawn(move||worker_core.call_cancellable(fault_request(),Duration::from_secs(10),&worker_cancel));
    thread::sleep(Duration::from_millis(100));
    let signal=thread::spawn(move||{thread::sleep(Duration::from_millis(100));cancel.store(true,Ordering::Release);});
    let start=Instant::now();bridge.shutdown();signal.join().unwrap();
    assert_eq!(worker.join().unwrap().unwrap_err(),"agent_cancelled");assert!(start.elapsed()<Duration::from_secs(5));
}
#[cfg(windows)]
#[test]
fn cancellation_during_healthy_send_preserves_core_channel() {
    use std::{sync::{Arc,atomic::{AtomicBool,Ordering}},time::Duration,thread};
    let bridge=fault_core("import sys,json,time\nfor line in sys.stdin:\n r=json.loads(line)\n if r['params'].get('warmup'): print(json.dumps({'version':'1.0','id':r['id'],'ok':True,'result':{'healthy':True}}),flush=True); time.sleep(0.1)\n else: print(json.dumps({'version':'1.0','id':r['id'],'ok':True,'result':{'healthy':True}}),flush=True)");
    let mut warmup=fault_request();warmup.params=json!({"warmup":true});bridge.call(warmup,|_|{}).unwrap();
    let cancel=Arc::new(AtomicBool::new(false));let trigger=cancel.clone();
    let signal=thread::spawn(move||{thread::sleep(Duration::from_millis(20));trigger.store(true,Ordering::Release);});
    assert_eq!(bridge.call_cancellable(fault_request(),Duration::from_secs(10),&cancel).unwrap_err(),"agent_cancelled");signal.join().unwrap();
    assert_eq!(bridge.call(fault_request(),|_|{}).unwrap()["healthy"],true);bridge.shutdown();
}
