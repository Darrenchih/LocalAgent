//! REVIEW-039 pure contracts. No filesystem, native process, Job, or tool transport.
use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Fault(pub &'static str);
type Result<T> = std::result::Result<T, Fault>;
const INVALID: Fault = Fault("PROTOCOL_INVALID");

#[derive(Debug, Clone)]
pub struct Binding { bundle_id: String, artifact_version: String }
impl Binding {
    pub fn new(bundle: &str, version: &str) -> Result<Self> {
        for s in [bundle, version] {
            if s.is_empty() || s.len()>128 || !s.bytes().all(|c|c.is_ascii_alphanumeric() || b"_.-".contains(&c)) {
                return Err(Fault("VERSION_MISMATCH"));
            }
        }
        Ok(Self{bundle_id:bundle.into(),artifact_version:version.into()})
    }
}

#[derive(Debug, Clone)]
pub struct Limits {
    pub frame_bytes: usize, pub depth: usize, pub nodes: usize, pub seen_ids: usize,
    pub pending: usize, pub queue: usize, pub startup_ms: u64, pub first_health_ms: u64,
    pub request_ms: u64, pub shutdown_ms: u64, pub shutdown_grace_ms: u64, pub heartbeat_ms: u64,
}
impl Default for Limits {
    fn default() -> Self {Self{frame_bytes:65536,depth:16,nodes:8192,seen_ids:65536,
        pending:8,queue:32,startup_ms:10000,first_health_ms:5000,request_ms:3000,
        shutdown_ms:5000,shutdown_grace_ms:2000,heartbeat_ms:5000}}
}
impl Limits {
    fn validate(&self) -> Result<()> {
        if self.frame_bytes==0 || self.frame_bytes>65536 || self.depth==0 || self.depth>16
            || self.nodes==0 || self.nodes>8192 || self.seen_ids==0 || self.seen_ids>65536
            || self.pending==0 || self.pending>8 || self.queue==0 || self.queue>32
            || self.startup_ms==0 || self.first_health_ms==0 || self.request_ms==0
            || self.shutdown_ms==0 || self.shutdown_grace_ms==0 || self.heartbeat_ms==0
            || self.startup_ms>10000 || self.first_health_ms>5000 || self.request_ms>3000
            || self.shutdown_ms>5000 || self.shutdown_grace_ms>self.shutdown_ms
            || self.heartbeat_ms>5000 {return Err(Fault("RESOURCE_LIMIT"));}
        Ok(())
    }
}

// The seed checks budget before materializing each value; duplicate map keys are
// rejected before their values are inserted. Container depth excludes scalar leaves.
struct Seed<'a> { depth:usize, nodes:&'a mut usize, limits:&'a Limits }
impl<'de> DeserializeSeed<'de> for Seed<'_> {
    type Value=Value;
    fn deserialize<D:de::Deserializer<'de>>(self, d:D)->std::result::Result<Value,D::Error>{
        *self.nodes+=1;
        if *self.nodes>self.limits.nodes {return Err(de::Error::custom("node limit"));}
        d.deserialize_any(self)
    }
}
impl<'de> Visitor<'de> for Seed<'_> {
    type Value=Value;
    fn expecting(&self,f:&mut fmt::Formatter)->fmt::Result{f.write_str("bounded strict JSON")}
    fn visit_bool<E:de::Error>(self,v:bool)->std::result::Result<Value,E>{Ok(Value::Bool(v))}
    fn visit_i64<E:de::Error>(self,v:i64)->std::result::Result<Value,E>{Ok(v.into())}
    fn visit_u64<E:de::Error>(self,v:u64)->std::result::Result<Value,E>{Ok(v.into())}
    fn visit_f64<E:de::Error>(self,v:f64)->std::result::Result<Value,E>{
        Number::from_f64(v).map(Value::Number).ok_or_else(||E::custom("nonfinite"))
    }
    fn visit_str<E:de::Error>(self,v:&str)->std::result::Result<Value,E>{Ok(v.into())}
    fn visit_string<E:de::Error>(self,v:String)->std::result::Result<Value,E>{Ok(Value::String(v))}
    fn visit_unit<E:de::Error>(self)->std::result::Result<Value,E>{Ok(Value::Null)}
    fn visit_seq<A:SeqAccess<'de>>(self,mut a:A)->std::result::Result<Value,A::Error>{
        if self.depth>=self.limits.depth{return Err(de::Error::custom("depth limit"));}
        let mut values=Vec::new();
        while let Some(v)=a.next_element_seed(Seed{depth:self.depth+1,nodes:&mut *self.nodes,limits:self.limits})?{values.push(v);}
        Ok(Value::Array(values))
    }
    fn visit_map<A:MapAccess<'de>>(self,mut a:A)->std::result::Result<Value,A::Error>{
        if self.depth>=self.limits.depth{return Err(de::Error::custom("depth limit"));}
        let mut values=Map::new();
        while let Some(key)=a.next_key::<String>()?{
            if values.contains_key(&key){return Err(de::Error::custom("duplicate key"));}
            let v=a.next_value_seed(Seed{depth:self.depth+1,nodes:&mut *self.nodes,limits:self.limits})?;
            values.insert(key,v);
        }
        Ok(Value::Object(values))
    }
}

