//! One actor owns the native session; UI only takes a short snapshot lock.
use crate::protected_process::{CompatGuard,NativeSession};
use serde::Serialize;
use std::{path::{Path,PathBuf},sync::{Arc,Mutex,mpsc::{self,SyncSender,RecvTimeoutError},atomic::{AtomicBool,Ordering}},thread::{self,JoinHandle},time::{Duration,SystemTime,UNIX_EPOCH}};
pub const BUNDLE_ID:&str=env!("PROTECTED_RUNTIME_BUNDLE_ID");
pub const ARTIFACT_VERSION:&str=env!("PROTECTED_RUNTIME_ARTIFACT_VERSION");
pub const MANIFEST_PIN:&str=env!("PROTECTED_RUNTIME_MANIFEST_SHA256");
#[derive(Debug,Clone,Serialize)]
pub struct RuntimeStatus{
    pub state:String,pub execution_enabled:bool,pub error:Option<String>,pub bundle_id:String,
    pub artifact_version:String,pub last_health_ms:Option<u64>,
}
struct Shared{status:RuntimeStatus,generation:u64,pid:Option<u32>}
enum Command{Start(u64)}
pub struct DesktopRuntime{shared:Arc<Mutex<Shared>>,sender:SyncSender<Command>,quit:Arc<AtomicBool>,worker:Mutex<Option<JoinHandle<()>>>}
fn now()->u64{SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64}
pub fn validate_action(action:&str)->Result<(),String>{match action{"start"|"status"|"stop"=>Ok(()),_=>Err("EXECUTION_DISABLED".into())}}
pub fn bundle_path(executable:&Path)->Result<PathBuf,String>{
    if !executable.is_absolute(){return Err("RUNTIME_MISSING".into());}
    Ok(executable.parent().ok_or("RUNTIME_MISSING")?.join("runtime/protected").join(BUNDLE_ID))
}
fn set(shared:&Arc<Mutex<Shared>>,state:&str,error:Option<&str>,pid:Option<u32>,healthy:bool){
    if let Ok(mut value)=shared.lock(){value.status.state=state.into();value.status.error=error.map(str::to_owned);value.pid=pid;if healthy{value.status.last_health_ms=Some(now());}}
}
fn close(session:&mut Option<NativeSession>,shared:&Arc<Mutex<Shared>>,generation:u64){
    let result=if let Some(mut session)=session.take(){session.shutdown()}else{Ok(())};
    if let Ok(mut value)=shared.lock(){if value.generation==generation{value.status.state=if result.is_ok(){"stopped"}else{"failed"}.into();value.status.error=result.err().map(|e|e.0.to_owned());value.pid=None;}}
}
impl DesktopRuntime{
    pub fn new(executable:&Path,guard:Arc<CompatGuard>)->Result<Self,String>{
        let root=bundle_path(executable)?;let (sender,receiver)=mpsc::sync_channel(1);
        let shared=Arc::new(Mutex::new(Shared{status:RuntimeStatus{state:"disabled".into(),execution_enabled:false,error:None,
            bundle_id:BUNDLE_ID.into(),artifact_version:ARTIFACT_VERSION.into(),last_health_ms:None},generation:0,pid:None}));
        let quit=Arc::new(AtomicBool::new(false));
        let s=shared.clone();let quitting=quit.clone();
        let worker=thread::Builder::new().name("protected-runtime-owner".into()).spawn(move||{
            let panic_state=s.clone();
            let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||{
                let mut session:Option<NativeSession>=None;
                loop{
                    if quitting.load(Ordering::Acquire){let generation=s.lock().map(|v|v.generation).unwrap_or(0);close(&mut session,&s,generation);break;}
                    let pending_stop=s.lock().ok().and_then(|v|if v.status.state=="stopping"{Some(v.generation)}else{None});
                    if let Some(generation)=pending_stop{close(&mut session,&s,generation);}
                    match receiver.recv_timeout(Duration::from_millis(100)){
                        Ok(Command::Start(generation))=>{
                            let current=s.lock().map(|v|v.generation==generation && v.status.state=="starting").unwrap_or(false);
                            if !current{continue;}
                            match NativeSession::start_with_guard(&root,MANIFEST_PIN,guard.clone()){
                                Ok(native)=>{
                                    let pid=native.process.pid();session=Some(native);
                                    let committed=if let Ok(mut value)=s.lock(){
                                        if value.generation==generation && value.status.state=="starting"{
                                            value.status.state="ready".into();value.status.error=None;value.pid=Some(pid);value.status.last_health_ms=Some(now());true
                                        }else{false}
                                    }else{false};
                                    if !committed{let cancelled=s.lock().map(|v|v.generation).unwrap_or(generation);close(&mut session,&s,cancelled);}
                                },
                                Err(error)=>{if let Ok(mut value)=s.lock(){if value.generation==generation{value.status.state="failed".into();value.status.error=Some(error.0.into());value.pid=None;}}},
                            }
                        },
                        Err(RecvTimeoutError::Disconnected)=>{let generation=s.lock().map(|v|v.generation).unwrap_or(0);close(&mut session,&s,generation);break;},
                        Err(RecvTimeoutError::Timeout)=>{},
                    }
                    if let Some(native)=session.as_mut(){
                        match native.maintain(){
                            Ok(true)=>{if let Ok(mut value)=s.lock(){value.status.last_health_ms=Some(now());}},
                            Ok(false)=>{},
                            Err(error)=>{session.take();set(&s,"failed",Some(error.0),None,false);},
                        }
                    }
                }
            }));
            if result.is_err(){set(&panic_state,"failed",Some("WORKER_EXITED"),None,false);}
        }).map_err(|_|"WORKER_UNAVAILABLE".to_owned())?;
        Ok(Self{shared,sender,quit,worker:Mutex::new(Some(worker))})
    }
    pub fn status(&self)->RuntimeStatus{
        self.shared.lock().map(|v|v.status.clone()).unwrap_or_else(|_|RuntimeStatus{state:"failed".into(),execution_enabled:false,error:Some("STATE_UNAVAILABLE".into()),bundle_id:BUNDLE_ID.into(),artifact_version:ARTIFACT_VERSION.into(),last_health_ms:None})
    }
    pub fn start(&self)->Result<RuntimeStatus,String>{
        if self.quit.load(Ordering::Acquire){return Err("OWNER_STOPPED".into());}
        let mut shared=self.shared.lock().map_err(|_|"STATE_UNAVAILABLE")?;
        if !matches!(shared.status.state.as_str(),"disabled"|"stopped"|"failed"){return Err("RUNTIME_BUSY".into());}
        shared.generation+=1;let generation=shared.generation;shared.status.state="starting".into();shared.status.error=None;
        shared.status.last_health_ms=None;
        if self.sender.try_send(Command::Start(generation)).is_err(){shared.status.state="failed".into();shared.status.error=Some("OWNER_BUSY".into());return Err("OWNER_BUSY".into());}
        Ok(shared.status.clone())
    }
    pub fn stop(&self)->Result<RuntimeStatus,String>{
        let mut shared=self.shared.lock().map_err(|_|"STATE_UNAVAILABLE")?;
        if matches!(shared.status.state.as_str(),"starting"|"ready"){
            shared.generation+=1;shared.status.state="stopping".into();
        }
        Ok(shared.status.clone())
    }
    pub fn shutdown(&self){
        self.quit.store(true,Ordering::Release);
        if let Ok(mut worker)=self.worker.lock(){if let Some(thread)=worker.take(){let _=thread.join();}}
    }
    #[cfg(test)]
    pub fn process_id_for_test(&self)->Option<u32>{self.shared.lock().ok().and_then(|v|v.pid)}
}
impl Drop for DesktopRuntime{fn drop(&mut self){self.shutdown();}}

#[cfg(test)]
mod lifecycle_races {
    use super::*;
    #[test]
    fn stale_stop_completion_cannot_discard_accepted_restart() {
        // The previous stop was generation 1; a new request already accepted generation 2.
        let shared=Arc::new(Mutex::new(Shared{status:RuntimeStatus{state:"starting".into(),execution_enabled:false,error:None,bundle_id:BUNDLE_ID.into(),artifact_version:ARTIFACT_VERSION.into(),last_health_ms:None},generation:2,pid:None}));
        close(&mut None,&shared,1);
        assert_eq!(shared.lock().unwrap().status.state,"starting");
    }
}
