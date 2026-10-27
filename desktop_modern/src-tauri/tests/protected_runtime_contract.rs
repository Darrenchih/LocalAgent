#[path = "../src/protected_runtime.rs"]
mod protected_runtime;

use protected_runtime::*;
use serde_json::{json, Value};
use std::collections::BTreeMap;

const SESSION: &str = "888cb0db-8dab-4e83-9265-dc0bd2b5b7b1";
const ID1: &str = "13572080-4e2e-48e4-91d7-c60dd0465809";
const ID2: &str = "f5f7708c-a58c-4910-b701-1507fb02a921";
const ID3: &str = "8dc86a21-a2fe-48cb-aa72-8abe97141143";
fn binding() -> Binding { Binding::new("review039-source-contract", "0.36.0").unwrap() }
fn frame(v: Value) -> Vec<u8> { format!("{v}\n").into_bytes() }
fn reply(id: &str, state: &str) -> Vec<u8> {
    frame(json!({"v":1,"id":id,"session":SESSION,"result":{
        "role":"protected-host-management","contract_version":1,
        "bundle_id":"review039-source-contract","artifact_version":"0.36.0",
        "state":state,"execution_enabled":false,
        "child_roles":{"oi":"not_started","broker":"not_started"}}}))
}
struct MemoryTransport { completed_at: u64, writes: Vec<Vec<u8>>, error: bool }
impl Transport for MemoryTransport {
    fn write(&mut self, bytes: &[u8], _deadline: u64) -> Result<u64, Fault> {
        if self.error { return Err(Fault("PEER_EXITED")); }
        self.writes.push(bytes.to_vec()); Ok(self.completed_at)
    }
}
fn transport(at: u64) -> MemoryTransport { MemoryTransport{completed_at:at,writes:vec![],error:false} }
fn starting(limits: Limits) -> Supervisor {
    let mut s = Supervisor::new(binding(), SESSION, limits).unwrap();
    s.begin(0).unwrap(); s.verified(1).unwrap(); s
}
fn ready() -> Supervisor {
    let mut s = starting(Limits::default());
    s.enqueue("health", ID1, 2).unwrap(); s.send_next(&mut transport(3), 2).unwrap();
    s.receive(&reply(ID1,"disabled_ready"),4).unwrap(); s
}

#[test]
fn duplicate_keys_nonfinite_and_excess_depth_are_rejected() {
    for raw in [b"{\"a\":1,\"a\":2}\n".as_slice(), b"{\"x\":{\"a\":0,\"a\":1}}\n",
                b"{\"x\":1e999}\n", b"{\"x\":NaN}\n", b"{}\r\n", b"{}\n{}\n", b"\xef\xbb\xbf{}\n"] {
        assert!(parse_frame(raw, &Limits::default()).is_err(), "{raw:?}");
    }
    let limits=Limits{depth:2,nodes:4,frame_bytes:128,..Limits::default()};
    assert!(parse_frame(b"[0,0,0]\n",&limits).is_ok());
    for raw in [b"[[[]]]\n".as_slice(), b"[0,0,0,0]\n"] {assert!(parse_frame(raw,&limits).is_err());}
    let mut exact=b"{}".to_vec();exact.extend(vec![b' ';125]);exact.push(b'\n');
    assert!(parse_frame(&exact,&limits).is_ok()); exact.insert(2,b' ');
    assert!(parse_frame(&exact,&limits).is_err());
}

#[test]
fn invalid_profile_cannot_disable_limits() {
    for limits in [Limits{pending:0,..Limits::default()},Limits{depth:100,..Limits::default()},
                   Limits{request_ms:0,..Limits::default()}] {
        assert!(Supervisor::new(binding(),SESSION,limits).is_err());
    }
}