pub(crate) fn strict_json(raw:&[u8], byte_limit:usize, limits:&Limits)->Result<Value>{
    if raw.is_empty() || raw.len()>byte_limit{return Err(INVALID);}
    let mut d=serde_json::Deserializer::from_slice(raw);
    let mut nodes=0;
    let v=Seed{depth:0,nodes:&mut nodes,limits}.deserialize(&mut d).map_err(|_|INVALID)?;
    d.end().map_err(|_|INVALID)?;
    Ok(v)
}
pub fn parse_frame(raw:&[u8], limits:&Limits)->Result<Value>{
    limits.validate()?;
    if raw.is_empty() || raw.len()>limits.frame_bytes || raw.last()!=Some(&b'\n')
        || raw[..raw.len()-1].contains(&b'\n') || raw.contains(&b'\r') || raw.starts_with(&[239,187,191]){
        return Err(INVALID);
    }
    strict_json(&raw[..raw.len()-1],limits.frame_bytes-1,limits)
}
fn exact(value:&Value, keys:&[&str])->Result<()>{
    let obj=value.as_object().ok_or(INVALID)?;
    if obj.len()!=keys.len() || !keys.iter().all(|k|obj.contains_key(*k)){return Err(INVALID);}
    Ok(())
}
fn uuid4(s:&str)->Result<()>{
    if s.len()!=36{return Err(INVALID);}
    let u=uuid::Uuid::parse_str(s).map_err(|_|INVALID)?;
    if u.get_version_num()!=4 || u.to_string()!=s || u.get_variant()!=uuid::Variant::RFC4122{return Err(INVALID);}
    Ok(())
}
fn text<'a>(v:&'a Value,k:&str)->Result<&'a str>{v.get(k).and_then(Value::as_str).ok_or(INVALID)}
fn integer(v:&Value,k:&str,n:u64)->bool{v.get(k).and_then(Value::as_u64)==Some(n)}
fn deadline(now:u64,span:u64)->Result<u64>{now.checked_add(span).ok_or(Fault("RESOURCE_LIMIT"))}
pub fn digest(raw:&[u8])->String{format!("{:x}",Sha256::digest(raw))}

fn safe_path(s:&str)->Result<String>{
    if s.is_empty() || s.len()>240 || !s.is_ascii(){return Err(INVALID);}
    for part in s.split('/'){
        if part.is_empty() || part=="." || part==".." || part.ends_with([' ','.'])
            || !part.bytes().all(|c|c.is_ascii_alphanumeric() || b"_.-".contains(&c))
            || part.bytes().any(|c|c<32 || b"\\:*?\"<>|".contains(&c)){return Err(INVALID);}
        let stem=part.split('.').next().unwrap_or("").to_ascii_uppercase();
        if ["CON","PRN","AUX","NUL"].contains(&stem.as_str())
            || (stem.len()==4 && (stem.starts_with("COM") || stem.starts_with("LPT"))
                && matches!(stem.as_bytes()[3],b'1'..=b'9')){return Err(INVALID);}
    }
    Ok(s.to_ascii_lowercase())
}

