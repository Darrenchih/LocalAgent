#![cfg(windows)]
#[allow(dead_code)]
#[path="../src/protected_runtime.rs"] mod protected_runtime;
#[path="../src/protected_process.rs"] mod protected_process;
use protected_process::*;
use std::{path::PathBuf,time::{Duration,Instant}};
use windows_sys::Win32::System::Threading::{GetCurrentProcess,GetProcessHandleCount};
use windows_sys::Win32::{Foundation::{CloseHandle,ERROR_ALREADY_EXISTS,GetLastError},System::Threading::CreateMutexW};

fn fixture()->PathBuf{PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../work/review-040/native/review040-inert.exe").canonicalize().unwrap()}
#[test]
fn native_suspended_creation_is_already_in_job_and_kill_reaps_it(){
    let mut p=NativeProcess::fixture(&fixture(),&["sleep"],true).unwrap();
    assert!(p.in_job().unwrap());assert!(!p.exited());
    p.terminate(Duration::from_secs(3)).unwrap();assert!(p.exited());assert_eq!(p.active_count().unwrap(),0);
}
#[test]
fn blocked_native_read_and_write_cancel_without_workers_or_leaks(){
    let begin=Instant::now();let mut p=NativeProcess::fixture(&fixture(),&["sleep"],false).unwrap();
    assert!(p.read_frame(Instant::now()+Duration::from_millis(150)).is_err());
    assert!(p.exited());assert_eq!(p.active_count().unwrap(),0);assert!(begin.elapsed()<Duration::from_secs(4));
    let mut p=NativeProcess::fixture(&fixture(),&["no-read"],false).unwrap();
    assert!(p.write_all(&vec![b'x';1024*1024],Instant::now()+Duration::from_millis(150)).is_err());
    assert!(p.exited());assert_eq!(p.active_count().unwrap(),0);
}
#[test]
fn stderr_flood_is_drained_and_stdout_oversize_is_terminal(){
    let mut p=NativeProcess::fixture(&fixture(),&["stderr-flood"],false).unwrap();
    assert_eq!(p.read_frame(Instant::now()+Duration::from_secs(5)).unwrap(),b"done\n");
    assert!(p.diagnostic_bytes()<=65536);p.terminate(Duration::from_secs(3)).unwrap();
    let mut p=NativeProcess::fixture(&fixture(),&["oversize"],false).unwrap();
    assert!(p.read_frame(Instant::now()+Duration::from_secs(3)).is_err());assert!(p.exited());
}
#[test]
fn actual_descendant_is_inherited_and_reaped_without_name_killing(){
    let mut p=NativeProcess::fixture(&fixture(),&["descendant"],false).unwrap();
    let raw=p.read_frame(Instant::now()+Duration::from_secs(3)).unwrap();
    let pid=String::from_utf8(raw).unwrap().trim().parse::<u32>().unwrap();
    let child=ObservedProcess::open(pid).unwrap();assert!(p.contains(&child).unwrap());
    assert!(p.active_count().unwrap()>=2);p.terminate(Duration::from_secs(3)).unwrap();
    assert!(child.wait(Duration::from_secs(2)));assert_eq!(p.active_count().unwrap(),0);
}
#[test]
fn real_frozen_host_handshake_status_and_shutdown(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime").canonicalize().unwrap();
    let expected=env!("PROTECTED_RUNTIME_MANIFEST_SHA256");
    let mut s=NativeSession::start(&root,expected).unwrap();
    let status=s.call("status").unwrap();assert_eq!(status["execution_enabled"],false);
    assert_eq!(status["child_roles"]["oi"],"not_started");
    let loaded=s.verify_loaded_modules().unwrap();assert!(!loaded.is_empty());
    s.shutdown().unwrap();assert!(s.process.exited());assert_eq!(s.process.active_count().unwrap(),0);
}

#[test]
fn production_binding_refuses_old_review_bundle_even_with_its_known_digest(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../work/review-042/baseline/protected-runtime-041").canonicalize().unwrap();
    assert!(NativeSession::start(&root,"4d546f42166b1351853a6039d1fe2e7817ee7841bbaa3273636008f5763f80a1").is_err());
}

#[test]
fn native_malformed_wrong_role_and_bad_shutdown_fail_closed(){
    for mode in ["malformed","wrong-identity"]{
        let mut s=NativeSession::fixture_session(&fixture(),&[mode]).unwrap();
        assert!(s.call("health").is_err());assert!(s.process.exited());assert_eq!(s.process.active_count().unwrap(),0);
    }
    let mut s=NativeSession::fixture_session(&fixture(),&["shutdown-bad"]).unwrap();
    s.call("health").unwrap();
    assert!(s.shutdown().is_err(),"bad ACK must not be hidden by successful forced cleanup");
    assert!(s.process.exited());
}

#[test]
fn acknowledged_but_hanging_shutdown_reserves_forced_cleanup_inside_total_budget(){
    let mut s=NativeSession::fixture_session(&fixture(),&["shutdown-ack-hang"]).unwrap();
    s.call("health").unwrap();let begin=Instant::now();
    let result=s.shutdown();assert!(result.is_ok(),"cleanup must finish before the total deadline: {result:?}");
    assert!(begin.elapsed()<Duration::from_secs(3),"forced cleanup must start at normal shutdown grace, not after total budget");
    assert!(s.process.exited());assert_eq!(s.process.active_count().unwrap(),0);
}

#[test]
fn bundle_pins_reject_mutation_hardlink_and_junction_before_launch(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime").canonicalize().unwrap();
    let digest=env!("PROTECTED_RUNTIME_MANIFEST_SHA256");
    let pin=PinnedBundle::open(&root,digest,Instant::now()+Duration::from_secs(10)).unwrap();
    assert!(pin.files()>=59);assert_eq!(pin.root(),root);assert!(pin.entry().exists());
    assert!(std::fs::OpenOptions::new().write(true).open(pin.entry()).is_err());
    assert!(std::fs::rename(pin.entry(),pin.root().join("replaced.exe")).is_err());
    assert!(PinnedBundle::open(&root,"00",Instant::now()+Duration::from_secs(10)).is_err());
    drop(pin);
    let target=root.join("protected-host-management.exe");
    let alias=root.parent().unwrap().join("review040-hardlink.exe");
    std::fs::hard_link(&target,&alias).unwrap();
    let result=PinnedBundle::open(&root,digest,Instant::now()+Duration::from_secs(10));
    std::fs::remove_file(&alias).unwrap();assert!(result.is_err());
}

#[test]
fn repeated_cancellation_returns_handle_count_to_baseline(){unsafe{
    let mut before=0;assert_ne!(GetProcessHandleCount(GetCurrentProcess(),&mut before),0);
    for _ in 0..8{let mut p=NativeProcess::fixture(&fixture(),&["sleep"],false).unwrap();
        assert!(p.read_frame(Instant::now()+Duration::from_millis(30)).is_err());assert_eq!(p.active_count().unwrap(),0);}
    let mut after=0;assert_ne!(GetProcessHandleCount(GetCurrentProcess(),&mut after),0);
    assert!(after<=before+1,"before={before} after={after}");
}}

#[test]
#[ignore = "internal isolated owner fixture, launched explicitly by native tests"]
fn native_owner_fixture(){
    let control=PathBuf::from(std::env::var_os("REVIEW040_CONTROL").unwrap());
    let mode=std::env::var("REVIEW040_MODE").unwrap();
    if mode=="guard-old" || mode=="guard-new"{
        let legacy=if mode=="guard-old"{
            let name:Vec<u16>="Local\\LocalAgentDesktop036".encode_utf16().chain(Some(0)).collect();
            let handle=unsafe{CreateMutexW(std::ptr::null(),0,name.as_ptr())};
            let okay=!handle.is_null() && unsafe{GetLastError()}!=ERROR_ALREADY_EXISTS;
            Some((handle,okay))
        }else{None};
        let newer=if mode=="guard-new"{Some(CompatGuard::acquire())}else{None};
        let acquired=legacy.as_ref().map(|p|p.1).unwrap_or_else(||newer.as_ref().unwrap().is_ok());
        std::fs::write(&control,if acquired{"acquired"}else{"refused"}).unwrap();
        std::thread::sleep(Duration::from_secs(30));
        if let Some((handle,_))=legacy{unsafe{CloseHandle(handle);}}drop(newer);return;
    }
    if mode=="before-create"{std::fs::write(&control,"0").unwrap();std::thread::sleep(Duration::from_secs(30));return;}
    if mode=="frozen"{
        let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime").canonicalize().unwrap();
        let s=NativeSession::start(&root,env!("PROTECTED_RUNTIME_MANIFEST_SHA256")).unwrap();
        std::fs::write(&control,s.process.pid().to_string()).unwrap();std::thread::sleep(Duration::from_secs(30));drop(s);return;
    }
    let p=NativeProcess::fixture(&fixture(),&["sleep"],mode=="suspended").unwrap();
    assert!(p.in_job().unwrap());std::fs::write(&control,p.pid().to_string()).unwrap();
    std::thread::sleep(Duration::from_secs(30));drop(p);
}
fn owner(mode:&str,path:&std::path::Path)->NativeProcess{
    NativeProcess::fixture_env(&std::env::current_exe().unwrap(),&["--exact","native_owner_fixture","--ignored","--nocapture"],
        &[("REVIEW040_CONTROL".into(),path.to_string_lossy().into_owned()),("REVIEW040_MODE".into(),mode.into())]).unwrap()
}
fn wait_control(path:&std::path::Path)->String{
    let until=Instant::now()+Duration::from_secs(5);
    while Instant::now()<until{if let Ok(text)=std::fs::read_to_string(path){if !text.is_empty(){return text;}}std::thread::sleep(Duration::from_millis(10));}
    panic!("isolated owner did not signal readiness")
}

#[test]
fn killing_supervisor_reaps_nested_job_at_verified_creation_checkpoints(){
    let work=tempfile::tempdir().unwrap();
    for mode in ["before-create","suspended","running","frozen"]{
        for round in 0..3{
            let path=work.path().join(format!("{mode}-{round}.txt"));let mut parent=owner(mode,&path);
            let pid=wait_control(&path).parse::<u32>().unwrap();
            let child=if pid==0{None}else{Some(ObservedProcess::open(pid).unwrap())};
            parent.kill_primary_only().unwrap();
            if let Some(child)=child{assert!(child.wait(Duration::from_secs(3)),"mode={mode}");}
            parent.wait_exit(Instant::now()+Duration::from_secs(3)).unwrap();assert_eq!(parent.active_count().unwrap(),0);
            println!("REVIEW040 parent-kill checkpoint={mode} round={round} owned_remaining=0");
        }
    }
}

#[test]
fn real_036_compatibility_guard_is_bidirectional_and_race_safe(){
    let work=tempfile::tempdir().unwrap();
    for (first,second) in [("guard-old","guard-new"),("guard-new","guard-old")]{
        let one=work.path().join(format!("{first}-first"));let two=work.path().join(format!("{second}-second"));
        let mut p1=owner(first,&one);assert_eq!(wait_control(&one),"acquired","existing app guard may already be held");
        let mut p2=owner(second,&two);assert_eq!(wait_control(&two),"refused");
        p2.terminate(Duration::from_secs(2)).unwrap();p1.terminate(Duration::from_secs(2)).unwrap();
    }
    for round in 0..3{
        let one=work.path().join(format!("race-old-{round}"));let two=work.path().join(format!("race-new-{round}"));
        let mut p1=owner("guard-old",&one);let mut p2=owner("guard-new",&two);
        let accepted=[wait_control(&one),wait_control(&two)].into_iter().filter(|s|s=="acquired").count();
        assert_eq!(accepted,1);p1.terminate(Duration::from_secs(2)).unwrap();p2.terminate(Duration::from_secs(2)).unwrap();
    }
}

fn copy_bundle(source:&std::path::Path,destination:&std::path::Path){
    std::fs::create_dir_all(destination).unwrap();
    for entry in std::fs::read_dir(source).unwrap(){let entry=entry.unwrap();let target=destination.join(entry.file_name());
        if entry.file_type().unwrap().is_dir(){copy_bundle(&entry.path(),&target);}else{std::fs::copy(entry.path(),target).unwrap();}}
}

#[test]
fn relocated_unicode_bundle_runs_with_empty_path_and_missing_dependency_is_refused(){
    let source=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime").canonicalize().unwrap();
    let pin=env!("PROTECTED_RUNTIME_MANIFEST_SHA256");
    let work=tempfile::tempdir().unwrap();let first=work.path().join("中文 空格 one");copy_bundle(&source,&first);
    {let mut s=NativeSession::start(&first,pin).unwrap();s.call("status").unwrap();
        let loaded=s.verify_loaded_modules().unwrap();assert!(loaded.iter().any(|p|p.contains("python312.dll") && p.contains("_internal")));
        println!("REVIEW040 verified_loaded_modules={}",serde_json::to_string(&loaded).unwrap());s.shutdown().unwrap();}
    let second=work.path().join("搬移 root two");std::fs::rename(&first,&second).unwrap();
    {let mut s=NativeSession::start(&second,pin).unwrap();s.shutdown().unwrap();}
    std::fs::remove_file(second.join("_internal/python312.dll")).unwrap();
    assert!(NativeSession::start(&second,pin).is_err());
}

#[test]
fn junction_alias_is_refused_and_late_unlisted_library_is_detected(){
    let source=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime").canonicalize().unwrap();
    let pin=env!("PROTECTED_RUNTIME_MANIFEST_SHA256");
    let junction=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../work/review-040/native/bundle-junction");
    assert!(junction.exists(),"acceptance script must prepare the isolated junction");
    assert!(PinnedBundle::open(&junction,pin,Instant::now()+Duration::from_secs(10)).is_err());
    let work=tempfile::tempdir().unwrap();let copy=work.path().join("bundle");copy_bundle(&source,&copy);
    let mut s=NativeSession::start(&copy,pin).unwrap();
    std::fs::write(copy.join("_internal/injected.dll"),b"not executable").unwrap();
    assert!(s.verify_loaded_modules().is_err(),"membership must be checked again before publishing a healthy result");
    assert!(s.process.exited());
}

#[test]
fn real_frozen_host_refuses_execution_methods_and_exits(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/protected-runtime").canonicalize().unwrap();
    for method in ["tools/call","grant","permit","dispatch","enable","exec"]{
        let bundle=PinnedBundle::open(&root,env!("PROTECTED_RUNTIME_MANIFEST_SHA256"),Instant::now()+Duration::from_secs(10)).unwrap();
        let mut p=NativeProcess::launch(bundle,Instant::now()).unwrap();
        let frame=format!("{}\n",serde_json::json!({"v":1,"id":uuid::Uuid::new_v4().to_string(),"session":uuid::Uuid::new_v4().to_string(),"method":method,"params":{}}));
        let end=Instant::now()+Duration::from_secs(3);p.write_all(frame.as_bytes(),end).unwrap();
        let answer:serde_json::Value=serde_json::from_slice(&p.read_frame(end).unwrap()).unwrap();
        assert_eq!(answer["error"]["code"],"EXECUTION_DISABLED");
        p.wait_natural_exit(Instant::now()+Duration::from_secs(3)).unwrap();assert!(p.exited());assert_eq!(p.active_count().unwrap(),0);
    }
}