#[test]
fn supervisor_requires_verified_identity_before_ready() {
    let mut s=starting(Limits::default());
    assert_eq!(s.state(), State::Starting);
    assert!(s.enqueue("status",ID1,2).is_err());
    let mut s=starting(Limits::default());
    s.enqueue("health",ID1,2).unwrap();s.send_next(&mut transport(3),2).unwrap();
    s.receive(&reply(ID1,"disabled_ready"),4).unwrap();
    assert_eq!(s.state(),State::Ready);
    assert!(!s.health_due(5003)); assert!(s.health_due(5004));
}

#[test]
fn execution_aliases_are_rejected_before_writing() {
    for method in ["tools/call","grant","permit","exec","approve","enable","dispatch"] {
        let mut s=ready(); assert_eq!(s.enqueue(method,ID2,5),Err(Fault("EXECUTION_DISABLED")));
        assert_eq!(s.state(),State::Failed);assert_eq!(s.outstanding(),0);
        assert_eq!(s.last_error(),Some(Fault("EXECUTION_DISABLED")));
    }
}

#[test]
fn forgotten_health_schedule_cannot_leave_ready_forever() {
    let mut s=ready();assert_eq!(s.tick(8004),Err(Fault("HEALTH_TIMEOUT")));
    assert_eq!(s.state(),State::Failed);
}

#[test]
fn extra_executable_cannot_be_disguised_as_data() {
    let (raw,mut inv)=manifest("host.exe");
    let mut v:Value=serde_json::from_slice(&raw).unwrap();
    v["artifacts"].as_array_mut().unwrap().push(json!({"path":"extra.exe","kind":"data","size":0,"sha256":digest(b"")}));
    inv.insert("extra.exe".into(),vec![]);let raw=v.to_string().into_bytes();
    assert!(validate_manifest(&raw,&digest(&raw),&binding(),&inv).is_err());
}

#[test]
fn duplicate_completed_ids_and_invalid_uuid_fail_closed() {
    let mut s=ready(); assert!(s.enqueue("status",ID1,5).is_err());
    assert_eq!(s.state(),State::Failed);
    let mut s=ready();assert!(s.enqueue("status",&ID2.to_uppercase(),5).is_err());
}

#[test]
fn response_schema_role_version_and_disabled_invariant_are_strict() {
    for (key,bad) in [("role",json!("broker")),("artifact_version",json!("other")),
        ("bundle_id",json!("other")),("execution_enabled",json!(true)),
        ("execution_enabled",json!(0)),("contract_version",json!(1.0)),
        ("state",json!("running")),("extra",json!(0))] {
        let mut s=starting(Limits::default());s.enqueue("health",ID1,2).unwrap();
        s.send_next(&mut transport(3),2).unwrap();
        let mut bad_frame:Value=serde_json::from_slice(&reply(ID1,"disabled_ready")).unwrap();
        bad_frame["result"][key]=bad;
        assert!(s.receive(&frame(bad_frame),4).is_err(),"{key}");
        assert_eq!(s.state(),State::Failed);assert_eq!(s.outstanding(),0);
    }
}

#[test]
fn unexpected_duplicate_cross_session_and_late_replies_cancel_pending() {
    for mode in ["unknown","session","both","missing","late"] {
        let mut s=ready();s.enqueue("status",ID2,10).unwrap();
        s.send_next(&mut transport(11),10).unwrap();
        let mut v:Value=serde_json::from_slice(&reply(ID2,"disabled_ready")).unwrap();
        match mode {"unknown"=>v["id"]=json!(ID3),"session"=>v["session"]=json!(ID3),
          "both"=>v["error"]=json!({"code":"PROTOCOL_INVALID","message":"PROTOCOL_INVALID"}),
          "missing"=>{v.as_object_mut().unwrap().remove("result");},_=>{}}
        assert!(s.receive(&frame(v),if mode=="late" {3010} else {12}).is_err());
        assert_eq!(s.state(),State::Failed);assert_eq!(s.outstanding(),0);
    }
    let mut s=ready();assert!(s.receive(&reply(ID1,"disabled_ready"),5).is_err());
}