pub fn validate_manifest(raw:&[u8], expected_digest:&str, binding:&Binding,
    inventory:&BTreeMap<String,Vec<u8>>)->Result<()>{
    validate_manifest_profile(raw,expected_digest,binding,inventory,"protected-host-management","disabled")
}
pub fn validate_manifest_profile(raw:&[u8], expected_digest:&str, binding:&Binding,
    inventory:&BTreeMap<String,Vec<u8>>,role_name:&str,mode:&str)->Result<()>{
    if !matches!((role_name,mode),("protected-host-management","disabled")|("protected-agent-broker","approval_required")){return Err(INVALID);}
    if raw.len()>1048576 || expected_digest.len()!=64 || digest(raw)!=expected_digest{
        return Err(Fault("INTEGRITY_MISMATCH"));
    }
    let v=strict_json(raw,1048576,&Limits::default())?;
    exact(&v,&["schema","contract_version","bundle_id","artifact_version","target","execution_mode","roles","artifacts"])?;
    if !integer(&v,"schema",1) || !integer(&v,"contract_version",1)
        || text(&v,"target")? != "x86_64-pc-windows-msvc" || text(&v,"execution_mode")? != mode {return Err(INVALID);}
    if text(&v,"bundle_id")?!=binding.bundle_id || text(&v,"artifact_version")?!=binding.artifact_version{
        return Err(Fault("VERSION_MISMATCH"));
    }
    exact(&v["roles"],&[role_name]).map_err(|_|Fault("ROLE_MISMATCH"))?;
    let role=text(&v["roles"],role_name)?;
    let artifacts=v["artifacts"].as_array().ok_or(INVALID)?;
    if artifacts.is_empty() || artifacts.len()>4096{return Err(INVALID);}
    if inventory.len()!=artifacts.len(){return Err(Fault("INTEGRITY_MISMATCH"));}
    let mut names=BTreeSet::new();let mut original=BTreeSet::new();let mut entrypoints=Vec::new();
    for item in artifacts{
        exact(item,&["path","kind","size","sha256"])?;
        let path=text(item,"path")?;let key=safe_path(path)?;
        if key=="protected-runtime-manifest.json" || !names.insert(key){return Err(INVALID);}
        original.insert(path.to_owned());
        let kind=text(item,"kind")?;
        if !["entrypoint","native_library","python_archive","data","license"].contains(&kind){return Err(INVALID);}
        if kind=="entrypoint"{entrypoints.push(path);}
        if path.to_ascii_lowercase().ends_with(".exe") != (kind=="entrypoint"){return Err(INVALID);}
        let size=item["size"].as_u64().ok_or(INVALID)?;
        let expected=text(item,"sha256")?;
        if expected.len()!=64 || !expected.bytes().all(|c|c.is_ascii_digit() || (b'a'..=b'f').contains(&c)){return Err(INVALID);}
        let data=inventory.get(path).ok_or(Fault("INTEGRITY_MISMATCH"))?;
        if data.len() as u64!=size || digest(data)!=expected{return Err(Fault("INTEGRITY_MISMATCH"));}
    }
    if entrypoints.len()!=1 || entrypoints[0]!=role{return Err(Fault("ROLE_MISMATCH"));}
    if original!=inventory.keys().cloned().collect(){return Err(Fault("INTEGRITY_MISMATCH"));}
    Ok(())
}

#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub enum State{Absent,Verifying,Starting,Ready,Stopping,Stopped,Failed}
#[derive(Debug,Clone)]
struct Pending{id:String,method:String,deadline:u64,bytes:Vec<u8>}

/// A completion timestamp is supplied by the transport clock. This batch has
/// only a test transport; this interface does not make blocking native I/O safe.
pub trait Transport{fn write(&mut self,bytes:&[u8],absolute_deadline:u64)->Result<u64>;}

