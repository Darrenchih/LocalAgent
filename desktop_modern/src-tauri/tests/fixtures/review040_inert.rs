//! Independent, visibly inert native failure fixture. Never included in the bundle.
use std::{io::{self, BufRead, Write}, time::Duration};
fn main() {
    let mode=std::env::args().nth(1).unwrap_or_else(||"sleep".into());
    match mode.as_str() {
        "sleep"|"no-read"=>std::thread::sleep(Duration::from_secs(60)),
        "malformed"=>{println!("not protocol JSON");std::thread::sleep(Duration::from_secs(60));},
        "oversize"=>{let _=io::stdout().write_all(&vec![b'x';65537]);std::thread::sleep(Duration::from_secs(60));},
        "stderr-flood"=>{for _ in 0..512{let _=io::stderr().write_all(&[b'x';4096]);} println!("done");},
        "descendant"=>{
            let child=std::process::Command::new(std::env::current_exe().unwrap()).arg("sleep")
                .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().unwrap();
            println!("{}",child.id());let _=io::stdout().flush();std::thread::sleep(Duration::from_secs(60));
        },
        "wrong-identity"=>{
            let mut line=String::new();io::stdin().lock().read_line(&mut line).unwrap();
            let extract=|key:&str|line.split(&format!("\"{key}\":\"")).nth(1).unwrap_or("").split('"').next().unwrap_or("").to_owned();
            println!("{{\"v\":1,\"id\":\"{}\",\"session\":\"{}\",\"result\":{{\"role\":\"broker\",\"contract_version\":1,\"bundle_id\":\"review040-management-v1\",\"artifact_version\":\"0.36.0\",\"state\":\"disabled_ready\",\"execution_enabled\":false,\"child_roles\":{{\"oi\":\"not_started\",\"broker\":\"not_started\"}}}}}}",extract("id"),extract("session"));
            std::thread::sleep(Duration::from_secs(60));
        },
        "shutdown-bad"|"shutdown-hang"|"shutdown-ack-hang"=>{
            for line in io::stdin().lock().lines(){
                let line=line.unwrap();
                let shutdown=line.contains("\"method\":\"shutdown\"");
                if shutdown && mode!="shutdown-ack-hang"{
                    if mode=="shutdown-hang"{std::thread::sleep(Duration::from_secs(60));}
                    else{println!("bad shutdown reply");let _=io::stdout().flush();continue;}
                }
                let extract=|key:&str|line.split(&format!("\"{key}\":\"")).nth(1).unwrap_or("").split('"').next().unwrap_or("").to_owned();
                println!("{{\"v\":1,\"id\":\"{}\",\"session\":\"{}\",\"result\":{{\"role\":\"protected-host-management\",\"contract_version\":1,\"bundle_id\":\"review040-management-v1\",\"artifact_version\":\"0.36.0\",\"state\":\"{}\",\"execution_enabled\":false,\"child_roles\":{{\"oi\":\"not_started\",\"broker\":\"not_started\"}}}}}}",extract("id"),extract("session"),if shutdown{"stopping"}else{"disabled_ready"});
                let _=io::stdout().flush();
                if shutdown && mode=="shutdown-ack-hang"{std::thread::sleep(Duration::from_secs(60));}
            }
        },
        _=>std::process::exit(2),
    }
}
