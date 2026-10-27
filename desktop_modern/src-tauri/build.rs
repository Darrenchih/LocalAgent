use std::{collections::BTreeSet,fs,path::Path};
use sha2::{Digest,Sha256};
fn digest(bytes:&[u8])->String{format!("{:x}",Sha256::digest(bytes))}
fn inventory(root:&Path,dir:&Path,out:&mut BTreeSet<String>){
    for item in fs::read_dir(dir).expect("protected resource directory"){
        let path=item.unwrap().path();let meta=fs::symlink_metadata(&path).unwrap();
        #[cfg(windows)] {use std::os::windows::fs::MetadataExt;assert_eq!(meta.file_attributes()&0x400,0,"reparse build input refused");}
        assert!(!meta.file_type().is_symlink(),"symlink build input refused");
        if meta.is_dir(){inventory(root,&path,out);}else{out.insert(path.strip_prefix(root).unwrap().to_str().unwrap().replace('\\',"/"));}
    }
}
fn main(){
    let base=std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let identity_file=base.join("../../packaging/protected-runtime-release.json");
    println!("cargo:rerun-if-changed={}",identity_file.display());
    let identity:serde_json::Value=serde_json::from_slice(&fs::read(&identity_file).expect("release identity missing")).expect("release identity JSON");
    assert_eq!(identity.as_object().expect("release object").len(),5);
    assert_eq!(identity["schema"],1);assert_eq!(identity["contract_version"],1);
    assert_eq!(identity["bundle_id"],"localagent-management-v1");assert_eq!(identity["execution_mode"],"disabled");
    assert_eq!(identity["artifact_version"],env!("CARGO_PKG_VERSION"),"product/Host version mismatch");
    let tauri:serde_json::Value=serde_json::from_slice(&fs::read(base.join("tauri.conf.json")).unwrap()).unwrap();
    let frontend:serde_json::Value=serde_json::from_slice(&fs::read(base.join("../package.json")).unwrap()).unwrap();
    assert_eq!(tauri["version"],identity["artifact_version"]);assert_eq!(frontend["version"],identity["artifact_version"]);
    println!("cargo:rerun-if-changed=tauri.conf.json");println!("cargo:rerun-if-changed=../package.json");
    let root=base.join("binaries/protected-runtime");let pin_file=base.join("binaries/protected-runtime.sha256");
    println!("cargo:rerun-if-changed={}",pin_file.display());println!("cargo:rerun-if-changed={}",root.display());
    let pin=fs::read_to_string(&pin_file).expect("run prepare_protected_desktop.ps1 with an explicitly approved pin first");let pin=pin.trim();
    assert!(pin.len()==64 && pin.bytes().all(|c|c.is_ascii_digit()||(b'a'..=b'f').contains(&c)),"invalid build pin");
    let raw=fs::read(root.join("protected-runtime-manifest.json")).expect("protected manifest missing");
    assert_eq!(digest(&raw),pin,"unapproved protected manifest");
    let value:serde_json::Value=serde_json::from_slice(&raw).expect("manifest JSON");
    assert_eq!(value["bundle_id"],identity["bundle_id"]);assert_eq!(value["artifact_version"],identity["artifact_version"]);
    assert_eq!(value["execution_mode"],"disabled");assert_eq!(value["contract_version"],1);
    assert_eq!(value["roles"]["protected-host-management"],"protected-host-management.exe");
    let mut expected=BTreeSet::from(["protected-runtime-manifest.json".to_owned()]);
    for item in value["artifacts"].as_array().expect("artifact inventory"){
        let relative=item["path"].as_str().unwrap();
        assert!(!relative.is_empty() && relative.split('/').all(|c|!c.is_empty()&&c!="."&&c!=".."&&c.bytes().all(|b|b.is_ascii_alphanumeric()||b"_.-".contains(&b))),"unsafe artifact path");
        assert!(expected.insert(relative.to_owned()),"duplicate artifact");
        let bytes=fs::read(root.join(relative)).expect("missing build artifact");
        assert_eq!(item["size"].as_u64(),Some(bytes.len() as u64));assert_eq!(item["sha256"].as_str(),Some(digest(&bytes).as_str()),"modified build artifact");
    }
    let mut actual=BTreeSet::new();inventory(&root,&root,&mut actual);assert_eq!(actual,expected,"extra or missing build resource");
    println!("cargo:rustc-env=PROTECTED_RUNTIME_MANIFEST_SHA256={pin}");
    println!("cargo:rustc-env=PROTECTED_RUNTIME_BUNDLE_ID={}",identity["bundle_id"].as_str().unwrap());
    println!("cargo:rustc-env=PROTECTED_RUNTIME_ARTIFACT_VERSION={}",identity["artifact_version"].as_str().unwrap());
    let agent_root=base.join("binaries/agent-broker");let agent_pin=base.join("binaries/agent-broker.sha256");
    println!("cargo:rerun-if-changed={}",agent_pin.display());println!("cargo:rerun-if-changed={}",agent_root.display());
    if agent_pin.exists(){
        let pin=fs::read_to_string(&agent_pin).expect("Agent Broker pin");let pin=pin.trim();
        assert!(pin.len()==64&&pin.bytes().all(|c|c.is_ascii_digit()||(b'a'..=b'f').contains(&c)));
        let raw=fs::read(agent_root.join("protected-runtime-manifest.json")).expect("Agent Broker manifest");assert_eq!(digest(&raw),pin);
        let value:serde_json::Value=serde_json::from_slice(&raw).unwrap();
        assert_eq!(value["schema"],1);assert_eq!(value["contract_version"],1);assert_eq!(value["bundle_id"],"localagent-agent-test-v1");
        assert_eq!(value["artifact_version"],identity["artifact_version"]);assert_eq!(value["execution_mode"],"approval_required");
        assert_eq!(value["roles"]["protected-agent-broker"],"agent-broker.exe");
        let mut expected=BTreeSet::from(["protected-runtime-manifest.json".to_owned()]);
        for item in value["artifacts"].as_array().unwrap(){
            let name=item["path"].as_str().unwrap();assert!(name.split('/').all(|s|!s.is_empty()&&s!="."&&s!=".."&&s.bytes().all(|c|c.is_ascii_alphanumeric()||b"_.-".contains(&c))));
            assert!(expected.insert(name.to_owned()));let bytes=fs::read(agent_root.join(name)).unwrap();
            assert_eq!(item["size"].as_u64(),Some(bytes.len() as u64));assert_eq!(item["sha256"].as_str(),Some(digest(&bytes).as_str()));
        }
        let mut actual=BTreeSet::new();inventory(&agent_root,&agent_root,&mut actual);assert_eq!(actual,expected);
        println!("cargo:rustc-env=AGENT_BROKER_MANIFEST_SHA256={pin}");
    }else{println!("cargo:rustc-env=AGENT_BROKER_MANIFEST_SHA256=");}
    tauri_build::build()
}
