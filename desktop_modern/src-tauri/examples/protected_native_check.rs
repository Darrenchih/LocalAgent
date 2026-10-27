//! Standalone native acceptance runner, deliberately separate from Desktop targets.
#[allow(dead_code)]
#[path="../src/protected_runtime.rs"] mod protected_runtime;
#[allow(dead_code)]
#[path="../src/protected_process.rs"] mod protected_process;
use std::{path::PathBuf,time::{Duration,Instant}};
const MANIFEST_SHA256:&str=env!("PROTECTED_RUNTIME_MANIFEST_SHA256");
fn main(){
    let args:Vec<_>=std::env::args_os().collect();
    if args.len()!=3 || (args[1]!="--check" && args[1]!="--verify-only"){
        eprintln!("usage: protected_native_check --check|--verify-only <bundle-directory>");std::process::exit(2);
    }
    let root=PathBuf::from(&args[2]);
    let result=(||{
        if args[1]=="--verify-only"{
            let bundle=protected_process::PinnedBundle::open(&root,MANIFEST_SHA256,Instant::now()+Duration::from_secs(10))?;
            println!("{}",serde_json::json!({"state":"verified_not_started","execution_enabled":false,"artifact_count":bundle.files()}));
        }else{
            let mut session=protected_process::NativeSession::start(&root,MANIFEST_SHA256)?;
            let status=session.call("status")?;let modules=session.verify_loaded_modules()?;
            session.shutdown()?;
            println!("{}",serde_json::json!({"state":"stopped","prior_status":status,"verified_loaded_modules":modules,"owned_remaining":session.process.active_count()?}));
        }
        Ok::<(),protected_runtime::Fault>(())
    })();
    if let Err(error)=result{eprintln!("{}",error.0);std::process::exit(1);}
}
