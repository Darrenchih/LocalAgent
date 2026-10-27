#![cfg(windows)]
#[allow(dead_code)]
#[path="../src/protected_runtime.rs"] mod protected_runtime;
#[allow(dead_code)]
#[path="../src/protected_process.rs"] mod protected_process;
#[path="../src/protected_desktop.rs"] mod protected_desktop;
use protected_desktop::*;
use protected_process::CompatGuard;
use std::{path::{Path,PathBuf},sync::Arc,time::{Duration,Instant}};

fn source()->PathBuf{PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime")}
fn copy_tree(from:&Path,to:&Path){std::fs::create_dir_all(to).unwrap();for entry in std::fs::read_dir(from).unwrap(){let e=entry.unwrap();let target=to.join(e.file_name());if e.file_type().unwrap().is_dir(){copy_tree(&e.path(),&target);}else{std::fs::copy(e.path(),target).unwrap();}}}
fn setup()->(tempfile::TempDir,DesktopRuntime){
    let work=tempfile::tempdir().unwrap();let exe=work.path().join("桌面 APP.exe");std::fs::write(&exe,b"inert path marker").unwrap();
    copy_tree(&source(),&bundle_path(&exe).unwrap());
    let manager=DesktopRuntime::new(&exe,Arc::new(CompatGuard::acquire().unwrap())).unwrap();(work,manager)
}
fn wait(manager:&DesktopRuntime,state:&str)->RuntimeStatus{
    let until=Instant::now()+Duration::from_secs(12);
    while Instant::now()<until{let s=manager.status();if s.state==state{return s;}std::thread::sleep(Duration::from_millis(20));}
    panic!("wanted {state}, got {:?}",manager.status());
}

#[test]
fn real_owner_start_status_stop_is_nonblocking_and_shares_guard(){
    let (_work,m)=setup();assert_eq!(m.status().state,"disabled");
    let now=Instant::now();assert_eq!(m.start().unwrap().state,"starting");assert!(now.elapsed()<Duration::from_millis(100));
    assert!(m.start().is_err());let ready=wait(&m,"ready");assert!(!ready.execution_enabled);assert!(ready.last_health_ms.is_some());
    assert_eq!(m.stop().unwrap().state,"stopping");wait(&m,"stopped");
    assert_eq!(m.start().unwrap().state,"starting");wait(&m,"ready");m.shutdown();assert_eq!(m.status().state,"stopped");
}

#[test]
fn heartbeat_runs_without_user_status_rpc_and_stops_on_exit(){
    let (_work,m)=setup();m.start().unwrap();let first=wait(&m,"ready").last_health_ms.unwrap();
    std::thread::sleep(Duration::from_millis(5500));
    assert!(m.status().last_health_ms.unwrap()>first);
    let pid=m.process_id_for_test().unwrap();
    unsafe{use windows_sys::Win32::{Foundation::CloseHandle,System::Threading::*};let process=OpenProcess(PROCESS_TERMINATE,0,pid);assert!(!process.is_null());assert_ne!(TerminateProcess(process,41),0);CloseHandle(process);}
    let failed=wait(&m,"failed");assert_eq!(failed.error.as_deref(),Some("PEER_EXITED"));
    std::thread::sleep(Duration::from_millis(300));assert_eq!(m.status().state,"failed");m.shutdown();
}

#[test]
fn missing_and_changed_bundle_fail_closed_without_developer_fallback(){
    let (work,m)=setup();let root=bundle_path(&work.path().join("桌面 APP.exe")).unwrap();
    std::fs::remove_file(root.join("_internal/python312.dll")).unwrap();m.start().unwrap();let failed=wait(&m,"failed");assert!(!failed.execution_enabled);assert!(failed.error.is_some());m.shutdown();
}

#[test]
fn stop_during_start_does_not_publish_stale_ready(){
    let (_work,m)=setup();m.start().unwrap();m.stop().unwrap();wait(&m,"stopped");
    std::thread::sleep(Duration::from_millis(200));assert_eq!(m.status().state,"stopped");assert!(m.process_id_for_test().is_none());m.shutdown();
}

#[test]
fn cancellation_followed_by_immediate_restart_keeps_new_request(){
    let (_work,m)=setup();
    for _ in 0..12 {
        m.start().unwrap();
        std::thread::sleep(Duration::from_millis(10));
        m.stop().unwrap();
        assert!(m.start().is_err());
        wait(&m,"stopped");
        m.start().unwrap();wait(&m,"ready");
        m.stop().unwrap();wait(&m,"stopped");
    }
    m.shutdown();
}

#[test]
fn manifest_tamper_rejects_and_does_not_self_accept_new_digest(){
    let (work,m)=setup();let root=bundle_path(&work.path().join("桌面 APP.exe")).unwrap();
    let manifest=root.join("protected-runtime-manifest.json");
    let original=std::fs::read(&manifest).unwrap();
    let mut altered=original.clone();altered.push(b' ');std::fs::write(&manifest,altered).unwrap();
    m.start().unwrap();let failed=wait(&m,"failed");assert!(!failed.execution_enabled);assert!(failed.error.is_some());
    std::fs::write(&manifest,original).unwrap();m.start().unwrap();wait(&m,"ready");m.shutdown();
}

#[test]
fn real_heartbeat_timeout_is_detected_without_ui_polling(){
    let (_work,m)=setup();m.start().unwrap();wait(&m,"ready");let pid=m.process_id_for_test().unwrap();
    unsafe{
        use windows_sys::Win32::{Foundation::CloseHandle,System::Threading::*};
        #[link(name="ntdll")] extern "system"{fn NtSuspendProcess(process:windows_sys::Win32::Foundation::HANDLE)->i32;}
        let p=OpenProcess(PROCESS_SUSPEND_RESUME|windows_sys::Win32::Storage::FileSystem::SYNCHRONIZE,0,pid);assert!(!p.is_null());assert_eq!(NtSuspendProcess(p),0);
        let failed=wait(&m,"failed");assert_eq!(failed.error.as_deref(),Some("IO_TIMEOUT"));
        assert_eq!(WaitForSingleObject(p,3000),0);CloseHandle(p);
    }
    m.shutdown();
}

#[test]
fn desktop_shutdown_during_blocked_heartbeat_has_a_finite_budget(){
    let (_work,m)=setup();m.start().unwrap();wait(&m,"ready");let pid=m.process_id_for_test().unwrap();
    unsafe{
        use windows_sys::Win32::{Foundation::CloseHandle,System::Threading::*};
        #[link(name="ntdll")] extern "system"{fn NtSuspendProcess(process:windows_sys::Win32::Foundation::HANDLE)->i32;}
        let process=OpenProcess(PROCESS_SUSPEND_RESUME|windows_sys::Win32::Storage::FileSystem::SYNCHRONIZE,0,pid);assert!(!process.is_null());assert_eq!(NtSuspendProcess(process),0);
        std::thread::sleep(Duration::from_millis(5200));
        let began=Instant::now();m.shutdown();assert!(began.elapsed()<Duration::from_secs(7));
        assert_eq!(WaitForSingleObject(process,0),0);CloseHandle(process);
    }
    assert!(m.process_id_for_test().is_none());
}

#[test]
fn deployment_path_is_only_executable_relative_and_actions_are_finite(){
    let root=bundle_path(Path::new("C:/中文 app/LocalAgent.exe")).unwrap();
    assert!(root.ends_with("runtime/protected/localagent-management-v1"));
    assert!(validate_action("start").is_ok());assert!(validate_action("status").is_ok());assert!(validate_action("stop").is_ok());
    for method in ["enable","grant","tools/call","exec","dispatch"]{assert!(validate_action(method).is_err());}
}