pub struct Supervisor{
    binding:Binding,session:String,limits:Limits,state:State,
    seen:BTreeSet<String>,queue:VecDeque<Pending>,pending:BTreeMap<String,Pending>,
    cancelled:BTreeMap<String,Pending>,drain_deadline:Option<u64>,
    startup_deadline:Option<u64>,stop_deadline:Option<u64>,next_health:u64,
    last_now:u64,last_error:Option<Fault>,
}
impl Supervisor{
    pub fn new(binding:Binding,session:&str,limits:Limits)->Result<Self>{
        uuid4(session)?;limits.validate()?;
        Ok(Self{binding,session:session.into(),limits,state:State::Absent,seen:BTreeSet::new(),
            queue:VecDeque::new(),pending:BTreeMap::new(),startup_deadline:None,
            cancelled:BTreeMap::new(),drain_deadline:None,
            stop_deadline:None,next_health:0,last_now:0,last_error:None})
    }
    pub fn state(&self)->State{self.state}
    pub fn outstanding(&self)->usize{self.queue.len()+self.pending.len()}
    pub fn last_error(&self)->Option<Fault>{self.last_error}
    pub fn next_deadline(&self)->Option<u64>{
        self.queue.iter().chain(self.pending.values()).map(|p|p.deadline)
            .chain(self.startup_deadline).chain(self.stop_deadline).min()
    }
    fn fail<T>(&mut self,error:Fault)->Result<T>{
        self.state=State::Failed;self.last_error=Some(error);self.queue.clear();self.pending.clear();self.cancelled.clear();Err(error)
    }
    fn observe(&mut self,now:u64)->Result<()>{
        if now<self.last_now{return self.fail(INVALID);}
        self.last_now=now;
        if self.state==State::Failed{return Err(self.last_error.unwrap_or(INVALID));}
        if self.state==State::Stopped{return Err(INVALID);}
        Ok(())
    }
    pub fn begin(&mut self,now:u64)->Result<()>{
        self.observe(now)?;
        if self.state!=State::Absent{return self.fail(INVALID);}
        let d=match deadline(now,self.limits.startup_ms){Ok(d)=>d,Err(e)=>return self.fail(e)};
        self.startup_deadline=Some(d);self.state=State::Verifying;Ok(())
    }
    pub fn verified(&mut self,now:u64)->Result<()>{
        self.tick(now)?;
        if self.state!=State::Verifying{return self.fail(INVALID);}
        let first=match deadline(now,self.limits.first_health_ms){Ok(d)=>d,Err(e)=>return self.fail(e)};
        self.startup_deadline=Some(self.startup_deadline.unwrap_or(first).min(first));
        self.state=State::Starting;Ok(())
    }
    pub fn health_due(&self,now:u64)->bool{
        self.state==State::Ready && now>=self.next_health
            && !self.queue.iter().chain(self.pending.values()).any(|p|p.method=="health")
    }
    pub fn tick(&mut self,now:u64)->Result<()>{
        self.observe(now)?;
        if self.stop_deadline.is_some_and(|d|now>=d){return self.fail(Fault("SHUTDOWN_TIMEOUT"));}
        if self.startup_deadline.is_some_and(|d|now>=d){return self.fail(Fault("HEALTH_TIMEOUT"));}
        if self.state==State::Ready && self.next_health.checked_add(self.limits.request_ms).is_none_or(|d|now>=d){
            return self.fail(Fault("HEALTH_TIMEOUT"));
        }
        if let Some(p)=self.queue.iter().chain(self.pending.values()).find(|p|now>=p.deadline){
            let error=Self::timeout(&p.method);return self.fail(error);
        }
        Ok(())
    }
    fn timeout(method:&str)->Fault{Fault(match method{"health"=>"HEALTH_TIMEOUT","shutdown"=>"SHUTDOWN_TIMEOUT",_=>"REQUEST_TIMEOUT"})}
    pub fn enqueue(&mut self,method:&str,id:&str,now:u64)->Result<()>{
        self.tick(now)?;
        if !["health","status","shutdown"].contains(&method){return self.fail(Fault("EXECUTION_DISABLED"));}
        if uuid4(id).is_err() || self.seen.contains(id){return self.fail(INVALID);}
        if self.seen.len()>=self.limits.seen_ids || self.queue.len()>=self.limits.queue{return self.fail(Fault("RESOURCE_LIMIT"));}
        if method=="shutdown" || !matches!(self.state,State::Starting|State::Ready)
            || (self.state==State::Starting && (method!="health" || self.outstanding()!=0)) {return self.fail(INVALID);}
        let span=if self.state==State::Starting{self.limits.first_health_ms}else{self.limits.request_ms};
        let mut d=match deadline(now,span){Ok(d)=>d,Err(e)=>return self.fail(e)};
        if let Some(start)=self.startup_deadline{d=d.min(start);}
        self.push(method,id,d)
    }
    fn push(&mut self,method:&str,id:&str,d:u64)->Result<()>{
        let value=serde_json::json!({"v":1,"id":id,"session":self.session,"method":method,"params":{}});
        let bytes=format!("{value}\n").into_bytes();
        if parse_frame(&bytes,&self.limits).is_err(){return self.fail(Fault("RESOURCE_LIMIT"));}
        self.seen.insert(id.into());
        self.queue.push_back(Pending{id:id.into(),method:method.into(),deadline:d,bytes});Ok(())
    }
    pub fn send_next(&mut self,transport:&mut impl Transport,now:u64)->Result<bool>{
        self.tick(now)?;
        if self.pending.len()>=self.limits.pending{return Ok(false);}
        let Some(p)=self.queue.pop_front() else{return Ok(false)};
        let completed=match transport.write(&p.bytes,p.deadline){Ok(t)=>t,Err(e)=>return self.fail(e)};
        if completed<now{return self.fail(INVALID);}
        if let Err(e)=self.tick(completed){return Err(e);}
        if completed>=p.deadline{return self.fail(Self::timeout(&p.method));}
        self.pending.insert(p.id.clone(),p);Ok(true)
    }
    pub fn receive(&mut self,raw:&[u8],now:u64)->Result<()>{
        self.tick(now)?;
        match self.validate_reply(raw,now){Ok(())=>Ok(()),Err(e)=>self.fail(e)}
    }
    fn validate_reply(&mut self,raw:&[u8],now:u64)->Result<()>{
        let v=parse_frame(raw,&self.limits)?;
        let is_error=v.get("error").is_some();
        exact(&v,if is_error{&["v","id","session","error"]}else{&["v","id","session","result"]})?;
        if !integer(&v,"v",1) || text(&v,"session")?!=self.session{return Err(INVALID);}
        let id=text(&v,"id")?;uuid4(id)?;
        let cancelled=self.cancelled.contains_key(id);
        let p=self.pending.get(id).or_else(||self.cancelled.get(id)).ok_or(INVALID)?;
        if cancelled && (self.state!=State::Stopping || self.drain_deadline.is_none_or(|d|now>=d)){return Err(INVALID);}
        if now>=p.deadline{return Err(Self::timeout(&p.method));}
        if is_error{
            exact(&v["error"],&["code","message"])?;
            let code=text(&v["error"],"code")?;
            if text(&v["error"],"message")?!=code{return Err(INVALID);}
            return Err(match code{"EXECUTION_DISABLED"=>Fault("EXECUTION_DISABLED"),"RESOURCE_LIMIT"=>Fault("RESOURCE_LIMIT"),_=>INVALID});
        }
        let r=&v["result"];
        exact(r,&["role","contract_version","bundle_id","artifact_version","state","execution_enabled","child_roles"])?;
        if text(r,"role")?!="protected-host-management"{return Err(Fault("ROLE_MISMATCH"));}
        if !integer(r,"contract_version",1) || text(r,"bundle_id")?!=self.binding.bundle_id
            || text(r,"artifact_version")?!=self.binding.artifact_version{return Err(Fault("VERSION_MISMATCH"));}
        if r["execution_enabled"]!=Value::Bool(false){return Err(Fault("EXECUTION_DISABLED"));}
        exact(&r["child_roles"],&["oi","broker"])?;
        if text(&r["child_roles"],"oi")?!="not_started" || text(&r["child_roles"],"broker")?!="not_started"{return Err(INVALID);}
        let wanted=if p.method=="shutdown"{"stopping"}else{"disabled_ready"};
        if text(r,"state")?!=wanted{return Err(INVALID);}
        let health=p.method=="health";
        if cancelled{
            // Only a recognized, still-in-budget reply to a previously sent request
            // is drained. It never completes a live caller or revives Ready.
            self.cancelled.remove(id);return Ok(());
        }
        self.pending.remove(id);
        if health{
            if self.state==State::Starting{self.state=State::Ready;self.startup_deadline=None;}
            self.next_health=deadline(now,self.limits.heartbeat_ms)?;
        }
        Ok(())
    }
    pub fn request_shutdown(&mut self,id:&str,now:u64)->Result<()>{
        self.tick(now)?;
        if self.state!=State::Ready || uuid4(id).is_err() || self.seen.contains(id){return self.fail(INVALID);}
        if self.seen.len()>=self.limits.seen_ids{return self.fail(Fault("RESOURCE_LIMIT"));}
        self.queue.clear();self.cancelled=std::mem::take(&mut self.pending);self.state=State::Stopping;
        let total=match deadline(now,self.limits.shutdown_ms){Ok(d)=>d,Err(e)=>return self.fail(e)};
        self.stop_deadline=Some(total);
        let grace=match deadline(now,self.limits.shutdown_grace_ms){Ok(d)=>d,Err(e)=>return self.fail(e)};
        self.drain_deadline=Some(grace);
        self.push("shutdown",id,total.min(grace))
    }
    /// Caller supplies exit confirmation; no OS observation happens in this module.
    pub fn confirm_exit(&mut self,now:u64)->Result<()>{
        self.tick(now)?;
        if self.state!=State::Stopping{return self.fail(INVALID);}
        self.queue.clear();self.pending.clear();self.cancelled.clear();self.stop_deadline=None;self.state=State::Stopped;Ok(())
    }
}