#[test]
fn queue_write_and_response_share_an_absolute_deadline() {
    let mut s=ready();s.enqueue("status",ID2,10).unwrap();
    assert_eq!(s.next_deadline(),Some(3010));
    assert_eq!(s.send_next(&mut transport(3010),20),Err(Fault("REQUEST_TIMEOUT")));
    assert_eq!(s.state(),State::Failed);
    let mut s=ready();s.enqueue("status",ID2,10).unwrap();
    assert_eq!(s.tick(3010),Err(Fault("REQUEST_TIMEOUT")));
    let mut s=starting(Limits::default());
    assert_eq!(s.tick(10000),Err(Fault("HEALTH_TIMEOUT")));
}

#[test]
fn pending_and_queue_and_seen_are_bounded() {
    let limits=Limits{pending:1,queue:1,seen_ids:2,..Limits::default()};
    let mut s=starting(limits);s.enqueue("health",ID1,2).unwrap();
    s.send_next(&mut transport(3),2).unwrap();s.receive(&reply(ID1,"disabled_ready"),4).unwrap();
    s.enqueue("status",ID2,5).unwrap();assert_eq!(s.enqueue("status",ID3,5),Err(Fault("RESOURCE_LIMIT")));
    let mut s=starting(Limits{queue:1,..Limits::default()});
    s.enqueue("health",ID1,2).unwrap();assert!(s.enqueue("health",ID2,3).is_err());
    let mut s=ready();s.enqueue("status",ID2,5).unwrap();
    let mut t=transport(6);t.error=true;
    assert_eq!(s.send_next(&mut t,5),Err(Fault("PEER_EXITED")));assert_eq!(s.outstanding(),0);
}

#[test]
fn shutdown_cancels_old_requests_and_ack_is_not_exit() {
    let mut s=ready();s.enqueue("status",ID2,5).unwrap();
    s.request_shutdown(ID3,6).unwrap();assert_eq!(s.outstanding(),1);
    s.send_next(&mut transport(7),6).unwrap();s.receive(&reply(ID3,"stopping"),8).unwrap();
    assert_eq!(s.state(),State::Stopping);
    s.confirm_exit(9).unwrap();assert_eq!(s.state(),State::Stopped);
    assert!(s.enqueue("health",ID2,10).is_err());
    let mut s=ready();s.request_shutdown(ID2,6).unwrap();
    assert_eq!(s.tick(5006),Err(Fault("SHUTDOWN_TIMEOUT")));
    assert_eq!(s.state(),State::Failed);
}

#[test]
fn normal_shutdown_drains_valid_already_inflight_response_without_routing_it() {
    let mut s=ready();s.enqueue("status",ID2,5).unwrap();
    s.send_next(&mut transport(6),5).unwrap();
    s.request_shutdown(ID3,7).unwrap();
    s.send_next(&mut transport(8),7).unwrap();
    s.receive(&reply(ID2,"disabled_ready"),9).unwrap();
    assert_eq!(s.state(),State::Stopping);assert_eq!(s.outstanding(),1);
    s.receive(&reply(ID3,"stopping"),10).unwrap();
    s.confirm_exit(11).unwrap();assert_eq!(s.state(),State::Stopped);
}

#[test]
fn cancelled_response_drain_does_not_accept_wrong_identity_or_replay() {
    for mode in ["session","replay","expired"] {
        let mut s=ready();s.enqueue("status",ID2,5).unwrap();s.send_next(&mut transport(6),5).unwrap();
        s.request_shutdown(ID3,7).unwrap();s.send_next(&mut transport(8),7).unwrap();
        let mut v:Value=serde_json::from_slice(&reply(ID2,"disabled_ready")).unwrap();
        if mode=="session"{v["session"]=json!(ID1);}
        if mode=="replay"{s.receive(&frame(v.clone()),9).unwrap();}
        assert!(s.receive(&frame(v),if mode=="expired"{2007}else{10}).is_err());
        assert_eq!(s.state(),State::Failed);assert_eq!(s.outstanding(),0);
    }
}

