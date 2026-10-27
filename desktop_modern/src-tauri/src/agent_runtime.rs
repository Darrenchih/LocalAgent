//! Approval owner for the explicitly enabled, isolated Agent test workspace.
//! The model only proposes. This owner signs a single immutable UI-approved call.
use crate::{agent_workspace::{WorkspaceLease,WorkspaceStatus,WorkspaceStore},core_process::{CoreBridge,CoreRequestInput},protected_process::{CompatGuard,JobCancel,NativeProcess,PinnedBundle}};
use serde_json::{json,Value};
use sha2::{Digest,Sha256};
use std::{path::{Path,PathBuf},sync::{Arc,Mutex,atomic::{AtomicBool,Ordering}},thread::{self,JoinHandle},time::{Duration,Instant,SystemTime,UNIX_EPOCH}};

pub const TEST_ROOT:&str="D:\\Agent\\desktop-agent\\work\\agent-integration\\workspace";
fn now()->u64{SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64}
fn hash(bytes:&[u8])->String{format!("{:x}",Sha256::digest(bytes))}
pub fn canonical(value:&Value)->Vec<u8>{serde_json::to_vec(value).expect("JSON value")}
// Type-tag every node for an injective digest format independent of JSON float
// spelling (e.g. Rust 1e-7 vs Python 1e-07). This is not the wire serializer.
pub fn grant_canonical(value:&Value)->Vec<u8>{
    fn encode(value:&Value,out:&mut Vec<u8>){match value{
        Value::Null=>out.push(b'n'),Value::Bool(v)=>out.push(if *v{b't'}else{b'f'}),
        Value::String(v)=>{out.extend_from_slice(format!("s{}:",v.len()).as_bytes());out.extend_from_slice(v.as_bytes());},
        Value::Number(v)=>{if let Some(n)=v.as_i64(){out.extend_from_slice(format!("i{n};").as_bytes());}else if let Some(n)=v.as_u64(){out.extend_from_slice(format!("i{n};").as_bytes());}else{let n=v.as_f64().expect("finite JSON number");out.extend_from_slice(format!("d{:016x};",n.to_bits()).as_bytes());}},
        Value::Array(v)=>{out.extend_from_slice(format!("a{}[",v.len()).as_bytes());for item in v{encode(item,out);}out.push(b']');},
        Value::Object(v)=>{out.extend_from_slice(format!("o{}{{",v.len()).as_bytes());let mut keys:Vec<_>=v.keys().collect();keys.sort_unstable_by(|a,b|a.as_bytes().cmp(b.as_bytes()));for key in keys{encode(&Value::String(key.clone()),out);encode(&v[key],out);}out.push(b'}');}
    }}let mut bytes=Vec::new();encode(value,&mut bytes);bytes
}
fn mac(key:&[u8],data:&[u8])->String{
    let mut inner=[0x36u8;64];let mut outer=[0x5cu8;64];
    for (i,b) in key.iter().enumerate(){inner[i]^=b;outer[i]^=b;}
    let mut a=Sha256::new();a.update(inner);a.update(data);let mut b=Sha256::new();b.update(outer);b.update(a.finalize());format!("{:x}",b.finalize())
}
fn core(bridge:&CoreBridge,action:&str,params:Value)->Result<Value,String>{bridge.call_cancellable(CoreRequestInput{version:"1.0".into(),request_id:None,action:action.into(),params},Duration::from_secs(2),&AtomicBool::new(false))}
fn audit(shared:&Arc<Mutex<Shared>>,bridge:&CoreBridge,task_id:&str,conversation_id:&str)->Result<(),String>{
    let event=shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?.snapshot["task"].clone();
    core(bridge,"agent_audit",json!({"task_id":task_id,"conversation_id":conversation_id,"event":event})).map(|_|())
}
fn broker(process:&mut NativeProcess,method:&str,params:Value)->Result<Value,String>{
    let id=uuid::Uuid::new_v4().to_string();let mut raw=canonical(&json!({"v":1,"id":id,"method":method,"params":params}));raw.push(b'\n');
    if raw.len()>65536{return Err("BROKER_FRAME_LIMIT".into());}
    let until=Instant::now()+Duration::from_secs(10);
    process.write_all(&raw,until).map_err(|e|e.0.to_owned())?;
    let response=process.read_frame(until).map_err(|e|e.0.to_owned())?;
    let value=crate::protected_runtime::strict_json(&response,65536,&crate::protected_runtime::Limits::default()).map_err(|_|"BROKER_PROTOCOL_INVALID")?;
    if value["id"]!=id || value["v"]!=1{return Err("BROKER_PROTOCOL_INVALID".into());}
    if value["ok"]!=true{return Err(value["error"]["code"].as_str().unwrap_or("BROKER_ERROR").into());}
    Ok(value["result"].clone())
}
pub const MAX_TOOLS:usize=24;
const ACTIVE_SECONDS:u64=180;
const WALL_SECONDS:u64=900;
const APPROVAL_SECONDS:u64=300;
struct Shared{snapshot:Value,decision:Option<bool>,cancel:Arc<AtomicBool>,job:Option<Arc<JobCancel>>,generation:u64}
pub struct AgentRuntime{shared:Arc<Mutex<Shared>>,worker:Mutex<Option<JoinHandle<()>>>,control:Mutex<()>,workspaces:Mutex<WorkspaceStore>,bridge:Arc<CoreBridge>,bundle:PathBuf,_guard:Arc<CompatGuard>}
fn active(task:&Value)->bool{matches!(task["state"].as_str(),Some("planning"|"awaiting_approval"|"running"))}
fn valid_tool(tool:&str)->bool{matches!(tool,"read_file"|"write_file"|"powershell"|"list_directory"|"create_directory"|"search_text")}
fn risk(tool:&str)->&'static str{match tool{"read_file"=>"read scoped file","write_file"=>"modify scoped file","list_directory"=>"list scoped directory","create_directory"=>"create scoped directory","search_text"=>"search scoped text",_=>"run fixed PowerShell JSON test"}}
// Descriptive Core metadata never changes native state, authorization or args.
fn copy_progress(task:&mut Value,turn:&Value)->Result<(),String>{
    for key in ["plan","files"]{if let Some(value)=turn.get(key){if value.is_null(){continue;}let list=value.as_array().ok_or("MODEL_PROGRESS_INVALID")?;if list.len()>128||canonical(value).len()>196608{return Err("MODEL_PROGRESS_LIMIT".into());}
        for item in list{if !item.is_object(){return Err("MODEL_PROGRESS_INVALID".into());}if key=="plan"{
            if item["id"].as_str().is_none_or(|s|s.is_empty()||s.len()>512)||item["title"].as_str().is_none_or(|s|s.len()>512)||!matches!(item["state"].as_str(),Some("pending"|"ready"|"running"|"completed"|"failed"|"blocked"|"skipped"))||item["dependencies"].as_array().is_none_or(|v|v.len()>128||v.iter().any(|x|x.as_str().is_none())){return Err("MODEL_PROGRESS_INVALID".into());}
        }else if item["path"].as_str().is_none_or(|s|s.len()>512)||!item["valid"].is_boolean(){return Err("MODEL_PROGRESS_INVALID".into());}}
        task[key]=value.clone();}}
    if let Some(value)=turn.get("metrics"){if !value.is_null(){let values=value.as_object().ok_or("MODEL_PROGRESS_INVALID")?;if values.len()>16||values.values().any(|v|v.as_u64().is_none()){return Err("MODEL_PROGRESS_INVALID".into());}task["metrics"]=value.clone();}}
    Ok(())
}
fn proposal_view(turn:&Value,args:&Value)->Result<Value,String>{
    let mut view=json!({});
    if let Some(id)=turn.get("plan_step_id"){if !id.is_null(){if id.as_str().is_none_or(|v|v.is_empty()||v.len()>128){return Err("MODEL_PROGRESS_INVALID".into());}view["plan_step_id"]=id.clone();}}
    if let Some(preview)=turn.get("preview"){if !preview.is_null(){
        let object=preview.as_object().ok_or("MODEL_PREVIEW_INVALID")?;
        if object.len()>8||preview["path"]!=args["path"]||preview["after"]!=args["content"]||preview["source_sha256"]!=args["expected_sha256"]{return Err("MODEL_PREVIEW_INVALID".into());}
        for key in ["before","after"]{if key=="before"&&preview[key].is_null()&&args["expected_sha256"]=="absent"{continue;}if preview[key].as_str().is_none_or(|v|v.len()>32768){return Err("MODEL_PREVIEW_LIMIT".into());}}
        if preview["content_type"].as_str().is_none_or(|v|v.len()>64)||preview["warnings"].as_array().is_none_or(|v|v.len()>16||v.iter().any(|x|x.as_str().is_none_or(|s|s.len()>512))){return Err("MODEL_PREVIEW_INVALID".into());}
        if args["expected_sha256"]!="absent"&&preview["before"].as_str().map(|v|hash(v.as_bytes()))!=args["expected_sha256"].as_str().map(str::to_owned){return Err("MODEL_PREVIEW_VERSION_MISMATCH".into());}
        view["preview"]=preview.clone();
    }}Ok(view)
}
fn mark_plan_step(task:&mut Value,state:&str){
    let id=task["steps"].as_array().and_then(|s|s.last()).and_then(|s|s["plan_step_id"].as_str()).map(str::to_owned);
    if let Some(id)=id{if let Some(plan)=task["plan"].as_array_mut(){for step in plan{if step["id"]==id{step["state"]=json!(state);}}}}
}
fn retain_result(task:&mut Value,result:&Value,count:usize){
    if let Some(step)=task["steps"].as_array_mut().and_then(|steps|steps.last_mut()){step["state"]=json!(if result["ok"]==false{"failed"}else{"completed"});step["output"]=result.clone();}
    mark_plan_step(task,if result["ok"]==false{"failed"}else{"completed"});
    if active(task){task["state"]=json!("planning");}task["tool_calls"]=json!(count);
}
fn unfinished_plan(task:&Value)->bool{task["plan"].as_array().is_some_and(|steps|steps.iter().any(|step|step["state"]!="completed"))}
fn failure_state(error:&str)->&'static str{
    if error.starts_with("planning_failed:"){"planning_failed"}
    else if error.starts_with("model_timeout:")||error.contains("_TIMEOUT")||matches!(error,"core_response_timeout"|"core_write_timeout"|"agent active time limit reached"|"agent wall time limit reached"){"timeout"}
    else if error.starts_with("provider_error:"){"provider_error"}
    else{"failed"}
}
fn task_failure_state(task:&Value,error:&str)->&'static str{
    let state=failure_state(error);
    if state=="failed"&&task["steps"].as_array().is_some_and(|steps|steps.iter().any(|v|v["state"]=="failed"||v["output"]["ok"]==false)){"execution_failed"}else{state}
}
fn needs_core_cleanup(task:&Value)->bool{task["core_finished"]!=true}
fn apply_model_finish(task:&mut Value,turn:&Value)->Result<(),String>{
    if unfinished_plan(task){return Err("PLAN_NOT_COMPLETED".into());}
    let steps=task["steps"].as_array().ok_or("MODEL_PROTOCOL_INVALID")?;
    let (state,outcome)=if steps.is_empty(){("no_operation","no_operation")}
        else if steps.iter().any(|v|v["state"]=="failed"||v["output"]["ok"]==false){("execution_failed","tool_error")}
        else{("unverified","unverified")};
    task["state"]=json!(state);task["outcome"]=json!(outcome);
    // A model finish (including a forged validation flag) is not independent verification.
    task["goal_validated"]=json!(false);task["final_response"]=turn["content"].clone();task["core_finished"]=json!(true);Ok(())
}
fn settle_plan(task:&mut Value){
    let state=task["state"].as_str().unwrap_or("").to_owned();if matches!(state.as_str(),"cancelled"|"rejected"|"failed"|"planning_failed"|"timeout"|"provider_error"|"execution_failed"|"no_operation"){
        if let Some(plan)=task["plan"].as_array_mut(){for step in plan{if matches!(step["state"].as_str(),Some("pending"|"ready"|"running")){step["state"]=json!("blocked");step["error"]=json!(format!("task {state}"));}}}
        if let Some(files)=task["files"].as_array_mut(){for file in files{file["valid"]=json!(false);}}
    }
}
pub fn validate_ui_core_action(action:&str)->Result<(),String>{if matches!(action,"agent_start"|"agent_continue"|"agent_cancel"|"agent_audit"|"agent_record_result"){Err("AGENT_NATIVE_OWNER_REQUIRED".into())}else{Ok(())}}
impl AgentRuntime{
    pub fn new(executable:&Path,bridge:Arc<CoreBridge>,guard:Arc<CompatGuard>,data_dir:&Path)->Self{
        let default_enabled=std::env::var_os("LOCALAGENT_AGENT_TEST_ROOT").is_some_and(|v|PathBuf::from(v)==Path::new(TEST_ROOT)&&WorkspaceLease::open(TEST_ROOT).is_ok());
        Self{shared:Arc::new(Mutex::new(Shared{snapshot:json!({"task":null,"broker_pid":null}),decision:None,cancel:Arc::new(AtomicBool::new(false)),job:None,generation:0})),worker:Mutex::new(None),control:Mutex::new(()),workspaces:Mutex::new(WorkspaceStore::new(data_dir,default_enabled)),bridge,bundle:executable.parent().unwrap_or(Path::new(".")).join("runtime/agent/localagent-agent-test-v1"),_guard:guard}
    }
    pub fn workspace_get(&self,id:Option<&str>)->WorkspaceStatus{
        let mut status=self.workspaces.lock().unwrap().get(id);
        if env!("AGENT_BROKER_MANIFEST_SHA256").is_empty(){status.enabled=false;status.reason=Some("AGENT_BROKER_UNAVAILABLE".into());status.permissions.clear();for tool in &mut status.tools{tool["enabled"]=json!(false);}for template in &mut status.templates{template["enabled"]=json!(false);}}status
    }
    pub fn status(&self)->Value{self.status_for(None)}
    pub fn status_for(&self,id:Option<&str>)->Value{
        let mut snapshot=self.shared.lock().unwrap().snapshot.clone();let id=id.map(str::to_owned).or_else(||snapshot["task"]["conversation_id"].as_str().map(str::to_owned));let scope=self.workspace_get(id.as_deref());
        snapshot["workspace"]=json!(scope.path);snapshot["execution_mode"]=json!(if scope.enabled{"approval_required"}else{"disabled"});snapshot["workspace_status"]=serde_json::to_value(scope).unwrap();snapshot
    }
    pub fn workspace_select(&self,id:&str,path:&str)->Result<WorkspaceStatus,String>{
        let _control=self.control.lock().map_err(|_|"AGENT_LOCK_FAILED")?;
        let task=self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?.snapshot["task"].clone();if active(&task){let _=self.cancel(task["task_id"].as_str().unwrap_or(""));}
        self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?.generation+=1;
        self.workspaces.lock().map_err(|_|"AGENT_LOCK_FAILED")?.select(id,path)
    }
    pub fn start(&self,conversation_id:String,goal:String,model:Option<String>)->Result<Value,String>{
        let _control=self.control.lock().map_err(|_|"AGENT_LOCK_FAILED")?;
        if goal.trim().is_empty()||goal.len()>16000{return Err("INVALID_GOAL".into());}if env!("AGENT_BROKER_MANIFEST_SHA256").is_empty(){return Err("AGENT_BROKER_UNAVAILABLE".into());}
        let mut worker=self.worker.lock().map_err(|_|"AGENT_LOCK_FAILED")?;
        if active(&self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?.snapshot["task"]){return Err("AGENT_TASK_ALREADY_ACTIVE".into());}
        if worker.as_ref().is_some_and(|w|!w.is_finished()){return Err("AGENT_CLEANUP_PENDING".into());}if let Some(old)=worker.take(){let _=old.join();}
        let (lease,scope)=self.workspaces.lock().map_err(|_|"AGENT_LOCK_FAILED")?.lease(&conversation_id)?;let task_id=uuid::Uuid::new_v4().to_string();let generation;
        {let mut s=self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?;s.cancel=Arc::new(AtomicBool::new(false));s.decision=None;s.job=None;s.generation+=1;generation=s.generation;
            s.snapshot["task"]=json!({"task_id":task_id,"conversation_id":conversation_id,"goal":goal,"model":model.clone(),"state":"planning","steps":[],"workspace":scope.path,"workspace_revision":scope.revision,"model_turns":0,"tool_calls":0,"goal_validated":false,"limits":{"tool_calls":MAX_TOOLS,"model_turns":20,"active_seconds":ACTIVE_SECONDS,"wall_seconds":WALL_SECONDS,"approval_seconds":APPROVAL_SECONDS}});}
        let shared=self.shared.clone();let bridge=self.bridge.clone();let bundle=self.bundle.clone();let guard=self._guard.clone();let response_id=conversation_id.clone();
        *worker=Some(thread::spawn(move||{
            let _guard=guard;let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||run(&shared,&bridge,&bundle,lease,&scope.revision,generation,&task_id,&conversation_id,&goal,model)));
            let error=match result{Ok(Ok(()))=>None,Ok(Err(e))=>Some(e),Err(_)=>Some("AGENT_WORKER_FAILED".into())};
            {let mut s=shared.lock().unwrap();s.job=None;s.snapshot["broker_pid"]=Value::Null;if let Some(e)=error{if s.snapshot["task"]["state"]!="cancelled"{s.snapshot["task"]["state"]=json!(task_failure_state(&s.snapshot["task"],&e));s.snapshot["task"]["outcome"]=s.snapshot["task"]["state"].clone();s.snapshot["task"]["error"]=json!(e);}}
                let state=s.snapshot["task"]["state"].clone();for step in s.snapshot["task"]["steps"].as_array_mut().unwrap(){if matches!(step["state"].as_str(),Some("running"|"awaiting_approval")){step["state"]=state.clone();}}}
            if needs_core_cleanup(&shared.lock().unwrap().snapshot["task"]){
                let last=shared.lock().unwrap().snapshot["task"]["steps"].as_array().and_then(|s|s.last()).cloned();
                if let Some(step)=last{if let Some(result)=step.get("output"){let _=core(&bridge,"agent_record_result",json!({"task_id":task_id,"proposal_id":step["proposal_id"],"result":result}));}}
                let _=core(&bridge,"agent_cancel",json!({"task_id":task_id}));
            }
            if let Ok(progress)=core(&bridge,"agent_progress",json!({"task_id":task_id})){let mut s=shared.lock().unwrap();let task=&mut s.snapshot["task"];if copy_progress(task,&progress).is_err(){task["progress_error"]=json!("MODEL_PROGRESS_INVALID");}if let Some(turns)=progress["model_turns"].as_u64(){task["model_turns"]=json!(turns);}}
            let event={let mut s=shared.lock().unwrap();settle_plan(&mut s.snapshot["task"]);s.snapshot["task"].clone()};
            let _=core(&bridge,"agent_audit",json!({"task_id":task_id,"conversation_id":conversation_id,"event":event}));
        }));Ok(self.status_for(Some(&response_id)))
    }
    pub fn decide(&self,task_id:&str,proposal_id:&str,approved:bool)->Result<Value,String>{
        let _control=self.control.lock().map_err(|_|"AGENT_LOCK_FAILED")?;let task=self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?.snapshot["task"].clone();let scope=self.workspace_get(task["conversation_id"].as_str());
        if !scope.enabled||task["workspace_revision"]!=scope.revision{let _=self.cancel(task_id);return Err("WORKSPACE_PERMISSION_INVALIDATED".into());}
        {let mut s=self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?;let task=&s.snapshot["task"];
            if task["task_id"]!=task_id||task["state"]!="awaiting_approval"||task["steps"].as_array().and_then(|x|x.last()).map(|v|v["proposal_id"].as_str())!=Some(Some(proposal_id))||s.decision.is_some(){return Err("STALE_APPROVAL_DENIED".into());}s.decision=Some(approved);}
        Ok(self.status())
    }
    pub fn cancel(&self,task_id:&str)->Result<Value,String>{
        {let mut s=self.shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?;if s.snapshot["task"]["task_id"]!=task_id{return Err("STALE_TASK_DENIED".into());}
            if active(&s.snapshot["task"]){s.cancel.store(true,Ordering::Release);if let Some(job)=&s.job{job.cancel();}s.snapshot["task"]["state"]=json!("cancelled");let bridge=self.bridge.clone();let id=task_id.to_owned();thread::spawn(move||{let _=core(&bridge,"agent_cancel",json!({"task_id":id}));});}}
        Ok(self.status())
    }
    pub fn shutdown(&self){let status=self.status();let id=status["task"]["task_id"].as_str().unwrap_or("");let conversation=status["task"]["conversation_id"].as_str().unwrap_or("");let _=self.cancel(id);if !id.is_empty(){let _=audit(&self.shared,&self.bridge,id,conversation);}}
    pub fn join(&self){if let Some(worker)=self.worker.lock().unwrap().take(){let _=worker.join();}}
}
struct Clock{started:Instant,paused:Duration}
impl Clock{
    fn active(&self)->Duration{self.started.elapsed().saturating_sub(self.paused)}
    fn check(&self,shared:&Arc<Mutex<Shared>>,cancel:&AtomicBool,generation:u64,human_wait:bool)->Result<(),String>{
        if cancel.load(Ordering::Acquire){return Err("CANCELLED".into());}if shared.lock().map_err(|_|"AGENT_LOCK_FAILED")?.generation!=generation{return Err("WORKSPACE_PERMISSION_INVALIDATED".into());}
        if self.started.elapsed()>=Duration::from_secs(WALL_SECONDS){return Err("TASK_WALL_TIMEOUT".into());}if !human_wait&&self.active()>=Duration::from_secs(ACTIVE_SECONDS){return Err("TASK_ACTIVE_TIMEOUT".into());}Ok(())
    }
    fn model(&self,bridge:&CoreBridge,action:&str,params:Value,cancel:&AtomicBool)->Result<Value,String>{bridge.call_cancellable(CoreRequestInput{version:"1.0".into(),request_id:None,action:action.into(),params},Duration::from_secs(ACTIVE_SECONDS).saturating_sub(self.active()),cancel)}
}
fn run(shared:&Arc<Mutex<Shared>>,bridge:&CoreBridge,bundle:&Path,lease:WorkspaceLease,revision:&str,generation:u64,task_id:&str,conversation_id:&str,goal:&str,model:Option<String>)->Result<(),String>{
    let cancel=shared.lock().unwrap().cancel.clone();let mut clock=Clock{started:Instant::now(),paused:Duration::ZERO};let root=lease.path.to_string_lossy().to_string();
    let binding=PinnedBundle::open_agent(bundle,env!("AGENT_BROKER_MANIFEST_SHA256"),Instant::now()+Duration::from_secs(10)).map_err(|e|e.0.to_owned())?;clock.check(shared,&cancel,generation,false)?;lease.verify()?;
    let mut process=NativeProcess::launch_agent(binding,Instant::now()).map_err(|e|e.0.to_owned())?;{let mut s=shared.lock().unwrap();s.snapshot["broker_pid"]=json!(process.pid());s.job=Some(Arc::new(process.cancellation().map_err(|e|e.0.to_owned())?));if cancel.load(Ordering::Acquire){s.job.as_ref().unwrap().cancel();}}
    let mut key=Vec::from(*uuid::Uuid::new_v4().as_bytes());key.extend_from_slice(uuid::Uuid::new_v4().as_bytes());let hex=key.iter().map(|b|format!("{b:02x}")).collect::<String>();broker(&mut process,"init",json!({"session_key":hex,"root":root,"task_id":task_id}))?;process.verify_loaded_modules().map_err(|e|e.0.to_owned())?;
    let catalog=broker(&mut process,"catalog",json!({}))?;let names:std::collections::BTreeSet<_>=catalog["tools"].as_array().ok_or("BROKER_CAPABILITIES_INVALID")?.iter().filter_map(|v|v["function"]["name"].as_str()).collect();if names!=["read_file","write_file","powershell","list_directory","create_directory","search_text"].into_iter().collect(){return Err("BROKER_CAPABILITIES_INVALID".into());}
    clock.check(shared,&cancel,generation,false)?;let mut turn=clock.model(bridge,"agent_start",json!({"task_id":task_id,"conversation_id":conversation_id,"goal":goal,"model":model,"workspace":root,"workspace_revision":revision}),&cancel)?;
    for index in 0..=MAX_TOOLS{
        clock.check(shared,&cancel,generation,false)?;lease.verify()?;{let mut s=shared.lock().unwrap();s.snapshot["task"]["model_turns"]=turn["model_turns"].clone();s.snapshot["task"]["tool_calls"]=json!(index);copy_progress(&mut s.snapshot["task"],&turn)?;}
        if matches!(turn["kind"].as_str(),Some("finished"|"completed")){let mut s=shared.lock().unwrap();if cancel.load(Ordering::Acquire){return Err("CANCELLED".into());}return apply_model_finish(&mut s.snapshot["task"],&turn);}
        if index==MAX_TOOLS{return Err("TOOL_CALL_LIMIT".into());}if turn["kind"]!="proposal"{return Err("MODEL_PROTOCOL_INVALID".into());}
        let proposal=turn["proposal_id"].as_str().ok_or("MODEL_PROTOCOL_INVALID")?.to_owned();if uuid::Uuid::parse_str(&proposal).is_err(){return Err("MODEL_PROTOCOL_INVALID".into());}let tool=turn["tool"].as_str().ok_or("MODEL_PROTOCOL_INVALID")?.to_owned();if !valid_tool(&tool){return Err("TOOL_DENIED".into());}let args=turn["args"].clone();if canonical(&args).len()>40000{return Err("TOOL_ARGUMENT_LIMIT".into());}let view=proposal_view(&turn,&args)?;
        {let mut s=shared.lock().unwrap();if cancel.load(Ordering::Acquire){return Err("CANCELLED".into());}s.decision=None;let mut step=json!({"proposal_id":proposal,"tool":tool,"args":args,"target":args["path"],"risk":risk(&tool),"state":"awaiting_approval"});for (key,value) in view.as_object().unwrap(){step[key]=value.clone();}s.snapshot["task"]["steps"].as_array_mut().unwrap().push(step);mark_plan_step(&mut s.snapshot["task"],"ready");s.snapshot["task"]["state"]=json!("awaiting_approval");s.snapshot["task"]["approval_deadline_ms"]=json!(now()+APPROVAL_SECONDS*1000);}
        audit(shared,bridge,task_id,conversation_id)?;let wait=Instant::now();let approved=loop{clock.check(shared,&cancel,generation,true)?;if wait.elapsed()>=Duration::from_secs(APPROVAL_SECONDS){return Err("APPROVAL_TIMEOUT".into());}if let Some(value)=shared.lock().unwrap().decision.take(){break value;}thread::sleep(Duration::from_millis(50));};clock.paused+=wait.elapsed();clock.check(shared,&cancel,generation,false)?;lease.verify()?;
        if !approved{let mut s=shared.lock().unwrap();s.snapshot["task"]["state"]=json!("rejected");s.snapshot["task"]["steps"].as_array_mut().unwrap().last_mut().unwrap()["state"]=json!("rejected");return Ok(());}
        {let mut s=shared.lock().unwrap();s.snapshot["task"]["state"]=json!("running");s.snapshot["task"]["steps"].as_array_mut().unwrap().last_mut().unwrap()["state"]=json!("running");mark_plan_step(&mut s.snapshot["task"],"running");}audit(shared,bridge,task_id,conversation_id)?;clock.check(shared,&cancel,generation,false)?;
        let grant=json!({"task_id":task_id,"proposal_id":proposal,"root":root,"tool":tool,"args":args,"expires_ms":now()+15000});let grant_sha=hash(&grant_canonical(&grant));let nonce=uuid::Uuid::new_v4().to_string();let signature=mac(&key,format!("{nonce}:{grant_sha}").as_bytes());let answer=broker(&mut process,"execute",json!({"grant":grant,"permit":{"nonce":nonce,"grant_sha256":grant_sha,"mac":signature}}));
        let result=match answer{Ok(mut value)=>{value.as_object_mut().ok_or("BROKER_PROTOCOL_INVALID")?.insert("ok".into(),json!(true));value},Err(e)=>json!({"ok":false,"error":e})};{let mut s=shared.lock().unwrap();retain_result(&mut s.snapshot["task"],&result,index+1);}clock.check(shared,&cancel,generation,false)?;lease.verify()?;audit(shared,bridge,task_id,conversation_id)?;if process.exited(){return Err("BROKER_EXITED".into());}
        turn=clock.model(bridge,"agent_continue",json!({"task_id":task_id,"proposal_id":proposal,"result":result}),&cancel)?;
    }Err("TOOL_CALL_LIMIT".into())
}

