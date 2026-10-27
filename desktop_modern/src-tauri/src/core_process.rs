use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

pub const API_VERSION: &str = "1.0";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoreLaunch {
    pub program: String,
    pub args: Vec<String>,
    pub working_dir: String,
}

pub fn resolve_core_launch(
    executable: &Path,
    sidecar: &Path,
    database: &Path,
    packaged: bool,
) -> CoreLaunch {
    if packaged {
        return CoreLaunch {
            program: sidecar.to_string_lossy().into_owned(),
            args: vec!["--stdio".into(), "--db".into(), database.to_string_lossy().into_owned()],
            working_dir: executable.parent().unwrap_or(Path::new(".")).to_string_lossy().into_owned(),
        };
    }
    let project_root = sidecar
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .and_then(Path::parent)
        .unwrap_or(Path::new("."));
    CoreLaunch {
        program: std::env::var("AGENT_PYTHON").unwrap_or_else(|_| "python".into()),
        args: vec![
            "-u".into(), "-m".into(), "agent_core.server".into(), "--stdio".into(),
            "--db".into(), database.to_string_lossy().into_owned(),
        ],
        working_dir: project_root.to_string_lossy().into_owned(),
    }
}

pub fn migrate_database_if_absent(legacy: &Path, destination: &Path) -> Result<bool, String> {
    if destination.exists() || !legacy.is_file() {
        return Ok(false);
    }
    let parent = destination.parent().ok_or("core_data_parent_unavailable")?;
    std::fs::create_dir_all(parent).map_err(|error| format!("core_data_create_failed: {error}"))?;
    std::fs::copy(legacy, destination).map_err(|error| format!("core_data_migration_failed: {error}"))?;
    Ok(true)
}

#[derive(Debug, Clone, PartialEq)]
pub enum RoutedResponse {
    Delta { request_id: String, content: String },
    Final { request_id: String, value: Value },
}

impl RoutedResponse {
    pub fn request_id(&self) -> &str {
        match self {
            Self::Delta { request_id, .. } | Self::Final { request_id, .. } => request_id,
        }
    }
}

pub fn encode_request(request_id: &str, action: &str, params: Value) -> String {
    let value = json!({
        "version": API_VERSION,
        "id": request_id,
        "action": action,
        "params": params,
    });
    format!("{}\n", serde_json::to_string(&value).expect("JSON value must serialize"))
}

