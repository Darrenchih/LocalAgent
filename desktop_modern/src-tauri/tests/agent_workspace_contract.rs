//! Native control-plane tests use a scripted model response, never fake tool success.
//! Actual model-driven multi-file acceptance is performed separately through the APP.
use windows_ollama_agent_desktop_lib::{agent_runtime::AgentRuntime,agent_workspace::TEST_ROOT,core_process::{CoreBridge,CoreLaunch},protected_process::{CompatGuard,ObservedProcess}};
use std::{path::{Path,PathBuf},sync::Arc,time::{Duration,Instant}};

fn wait_state(runtime:&AgentRuntime,expected:&str)->serde_json::Value{
    let until=Instant::now()+Duration::from_secs(10);
    loop{let value=runtime.status();if value["task"]["state"]==expected{return value;}
        assert!(value["task"]["state"]!="failed","native task failed: {value}");assert!(Instant::now()<until,"state timeout: {value}");std::thread::sleep(Duration::from_millis(30));}
}

#[test]
#[ignore="explicit REVIEW043 Frozen Broker native scope/approval/cleanup acceptance"]
fn switching_workspace_invalidates_pending_approval_and_reaps_owned_broker(){
    let executable=PathBuf::from(std::env::var_os("REVIEW043_APP_EXE").expect("staged REVIEW043 APP path required"));
    let project=Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap();
    assert!(executable.is_absolute()&&executable.starts_with(project.join("work/review-043")));
    let id=uuid::Uuid::new_v4().to_string();let root=Path::new(TEST_ROOT).join("work/review043/native-actor").join(&id);let a=root.join("a");let b=root.join("b");
    std::fs::create_dir_all(&a).unwrap();std::fs::create_dir_all(&b).unwrap();std::fs::write(a.join("probe.txt"),"A unchanged").unwrap();std::fs::write(b.join("probe.txt"),"B unchanged").unwrap();
    let data=project.join("work/review-043/native-actor-data").join(&id);std::fs::create_dir_all(&data).unwrap();
    let fixture=data.join("scripted_model_core.py");std::fs::write(&fixture,r#"import json,sys,uuid
for line in sys.stdin:
    r=json.loads(line)
    result={'stored':True}
    if r['action']=='agent_start':
        result={'kind':'proposal','proposal_id':str(uuid.uuid4()),'tool':'read_file','args':{'path':'probe.txt'},'model_turns':1,'tool_calls':1}
    elif r['action']=='agent_cancel': result={'cancelled':True}
    print(json.dumps({'version':'1.0','id':r['id'],'ok':True,'result':result}),flush=True)
"#).unwrap();
    let bridge=CoreBridge::spawn(CoreLaunch{program:std::env::var("LOCALAGENT_TEST_PYTHON").expect("set LOCALAGENT_TEST_PYTHON to an absolute Python 3.12 path"),args:vec!["-I".into(),"-u".into(),fixture.to_string_lossy().to_string()],working_dir:data.to_string_lossy().to_string()}).unwrap();
    let guard=Arc::new(CompatGuard::acquire().unwrap());let runtime=AgentRuntime::new(&executable,bridge.clone(),guard,&data);let conversation=uuid::Uuid::new_v4().to_string();
    assert!(runtime.workspace_select(&conversation,a.to_str().unwrap()).unwrap().enabled);
    runtime.start(conversation.clone(),"scripted read proposal for cancellation test".into(),Some("scripted-unit-model".into())).unwrap();
    let pending=wait_state(&runtime,"awaiting_approval");let pid=pending["broker_pid"].as_u64().unwrap() as u32;let observed=ObservedProcess::open(pid).unwrap();
    let old_task=pending["task"]["task_id"].as_str().unwrap();let old_proposal=pending["task"]["steps"][0]["proposal_id"].as_str().unwrap();
    let changed=runtime.workspace_select(&conversation,b.to_str().unwrap()).unwrap();assert!(changed.enabled);
    assert!(runtime.decide(old_task,old_proposal,true).is_err());assert!(observed.wait(Duration::from_secs(3)));runtime.join();
    let cancelled=runtime.status();assert_eq!(cancelled["task"]["state"],"cancelled");assert!(cancelled["broker_pid"].is_null());assert!(cancelled["task"]["steps"][0].get("output").is_none());
    runtime.start(conversation.clone(),"scripted reject proposal".into(),Some("scripted-unit-model".into())).unwrap();let second=wait_state(&runtime,"awaiting_approval");
    assert_ne!(second["task"]["workspace_revision"],pending["task"]["workspace_revision"]);assert_eq!(second["task"]["workspace"],b.to_string_lossy().replace('/',"\\"));
    runtime.decide(second["task"]["task_id"].as_str().unwrap(),second["task"]["steps"][0]["proposal_id"].as_str().unwrap(),false).unwrap();runtime.join();assert_eq!(runtime.status()["task"]["state"],"rejected");
    let outside=runtime.workspace_select(&conversation,"C:\\Windows").unwrap();assert!(!outside.enabled);assert!(runtime.start(conversation,"must not start outside scope".into(),None).is_err());
    assert_eq!(std::fs::read_to_string(a.join("probe.txt")).unwrap(),"A unchanged");assert_eq!(std::fs::read_to_string(b.join("probe.txt")).unwrap(),"B unchanged");
    runtime.shutdown();bridge.shutdown();runtime.join();
}