#[cfg(test)]mod tests{
    use super::*;
    #[test]fn model_finish_cannot_claim_validation_or_hide_no_operation(){
        let mut empty=json!({"steps":[],"plan":[]});
        apply_model_finish(&mut empty,&json!({"outcome":"model_finished","goal_validated":true,"content":"Done"})).unwrap();
        assert_eq!(empty["state"],"no_operation");assert_eq!(empty["goal_validated"],false);
        let mut read=json!({"steps":[{"state":"completed","output":{"ok":true}}],"plan":[{"state":"completed"}]});
        apply_model_finish(&mut read,&json!({"outcome":"unverified","goal_validated":true,"content":"Done"})).unwrap();
        assert_eq!(read["state"],"unverified");assert_eq!(read["goal_validated"],false);
        let mut failure=json!({"steps":[{"state":"failed","output":{"ok":false}}],"plan":[]});
        apply_model_finish(&mut failure,&json!({"content":"Done"})).unwrap();assert_eq!(failure["state"],"execution_failed");
        assert!(apply_model_finish(&mut json!({"steps":[],"plan":[{"state":"ready"}]}),&json!({"content":"Done"})).is_err());
    }
    #[test]fn model_failures_have_distinct_terminal_states(){
        assert_eq!(failure_state("planning_failed: expected plan"),"planning_failed");
        assert_eq!(failure_state("model_timeout: Ollama request timed out"),"timeout");
        assert_eq!(failure_state("provider_error: invalid JSON"),"provider_error");
        assert_eq!(failure_state("TASK_ACTIVE_TIMEOUT"),"timeout");
        for error in ["core_response_timeout","core_write_timeout","agent active time limit reached","agent wall time limit reached"]{assert_eq!(failure_state(error),"timeout");}
        assert_eq!(failure_state("Access denied"),"failed");
        assert_eq!(task_failure_state(&json!({"steps":[{"state":"failed","output":{"ok":false}}]}),"incomplete_plan: failed step"),"execution_failed");
        assert_eq!(task_failure_state(&json!({"steps":[]}),"planning_failed: invalid plan"),"planning_failed");
    }
    #[test]fn failed_broker_before_core_continue_still_requires_result_retention(){
        let task=json!({"state":"execution_failed","steps":[{"state":"failed","output":{"ok":false,"error":"BROKER_EXITED"}}]});
        assert!(needs_core_cleanup(&task));
        let mut finished=json!({"steps":[{"state":"completed","output":{"ok":true}}],"plan":[]});
        apply_model_finish(&mut finished,&json!({"content":"read summary"})).unwrap();
        assert!(!needs_core_cleanup(&finished));
    }
    #[test]fn progress_is_advisory_and_bounded(){
        let mut task=json!({"state":"awaiting_approval","goal_validated":false});
        copy_progress(&mut task,&json!({"plan":[{"id":"s1","title":"read","dependencies":[],"state":"ready"}],"files":[],"metrics":{"schema_errors":1},"state":"completed","goal_validated":true,"permit":"fake"})).unwrap();
        assert_eq!(task["state"],"awaiting_approval");assert_eq!(task["goal_validated"],false);assert!(task.get("permit").is_none());assert_eq!(task["metrics"]["schema_errors"],1);
        assert!(copy_progress(&mut task,&json!({"plan":vec![json!({});129]})).is_err());assert!(copy_progress(&mut task,&json!({"plan":["invalid"]})).is_err());
    }
    #[test]fn model_finish_cannot_complete_unfinished_plan(){
        assert!(unfinished_plan(&json!({"plan":[{"state":"completed"},{"state":"ready"}]})));
        assert!(unfinished_plan(&json!({"plan":[{"state":"skipped"}]})));
        assert!(!unfinished_plan(&json!({"plan":[{"state":"completed"}]})));
        assert!(!unfinished_plan(&json!({"plan":[]})));
    }
    #[test]fn completed_broker_result_is_retained_after_cancel_without_resuming(){
        let mut task=json!({"state":"cancelled","steps":[{"proposal_id":"p","plan_step_id":"s1","state":"running"}],"plan":[{"id":"s1","state":"running"}],"tool_calls":0});
        retain_result(&mut task,&json!({"ok":true,"bytes_written":3,"sha256":hash(b"new")}),1);
        assert_eq!(task["state"],"cancelled");assert_eq!(task["steps"][0]["state"],"completed");assert_eq!(task["steps"][0]["output"]["bytes_written"],3);assert_eq!(task["tool_calls"],1);assert_eq!(task["plan"][0]["state"],"completed");
    }
    #[test]fn cancelled_plan_preserves_completed_work_and_invalidates_versions(){
        let mut task=json!({"state":"cancelled","plan":[{"id":"a","state":"completed"},{"id":"b","state":"ready"}],"files":[{"path":"a.txt","valid":true}]});settle_plan(&mut task);
        assert_eq!(task["plan"][0]["state"],"completed");assert_eq!(task["plan"][1]["state"],"blocked");assert_eq!(task["files"][0]["valid"],false);assert_eq!(task["state"],"cancelled");
    }
    #[test]fn preview_cannot_misrepresent_immutable_proposal(){
        let args=json!({"path":"docs/status.md","content":"new text","expected_sha256":hash(b"old text")});
        let good=json!({"preview":{"path":"docs/status.md","before":"old text","after":"new text","source_sha256":hash(b"old text"),"content_type":"text","warnings":[]},"plan_step_id":"s1"});
        assert_eq!(proposal_view(&good,&args).unwrap()["preview"]["before"],"old text");
        for (key,value) in [("before",json!("misleading old text")),("path",json!("other.txt")),("after",json!("different text")),("source_sha256",json!("b".repeat(64)))]{let mut bad=good.clone();bad["preview"][key]=value;assert!(proposal_view(&bad,&args).is_err());}
        assert!(proposal_view(&json!({"preview":{"path":"x","before":"x".repeat(32769)}}),&args).is_err());
    }

    #[test]fn cross_language_typed_grant_digest_has_no_float_or_type_alias(){
        let value=json!({"n":1e-7,"z":1.0,"negative":-0.0,"unicode":"測試","obj":{"n":1},"list":[true,null,1]});
        assert_eq!(hash(&grant_canonical(&value)),"c749474e61cef5be421839248143d2ab2855d1deda6d8b14d8bf50cbe6f879ec");
        assert_ne!(grant_canonical(&json!(1)),grant_canonical(&json!(1.0)));assert_ne!(grant_canonical(&json!(true)),grant_canonical(&json!(1)));
        assert_ne!(grant_canonical(&json!({"number":"3e7ad7f29abcaf48"})),grant_canonical(&json!(1e-7)));
    }
    #[test]fn webview_cannot_submit_internal_authority_actions(){
        for action in ["agent_start","agent_continue","agent_cancel","agent_audit","agent_record_result"]{assert!(validate_ui_core_action(action).is_err());}
        for action in ["read_history","agent_history","agent_capabilities","stream_message"]{assert!(validate_ui_core_action(action).is_ok());}
    }
    #[test]fn approval_wait_is_excluded_but_wall_time_and_cancellation_remain_bounded(){
        let shared=Arc::new(Mutex::new(Shared{snapshot:json!({"task":null}),decision:None,cancel:Arc::new(AtomicBool::new(false)),job:None,generation:5}));let cancel=AtomicBool::new(false);
        let clock=Clock{started:Instant::now()-Duration::from_secs(400),paused:Duration::from_secs(390)};assert!(clock.check(&shared,&cancel,5,false).is_ok());
        assert!(clock.check(&shared,&cancel,4,false).is_err());cancel.store(true,Ordering::Release);assert!(clock.check(&shared,&cancel,5,true).is_err());cancel.store(false,Ordering::Release);
        let wall=Clock{started:Instant::now()-Duration::from_secs(WALL_SECONDS+1),paused:Duration::from_secs(WALL_SECONDS)};assert!(wall.check(&shared,&cancel,5,true).is_err());
    }
    #[test]fn grant_canonical_and_hmac(){let v=json!({"z":1,"a":{"b":2,"a":"x"}});assert_eq!(String::from_utf8(canonical(&v)).unwrap(),"{\"a\":{\"a\":\"x\",\"b\":2},\"z\":1}");assert_eq!(mac(b"key",b"The quick brown fox jumps over the lazy dog"),"f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8");}
    #[test]
    #[ignore="explicit real Frozen Broker / file / PowerShell acceptance"]
    fn frozen_broker_real_tools_and_recovery(){
        let base=Path::new(env!("CARGO_MANIFEST_DIR"));let bundle=base.join("binaries/agent-broker");
        let root=Path::new(TEST_ROOT).join("native-acceptance");std::fs::create_dir_all(&root).unwrap();
        let root=root.to_str().unwrap();let file=format!("native-{}.json",uuid::Uuid::new_v4());
        let start=||NativeProcess::launch_agent(PinnedBundle::open_agent(&bundle,env!("AGENT_BROKER_MANIFEST_SHA256"),Instant::now()+Duration::from_secs(10)).unwrap(),Instant::now()).unwrap();
        let mut p=start();let cancel=p.cancellation().unwrap();
        assert_eq!(broker(&mut p,"execute",json!({})).unwrap_err(),"NOT_INITIALIZED");
        let key=[42u8;32];let task=uuid::Uuid::new_v4().to_string();
        broker(&mut p,"init",json!({"session_key":"2a".repeat(32),"root":root,"task_id":task})).unwrap();
        p.verify_loaded_modules().unwrap();
        let permit=|tool:&str,args:Value|{let grant=json!({"task_id":task,"proposal_id":uuid::Uuid::new_v4().to_string(),"root":root,"tool":tool,"args":args,"expires_ms":now()+15000});let sha=hash(&grant_canonical(&grant));let nonce=uuid::Uuid::new_v4().to_string();let sig=mac(&key,format!("{nonce}:{sha}").as_bytes());json!({"grant":grant,"permit":{"nonce":nonce,"grant_sha256":sha,"mac":sig}})};
        let request=permit("write_file",json!({"path":file,"content":"{\"enabled\":true}","expected_sha256":"absent"}));
        assert_eq!(broker(&mut p,"execute",request.clone()).unwrap()["bytes_written"],16);
        assert_eq!(broker(&mut p,"execute",request).unwrap_err(),"AUTHORIZATION_DENIED");
        let read=broker(&mut p,"execute",permit("read_file",json!({"path":file}))).unwrap();assert_eq!(read["content"],"{\"enabled\":true}");
        let result=broker(&mut p,"execute",permit("powershell",json!({"template":"test_json","path":file,"expected":{"enabled":true}}))).unwrap();assert_eq!(result["passed"],true);
        assert_eq!(broker(&mut p,"execute",permit("read_file",json!({"path":"../outside.txt"}))).unwrap_err(),"PATH_DENIED");
        let mut mutation=permit("read_file",json!({"path":file}));mutation["grant"]["args"]["path"]=json!("outside.txt");assert_eq!(broker(&mut p,"execute",mutation).unwrap_err(),"AUTHORIZATION_DENIED");
        let dir=format!("dir-{}",uuid::Uuid::new_v4());
        assert_eq!(broker(&mut p,"execute",permit("create_directory",json!({"path":dir}))).unwrap()["created"],true);
        let listing=broker(&mut p,"execute",permit("list_directory",json!({"path":"."}))).unwrap();assert!(listing["entries"].as_array().unwrap().iter().any(|e|e["name"]==file));
        let found=broker(&mut p,"execute",permit("search_text",json!({"path":".","query":"enabled"}))).unwrap();assert!(found["matches"].as_array().unwrap().iter().any(|e|e["path"]==file));
        let floatfile=format!("float-{}.json",uuid::Uuid::new_v4());
        broker(&mut p,"execute",permit("write_file",json!({"path":floatfile,"content":"{\"n\":1e-7}","expected_sha256":"absent"}))).unwrap();
        assert_eq!(broker(&mut p,"execute",permit("powershell",json!({"template":"test_json","path":floatfile,"expected":{"n":1e-7}}))).unwrap()["passed"],true);
        cancel.cancel();p.terminate(Duration::from_secs(2)).unwrap();assert!(p.exited());assert_eq!(p.active_count().unwrap(),0);
        let mut fault=start();let until=Instant::now()+Duration::from_millis(100);assert!(fault.read_frame(until).is_err());assert!(fault.exited());assert_eq!(fault.active_count().unwrap(),0);
    }
}