#[test]
fn full_pending_window_keeps_queue_bounded_without_resetting_deadline() {
    let mut s=starting(Limits{pending:1,..Limits::default()});
    s.enqueue("health",ID1,2).unwrap();s.send_next(&mut transport(3),2).unwrap();
    s.receive(&reply(ID1,"disabled_ready"),4).unwrap();
    s.enqueue("status",ID2,10).unwrap();s.send_next(&mut transport(11),10).unwrap();
    s.enqueue("status",ID3,12).unwrap();let mut t=transport(13);
    assert!(!s.send_next(&mut t,13).unwrap());assert!(t.writes.is_empty());
    assert_eq!(s.tick(3010),Err(Fault("REQUEST_TIMEOUT")));
    assert_eq!(s.outstanding(),0);
}

#[test]
fn first_health_budget_is_anchored_to_starting_even_without_scheduling() {
    let mut s=starting(Limits::default());
    assert_eq!(s.tick(5001),Err(Fault("HEALTH_TIMEOUT")));
    let mut s=starting(Limits::default());
    assert_eq!(s.enqueue("health",ID1,9000),Err(Fault("HEALTH_TIMEOUT")));
}

fn manifest(path:&str)->(Vec<u8>,BTreeMap<String,Vec<u8>>) {
    let data=b"inert artifact bytes, not executable".to_vec();
    let value=json!({"schema":1,"contract_version":1,"bundle_id":"review039-source-contract",
      "artifact_version":"0.36.0","target":"x86_64-pc-windows-msvc","execution_mode":"disabled",
      "roles":{"protected-host-management":path},"artifacts":[{"path":path,"kind":"entrypoint",
      "size":data.len(),"sha256":digest(&data)}]});
    (value.to_string().into_bytes(),BTreeMap::from([(path.to_owned(),data)]))
}

#[test]
fn manifest_verifies_digest_identity_bytes_and_complete_inventory() {
    let (raw,inventory)=manifest("host.exe");
    assert!(validate_manifest(&raw,&digest(&raw),&binding(),&inventory).is_ok());
    assert!(validate_manifest(&raw,"00",&binding(),&inventory).is_err());
    let mut extra=inventory.clone();extra.insert("extra.dll".into(),vec![]);
    assert!(validate_manifest(&raw,&digest(&raw),&binding(),&extra).is_err());
    let mut changed=inventory;changed.insert("host.exe".into(),b"changed".to_vec());
    assert!(validate_manifest(&raw,&digest(&raw),&binding(),&changed).is_err());
}

#[test]
fn manifest_refuses_windows_aliases_extra_roles_and_version_combinations() {
    for path in ["../host.exe","C:/host.exe","//server/share","a\\host.exe","a:stream",
        "a/./host.exe","a/../host.exe","CON.exe","dir/file. ","a//host.exe","a/COM1.txt"] {
        let (raw,inv)=manifest(path);assert!(validate_manifest(&raw,&digest(&raw),&binding(),&inv).is_err(),"{path}");
    }
    let (raw,inv)=manifest("host.exe");
    for mode in ["role","version","schema","extra","case"] {
        let mut value:Value=serde_json::from_slice(&raw).unwrap();
        match mode {"role"=>value["roles"]["broker"]=json!("host.exe"),
          "version"=>value["artifact_version"]=json!("other"),"schema"=>value["schema"]=json!(true),
          "extra"=>value["extra"]=json!(0),"case"=>{let mut artifact=value["artifacts"][0].clone();artifact["path"]=json!("HOST.exe");value["artifacts"].as_array_mut().unwrap().push(artifact);},_=>{}}
        let bad=value.to_string().into_bytes();assert!(validate_manifest(&bad,&digest(&bad),&binding(),&inv).is_err(),"{mode}");
    }
}

#[test]
fn windows_short_names_and_spaced_device_aliases_are_refused() {
    for path in ["HOST~1.exe","CON .exe","dir/NUL .txt"] {
        let (raw,inv)=manifest(path);
        assert!(validate_manifest(&raw,&digest(&raw),&binding(),&inv).is_err(),"{path}");
    }
}