pub fn route_response(line: &str) -> Result<RoutedResponse, String> {
    let value: Value = serde_json::from_str(line).map_err(|_| "core_malformed_json".to_string())?;
    if value.get("version").and_then(Value::as_str) != Some(API_VERSION) {
        return Err("core_protocol_version".into());
    }
    let request_id = value
        .get("id")
        .and_then(Value::as_str)
        .filter(|id| !id.is_empty())
        .ok_or_else(|| "core_missing_request_id".to_string())?
        .to_string();
    if let Some(event) = value.get("event") {
        if event.get("type").and_then(Value::as_str) != Some("delta") {
            return Err("core_unknown_event".into());
        }
        let content = event
            .get("content")
            .and_then(Value::as_str)
            .ok_or_else(|| "core_invalid_delta".to_string())?
            .to_string();
        return Ok(RoutedResponse::Delta { request_id, content });
    }
    Ok(RoutedResponse::Final { request_id, value })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LifecycleState {
    Starting,
    Running,
    Stopped,
}

#[derive(Debug)]
pub struct CoreLifecycle {
    state: LifecycleState,
}

impl CoreLifecycle {
    pub fn new() -> Self {
        Self { state: LifecycleState::Starting }
    }

    pub fn mark_running(&mut self) -> Result<(), String> {
        if self.state != LifecycleState::Starting {
            return Err("core_invalid_lifecycle_transition".into());
        }
        self.state = LifecycleState::Running;
        Ok(())
    }

    pub fn mark_stopped(&mut self) {
        self.state = LifecycleState::Stopped;
    }

    pub fn is_stopped(&self) -> bool {
        self.state == LifecycleState::Stopped
    }
}

impl Default for CoreLifecycle {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CoreRequestInput {
    pub version: String,
    pub request_id: Option<String>,
    pub action: String,
    #[serde(default)]
    pub params: Value,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeltaPayload {
    pub request_id: String,
    pub content: String,
}

type Pending = Arc<Mutex<HashMap<String, Sender<RoutedResponse>>>>;

pub struct CoreBridge {
    job: Mutex<Option<crate::process_job::OwnedJob>>,
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Pending,
    lifecycle: Mutex<CoreLifecycle>,
}

impl CoreBridge {
    pub fn spawn(launch: CoreLaunch) -> Result<Arc<Self>, String> {
        let mut child = Command::new(&launch.program)
            .args(&launch.args)
            .current_dir(&launch.working_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .creation_flags_no_window()
            .spawn()
            .map_err(|error| format!("core_start_failed: {error}"))?;

        let job = crate::process_job::OwnedJob::contain(&mut child)?;
        let stdin = child.stdin.take().ok_or("core_stdin_unavailable")?;
        let stdout = child.stdout.take().ok_or("core_stdout_unavailable")?;
        let stderr = child.stderr.take().ok_or("core_stderr_unavailable")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let reader_pending = Arc::clone(&pending);

        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(response) = route_response(&line) else { continue };
                let request_id = response.request_id().to_string();
                let is_final = matches!(response, RoutedResponse::Final { .. });
                let sender = reader_pending.lock().ok().and_then(|map| map.get(&request_id).cloned());
                if let Some(sender) = sender {
                    let _ = sender.send(response);
                }
                if is_final {
                    if let Ok(mut map) = reader_pending.lock() {
                        map.remove(&request_id);
                    }
                }
            }
            if let Ok(mut map) = reader_pending.lock() {
                map.clear();
            }
        });
        thread::spawn(move || {
            for _ in BufReader::new(stderr).lines() {}
        });

        let mut lifecycle = CoreLifecycle::new();
        lifecycle.mark_running()?;
        Ok(Arc::new(Self {
            job: Mutex::new(Some(job)),
            child: Mutex::new(child),
            stdin: Mutex::new(Some(stdin)),
            pending,
            lifecycle: Mutex::new(lifecycle),
        }))
    }

    pub fn call<F>(&self, input: CoreRequestInput, on_delta: F) -> Result<Value, String>
    where F: Fn(DeltaPayload),
    { self.call_with_timeout(input, on_delta, Duration::from_secs(120)) }

    pub fn call_with_timeout<F>(&self, input: CoreRequestInput, on_delta: F, timeout: Duration) -> Result<Value, String>
    where F: Fn(DeltaPayload),
    { self.call_controlled(input,on_delta,timeout,None) }

    pub fn call_cancellable(&self,input:CoreRequestInput,timeout:Duration,cancel:&std::sync::atomic::AtomicBool)->Result<Value,String>{
        self.call_controlled(input,|_|{},timeout,Some(cancel))
    }

    /// A failed cancellable send is fatal to this private Core channel. Closing
    /// its owned job releases blocked anonymous-pipe writers without taking stdin.
    fn abort_channel(&self) {
        if let Ok(mut lifecycle) = self.lifecycle.lock() { lifecycle.mark_stopped(); }
        if let Ok(mut pending) = self.pending.lock() { pending.clear(); }
        if let Ok(mut job) = self.job.lock() { drop(job.take()); }
        if let Ok(mut child) = self.child.lock() { let _ = child.kill(); }
    }

    fn send_cancellable(&self, encoded: &[u8], cancel: &std::sync::atomic::AtomicBool, timeout: Duration) -> Result<(), String> {
        // One scoped, joined watchdog per send. Completion and abort are selected
        // under the same lock, so a completed send can never be killed later.
        if cancel.load(std::sync::atomic::Ordering::Acquire) { return Err("agent_cancelled".into()); }
        let stage = Mutex::new((false, None::<String>));
        let deadline = std::time::Instant::now() + timeout.min(Duration::from_secs(2));
        thread::scope(|scope| {
            scope.spawn(|| {
                let mut cancellation_seen = None::<std::time::Instant>;
                loop {
                {
                    let mut state = stage.lock().unwrap();
                    if state.0 { return; }
                    let now = std::time::Instant::now();
                    if cancel.load(std::sync::atomic::Ordering::Acquire) && cancellation_seen.is_none() {
                        cancellation_seen = Some(now);
                    }
                    // Give a healthy writer a short opportunity to finish. Only
                    // a persistently blocked send makes task cancellation fatal
                    // to the shared Core channel; the absolute send limit remains.
                    let reason = if cancellation_seen.is_some_and(|seen| now.duration_since(seen) >= Duration::from_millis(200)) { Some("agent_cancelled") }
                        else if now >= deadline { Some("core_write_timeout") } else { None };
                    if let Some(reason) = reason {
                        state.0 = true;
                        state.1 = Some(reason.into());
                        self.abort_channel();
                        return;
                    }
                }
                thread::sleep(Duration::from_millis(20));
                }
            });
            let result = self.stdin.lock().map_err(|_| "core_lock_failed".to_string())?
                .as_mut().ok_or_else(|| "core_stopped".to_string())
                .and_then(|stdin| stdin.write_all(encoded).and_then(|_| stdin.flush()).map_err(|e| format!("core_write_failed: {e}")));
            let mut state = stage.lock().unwrap();
            state.0 = true;
            if let Some(reason) = &state.1 { Err(reason.clone()) } else { result }
        })
    }

    fn call_controlled<F>(&self,input:CoreRequestInput,on_delta:F,timeout:Duration,cancel:Option<&std::sync::atomic::AtomicBool>)->Result<Value,String>
    where F:Fn(DeltaPayload),
    {
        if input.version != API_VERSION {
            return Err("unsupported_api_version".into());
        }
        if self.lifecycle.lock().map_err(|_| "core_lock_failed")?.is_stopped() {
            return Err("core_stopped".into());
        }
        let request_id = input.request_id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let (sender, receiver): (Sender<RoutedResponse>, Receiver<RoutedResponse>) = mpsc::channel();
        self.pending.lock().map_err(|_| "core_lock_failed")?.insert(request_id.clone(), sender);

        let encoded = encode_request(&request_id, &input.action, input.params);
        let deadline=std::time::Instant::now()+timeout;
        let write_result = if let Some(cancel) = cancel {
            self.send_cancellable(encoded.as_bytes(), cancel, timeout)
        } else {
            self
            .stdin
            .lock()
            .map_err(|_| "core_lock_failed".to_string())?
            .as_mut()
            .ok_or_else(|| "core_stopped".to_string())
            .and_then(|stdin| {
                stdin.write_all(encoded.as_bytes()).and_then(|_| stdin.flush())
                    .map_err(|error| format!("core_write_failed: {error}"))
            })
        };
        if let Err(error) = write_result {
            if let Ok(mut map) = self.pending.lock() { map.remove(&request_id); }
            return Err(error);
        }

        loop {
            if cancel.is_some_and(|c|c.load(std::sync::atomic::Ordering::Acquire)){
                if let Ok(mut map)=self.pending.lock(){map.remove(&request_id);}return Err("agent_cancelled".into());
            }
            let wait=if cancel.is_some(){
                if std::time::Instant::now()>=deadline{if let Ok(mut map)=self.pending.lock(){map.remove(&request_id);}return Err("core_response_timeout".into());}
                deadline.saturating_duration_since(std::time::Instant::now()).min(Duration::from_millis(100))
            }else{timeout};
            match receiver.recv_timeout(wait) {
                Ok(RoutedResponse::Delta { request_id, content }) => on_delta(DeltaPayload { request_id, content }),
                Ok(RoutedResponse::Final { value, .. }) => {
                    if value.get("ok").and_then(Value::as_bool) == Some(true) {
                        return Ok(value.get("result").cloned().unwrap_or(Value::Null));
                    }
                    let message = value.pointer("/error/message").and_then(Value::as_str).unwrap_or("core_error");
                    return Err(message.to_string());
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if cancel.is_some(){continue;}
                    if let Ok(mut map) = self.pending.lock() { map.remove(&request_id); }
                    return Err("core_response_timeout".into());
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => return Err("core_channel_closed".into()),
            }
        }
    }

    pub fn shutdown(&self) {
        if let Ok(mut stdin) = self.stdin.lock() {
            stdin.take();
        }
        if let Ok(mut child) = self.child.lock() {
            for _ in 0..15 {
                if child.try_wait().ok().flatten().is_some() { break; }
                thread::sleep(Duration::from_millis(100));
            }
            if child.try_wait().ok().flatten().is_none() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        if let Ok(mut job) = self.job.lock() { job.take(); }
        if let Ok(mut lifecycle) = self.lifecycle.lock() {
            lifecycle.mark_stopped();
        }
    }
}

#[cfg(windows)]
trait CommandNoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

#[cfg(windows)]
impl CommandNoWindow for Command {
    fn creation_flags_no_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(0x08000000 | 0x00000004)
    }
}

#[cfg(not(windows))]
trait CommandNoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

#[cfg(not(windows))]
impl CommandNoWindow for Command {
    fn creation_flags_no_window(&mut self) -> &mut Self { self }
}
