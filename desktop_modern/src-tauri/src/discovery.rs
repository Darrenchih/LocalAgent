use crate::core_process::{CoreBridge,CoreLaunch,CoreRequestInput};
use serde_json::{json,Value};
use std::{path::Path,sync::Arc,io::Read,time::Duration};
use sha2::{Digest,Sha256};

pub fn validate_action(action:&str)->Result<(),String>{
    match action {"health"|"list_servers"|"save_server"|"connect"|"disconnect"|"status"=>Ok(()),_=>Err("formal_execution_disabled_review036".into())}
}
pub fn validate_health(value:&Value)->Result<(),String>{
    if value["component"]!="agent-discovery" || value["version"]!=env!("CARGO_PKG_VERSION") || value["protocol"]!="1.0" || value["status"]!="ok" || value["execution_enabled"]!=false {return Err("runtime_identity_invalid".into());}
    Ok(())
}
pub struct DiscoveryRuntime(pub Result<DiscoveryBridge,String>);
pub struct DiscoveryBridge {pub bridge:Arc<CoreBridge>}
impl DiscoveryBridge {
    pub fn start(executable:&Path,project:&Path,data:&Path,packaged:bool)->Result<Self,String>{
        std::fs::create_dir_all(data).map_err(|_|"discovery_data_unavailable")?;
        let program=executable.parent().ok_or("runtime_path_invalid")?.join("agent-discovery.exe");
        let mut _pinned_file:Option<std::fs::File>=None;
        let launch=if packaged {
            let mut options=std::fs::OpenOptions::new();options.read(true);
            #[cfg(windows)] {use std::os::windows::fs::OpenOptionsExt; options.share_mode(1);}
            let mut pinned=options.open(&program).map_err(|_|"runtime_missing")?;
            let mut bytes=Vec::new();pinned.read_to_end(&mut bytes).map_err(|_|"runtime_missing")?;
            let actual=format!("{:x}",Sha256::digest(bytes));
            if actual!=include_str!("../binaries/discovery.sha256").trim(){return Err("runtime_hash_invalid".into());}
            _pinned_file=Some(pinned);
            CoreLaunch{program:program.to_string_lossy().into_owned(),args:vec!["--stdio".into(),"--db".into(),data.join("servers.db").to_string_lossy().into_owned()],working_dir:data.to_string_lossy().into_owned()}
        }else{
            CoreLaunch{program:std::env::var("AGENT_PYTHON").unwrap_or_else(|_|"python".into()),args:vec!["-u".into(),"-m".into(),"agent_discovery.server".into(),"--stdio".into(),"--db".into(),data.join("servers.db").to_string_lossy().into_owned()],working_dir:project.to_string_lossy().into_owned()}
        };
        let bridge=CoreBridge::spawn(launch)?;
        let wrapper=Self{bridge};
        let health=wrapper.request("health",json!({}));
        if health.as_ref().map_err(Clone::clone).and_then(validate_health).is_err(){wrapper.bridge.shutdown();return Err("runtime_identity_invalid".into());}
        Ok(wrapper)
    }
    pub fn request(&self,action:&str,params:Value)->Result<Value,String>{
        validate_action(action)?;
        self.bridge.call_with_timeout(CoreRequestInput{version:"1.0".into(),request_id:None,action:action.into(),params}, |_|{},Duration::from_secs(if action=="connect" {35} else {5}))
    }
}
