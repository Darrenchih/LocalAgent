//! Human-selected workspace bindings. No model output is accepted as a selection.
//! Persisted settings are descriptive; directory identity and scope are rechecked.
use serde::{Deserialize,Serialize};
use serde_json::{json,Value};
use std::{collections::BTreeMap,fs::{self,File,OpenOptions},io::Write,mem::zeroed,
    os::windows::{fs::OpenOptionsExt,io::AsRawHandle},path::{Component,Path,PathBuf,Prefix}};
use windows_sys::Win32::{Foundation::HANDLE,Storage::FileSystem::*};

pub const TEST_ROOT:&str="D:\\Agent\\desktop-agent\\work\\agent-integration\\workspace";
const SETTINGS_LIMIT:usize=262144;
pub type Result<T>=std::result::Result<T,String>;

#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DirectoryIdentity{volume:u32,index:u64}
fn info(file:&File)->Result<BY_HANDLE_FILE_INFORMATION>{
    let mut value:BY_HANDLE_FILE_INFORMATION=unsafe{zeroed()};
    if unsafe{GetFileInformationByHandle(file.as_raw_handle() as HANDLE,&mut value)}==0{return Err("WORKSPACE_ACCESS_DENIED".into());}
    Ok(value)
}
fn identity(file:&File)->Result<DirectoryIdentity>{let v=info(file)?;Ok(DirectoryIdentity{volume:v.dwVolumeSerialNumber,index:((v.nFileIndexHigh as u64)<<32)|v.nFileIndexLow as u64})}
fn pin_directory(path:&Path)->Result<File>{
    let f=OpenOptions::new().read(true).share_mode(FILE_SHARE_READ|FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS).open(path).map_err(|_|"WORKSPACE_ACCESS_DENIED")?;
    let v=info(&f)?;
    if v.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT!=0 || v.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY==0{return Err("WORKSPACE_UNSAFE_PATH".into());}
    Ok(f)
}
fn normal(path:&Path)->String{path.to_string_lossy().trim_start_matches("\\\\?\\").replace('/',"\\").trim_end_matches('\\').to_ascii_lowercase()}
fn scoped(path:&Path)->bool{let root=normal(Path::new(TEST_ROOT));let value=normal(path);value==root||value.starts_with(&(root+"\\"))}
fn lexical(raw:&str)->Result<PathBuf>{
    if raw.is_empty()||raw.len()>240||raw.contains('\0'){return Err("WORKSPACE_INVALID_PATH".into());}
    let value=raw.replace('/',"\\");
    let path=PathBuf::from(&value);
    if !path.is_absolute()||!matches!(path.components().next(),Some(Component::Prefix(p)) if matches!(p.kind(),Prefix::Disk(_))){return Err("WORKSPACE_INVALID_PATH".into());}
    for part in value.get(3..).ok_or("WORKSPACE_INVALID_PATH")?.split('\\'){
        if part.is_empty(){continue;}
        let stem=part.split('.').next().unwrap_or("").trim_end_matches([' ','.']).to_ascii_uppercase();
        if part=="."||part==".."||part.ends_with([' ','.'])||part.chars().any(|c|c<' '||"<>:\"|?*".contains(c))
            ||["CON","PRN","AUX","NUL"].contains(&stem.as_str())
            || (stem.len()==4&&(stem.starts_with("COM")||stem.starts_with("LPT"))&&stem.as_bytes()[3].is_ascii_digit()){
            return Err("WORKSPACE_INVALID_PATH".into());
        }
    }
    Ok(path)
}

/// Held for the entire task, so the selected directory and every ancestor cannot
/// be renamed/deleted between the user approval and Broker initialization or IO.
pub struct WorkspaceLease{pub path:PathBuf,pub identity:DirectoryIdentity,pins:Vec<File>}
impl WorkspaceLease{
    pub fn open(raw:&str)->Result<Self>{
        let supplied=lexical(raw)?;
        if !scoped(&supplied){return Err("OUTSIDE_AUTHORIZED_ROOT".into());}
        if !raw.is_ascii(){return Err("WORKSPACE_UNSUPPORTED_NAME".into());}
        let mut ancestors:Vec<_>=supplied.ancestors().collect();ancestors.reverse();
        if ancestors.len()>48{return Err("WORKSPACE_INVALID_PATH".into());}
        let mut pins=Vec::new();for ancestor in ancestors{pins.push(pin_directory(ancestor)?);}
        let path=supplied.canonicalize().map_err(|_|"WORKSPACE_ACCESS_DENIED")?;
        if !scoped(&path){return Err("OUTSIDE_AUTHORIZED_ROOT".into());}
        let id=identity(pins.last().ok_or("WORKSPACE_INVALID_PATH")?)?;
        // Use an ordinary absolute spelling on the Python wire. The held handles
        // retain identity; canonicalize is not used to hide a reparse component.
        let path=PathBuf::from(path.to_string_lossy().trim_start_matches("\\\\?\\").to_owned());
        Ok(Self{path,identity:id,pins})
    }
    pub fn verify(&self)->Result<()>{
        for f in &self.pins{let v=info(f)?;if v.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT!=0||v.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY==0{return Err("WORKSPACE_UNSAFE_PATH".into());}}
        if identity(self.pins.last().ok_or("WORKSPACE_INVALID_PATH")?)?!=self.identity{return Err("WORKSPACE_IDENTITY_CHANGED".into());}
        Ok(())
    }
}

#[derive(Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection{path:String,identity:Option<DirectoryIdentity>,revision:String}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved{schema:u32,selections:BTreeMap<String,Selection>}

#[derive(Clone,Debug,Serialize)]
pub struct WorkspaceStatus{
    pub conversation_id:Option<String>,pub path:Option<String>,pub allowed_root:String,pub enabled:bool,pub reason:Option<String>,pub revision:String,
    pub permissions:Vec<String>,pub tools:Vec<Value>,pub templates:Vec<Value>,
}
fn descriptors(enabled:bool)->Vec<Value>{[
    ("list_directory","list scoped directory"),("read_file","read scoped file"),("write_file","modify scoped file"),
    ("create_directory","create scoped directory"),("search_text","search scoped text"),("powershell","run fixed PowerShell JSON test")
].iter().map(|(name,risk)|json!({"name":name,"risk":risk,"enabled":enabled,"source":"builtin"})).collect()}

pub struct WorkspaceStore{file:PathBuf,saved:Saved,error:Option<String>,epoch:String,default_enabled:bool}
fn valid_id(id:&str)->bool{uuid::Uuid::parse_str(id).is_ok_and(|v|v.get_version_num()==4&&v.to_string()==id)}
impl WorkspaceStore{
    pub fn new(data_dir:&Path,default_enabled:bool)->Self{
        let file=data_dir.join("agent-workspaces.json");
        let loaded=(||->Result<Saved>{
            if !data_dir.is_absolute()||data_dir.components().any(|c|matches!(c,Component::ParentDir|Component::CurDir)){
                return Err("WORKSPACE_SETTINGS_INVALID".into());
            }
            if scoped(data_dir){return Err("WORKSPACE_SETTINGS_WITHIN_SCOPE".into());}
            if data_dir.exists(){
                let mut parents:Vec<_>=data_dir.ancestors().collect();parents.reverse();let mut pins=Vec::new();for p in parents{pins.push(pin_directory(p)?);}
                if scoped(&data_dir.canonicalize().map_err(|_|"WORKSPACE_SETTINGS_INVALID")?){return Err("WORKSPACE_SETTINGS_WITHIN_SCOPE".into());}
            }
            if !file.exists(){return Ok(Saved{schema:1,selections:BTreeMap::new()});}
            let meta=fs::symlink_metadata(&file).map_err(|_|"WORKSPACE_SETTINGS_INVALID")?;
            use std::os::windows::fs::MetadataExt;
            if meta.file_attributes()&FILE_ATTRIBUTE_REPARSE_POINT!=0||meta.len()>SETTINGS_LIMIT as u64{return Err("WORKSPACE_SETTINGS_INVALID".into());}
            let raw=fs::read(&file).map_err(|_|"WORKSPACE_SETTINGS_INVALID")?;
            let value=crate::protected_runtime::strict_json(&raw,SETTINGS_LIMIT,&crate::protected_runtime::Limits::default()).map_err(|_|"WORKSPACE_SETTINGS_INVALID")?;
            let saved:Saved=serde_json::from_value(value).map_err(|_|"WORKSPACE_SETTINGS_INVALID")?;
            if saved.schema!=1||saved.selections.len()>256||saved.selections.iter().any(|(k,v)|!valid_id(k)||v.path.len()>240||!valid_id(&v.revision)){return Err("WORKSPACE_SETTINGS_INVALID".into());}
            Ok(saved)
        })();
        let (saved,error)=match loaded{Ok(s)=>(s,None),Err(e)=>(Saved{schema:1,selections:BTreeMap::new()},Some(e))};
        Self{file,saved,error,epoch:uuid::Uuid::new_v4().to_string(),default_enabled}
    }
    fn selection(&self,id:Option<&str>)->Option<Selection>{
        id.and_then(|i|self.saved.selections.get(i).cloned()).or_else(||if self.default_enabled{Some(Selection{path:TEST_ROOT.into(),identity:None,revision:self.epoch.clone()})}else{None})
    }
    pub fn get(&self,id:Option<&str>)->WorkspaceStatus{
        let selected=self.selection(id);let path=selected.as_ref().map(|s|s.path.clone());
        let revision=format!("{}:{}",self.epoch,selected.as_ref().map(|s|s.revision.as_str()).unwrap_or("unselected"));
        let error=if let Some(error)=&self.error{Some(error.clone())}else if id.is_some_and(|i|!valid_id(i)){Some("WORKSPACE_INVALID_CONVERSATION".into())}
            else if let Some(selection)=&selected{match WorkspaceLease::open(&selection.path){
                Ok(lease)=>if selection.identity.as_ref().is_some_and(|v|v!=&lease.identity){Some("WORKSPACE_IDENTITY_CHANGED".into())}
                    else if selection.identity.is_none()&&id.is_some_and(|i|self.saved.selections.contains_key(i)){Some("WORKSPACE_RESELECT_REQUIRED".into())}else{None},Err(e)=>Some(e)}}
            else{Some("WORKSPACE_NOT_SELECTED".into())};
        let enabled=error.is_none();
        WorkspaceStatus{conversation_id:id.map(str::to_owned),path,allowed_root:TEST_ROOT.into(),enabled,reason:error,revision,
            permissions:if enabled{vec!["read".into(),"write".into(),"create_directory".into(),"fixed_powershell:test_json".into()]}else{Vec::new()},
            tools:descriptors(enabled),templates:vec![json!({"name":"test_json","enabled":enabled}),json!({"name":"project_tests","enabled":false})]}
    }
    fn persist(&self)->Result<()>{
        if self.error.is_some(){return Err("WORKSPACE_SETTINGS_INVALID".into());}
        let parent=self.file.parent().ok_or("WORKSPACE_SETTINGS_INVALID")?;
        if scoped(parent){return Err("WORKSPACE_SETTINGS_WITHIN_SCOPE".into());}
        fs::create_dir_all(parent).map_err(|_|"WORKSPACE_SETTINGS_WRITE_FAILED")?;
        let mut ancestors:Vec<_>=parent.ancestors().collect();ancestors.reverse();let mut pins=Vec::new();
        for ancestor in ancestors{pins.push(pin_directory(ancestor)?);}
        if scoped(&parent.canonicalize().map_err(|_|"WORKSPACE_SETTINGS_INVALID")?){return Err("WORKSPACE_SETTINGS_WITHIN_SCOPE".into());}
        if self.file.exists(){
            let f=OpenOptions::new().read(true).share_mode(FILE_SHARE_READ).custom_flags(FILE_FLAG_OPEN_REPARSE_POINT).open(&self.file).map_err(|_|"WORKSPACE_SETTINGS_INVALID")?;
            let i=info(&f)?;if i.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT!=0||i.nNumberOfLinks!=1{return Err("WORKSPACE_SETTINGS_INVALID".into());}
        }
        let raw=serde_json::to_vec(&self.saved).map_err(|_|"WORKSPACE_SETTINGS_INVALID")?;
        if raw.len()>SETTINGS_LIMIT{return Err("WORKSPACE_SETTINGS_LIMIT".into());}
        let temp=parent.join(format!(".agent-workspaces-{}.tmp",uuid::Uuid::new_v4()));
        let write=(||{let mut f=OpenOptions::new().write(true).create_new(true).open(&temp).map_err(|_|"WORKSPACE_SETTINGS_WRITE_FAILED")?;
            f.write_all(&raw).and_then(|_|f.sync_all()).map_err(|_|"WORKSPACE_SETTINGS_WRITE_FAILED")?;drop(f);
            fs::rename(&temp,&self.file).map_err(|_|"WORKSPACE_SETTINGS_WRITE_FAILED")?;Ok(())})();
        // This exact temporary file was created by this call in a pinned directory.
        if write.is_err(){let _=fs::remove_file(&temp);}drop(pins);write
    }
    pub fn select(&mut self,id:&str,raw:&str)->Result<WorkspaceStatus>{
        if !valid_id(id){return Err("WORKSPACE_INVALID_CONVERSATION".into());}
        if self.saved.selections.len()>=256&&!self.saved.selections.contains_key(id){return Err("WORKSPACE_SETTINGS_LIMIT".into());}
        let path=lexical(raw)?;
        let candidate=if scoped(&path){match WorkspaceLease::open(raw){Ok(lease)=>Selection{path:lease.path.to_string_lossy().to_string(),identity:Some(lease.identity),revision:uuid::Uuid::new_v4().to_string()},Err(_)=>Selection{path:raw.replace('/',"\\"),identity:None,revision:uuid::Uuid::new_v4().to_string()}}}
            else{Selection{path:raw.replace('/',"\\"),identity:None,revision:uuid::Uuid::new_v4().to_string()}};
        let old=self.saved.selections.insert(id.to_owned(),candidate);
        if let Err(e)=self.persist(){match old{Some(v)=>{self.saved.selections.insert(id.to_owned(),v);},None=>{self.saved.selections.remove(id);}}return Err(e);}
        Ok(self.get(Some(id)))
    }
    pub fn lease(&mut self,id:&str)->Result<(WorkspaceLease,WorkspaceStatus)>{
        if !valid_id(id){return Err("WORKSPACE_INVALID_CONVERSATION".into());}
        if !self.saved.selections.contains_key(id)&&self.default_enabled{self.select(id,TEST_ROOT)?;}
        let status=self.get(Some(id));
        if !status.enabled{return Err(status.reason.unwrap_or("WORKSPACE_DISABLED".into()));}
        let lease=WorkspaceLease::open(status.path.as_deref().ok_or("WORKSPACE_DISABLED")?)?;
        let selected=self.saved.selections.get(id).ok_or("WORKSPACE_DISABLED")?;
        if selected.identity.as_ref()!=Some(&lease.identity){return Err("WORKSPACE_IDENTITY_CHANGED".into());}
        Ok((lease,status))
    }
}

#[cfg(test)]mod tests{
    use super::*;
    fn fixture()->(PathBuf,PathBuf){let id=uuid::Uuid::new_v4().to_string();let root=Path::new(TEST_ROOT).join("work/review043").join(&id);let data=Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().join("work/review-043/native-settings").join(id);fs::create_dir_all(&root).unwrap();(root,data)}
    #[test]fn persisted_scope_is_per_conversation_and_identity_is_revalidated(){
        let (root,data)=fixture();let child=root.join("child");fs::create_dir(&child).unwrap();let a=uuid::Uuid::new_v4().to_string();let b=uuid::Uuid::new_v4().to_string();
        let mut store=WorkspaceStore::new(&data,false);assert!(!store.get(Some(&a)).enabled);
        let first=store.select(&a,root.to_str().unwrap()).unwrap();assert!(first.enabled);assert!(!store.get(Some(&b)).enabled);
        let second=store.select(&b,child.to_str().unwrap()).unwrap();assert!(second.enabled);assert_ne!(first.revision,second.revision);
        let mut resumed=WorkspaceStore::new(&data,false);let again=resumed.get(Some(&a));assert!(again.enabled);assert_ne!(again.revision,first.revision);
        let (lease,_)=resumed.lease(&a).unwrap();assert!(fs::rename(&root,root.with_extension("moved")).is_err());drop(lease);
        fs::rename(&child,root.join("old-child")).unwrap();fs::create_dir(&child).unwrap();assert_eq!(resumed.get(Some(&b)).reason.as_deref(),Some("WORKSPACE_IDENTITY_CHANGED"));
    }
    #[test]fn outside_root_invalid_paths_and_tampered_settings_fail_closed(){
        let (_,data)=fixture();let id=uuid::Uuid::new_v4().to_string();let mut store=WorkspaceStore::new(&data,false);
        let status=store.select(&id,"C:\\Windows").unwrap();assert!(!status.enabled);assert_eq!(status.reason.as_deref(),Some("OUTSIDE_AUTHORIZED_ROOT"));
        let unicode=store.select(&id,"C:\\使用者\\資料").unwrap();assert!(!unicode.enabled);assert_eq!(unicode.path.as_deref(),Some("C:\\使用者\\資料"));
        assert!(store.lease(&id).is_err());assert!(store.select(&id,&format!("{}\\..\\escape",TEST_ROOT)).is_err());
        fs::write(data.join("agent-workspaces.json"),b"{\"schema\":1,\"schema\":1,\"selections\":{}}").unwrap();
        let tampered=WorkspaceStore::new(&data,true);assert!(!tampered.get(Some(&id)).enabled);
    }
    #[test]fn invalid_selection_cannot_gain_permission_when_created_later(){
        let (root,data)=fixture();let path=root.join("not-created-yet");let id=uuid::Uuid::new_v4().to_string();let mut store=WorkspaceStore::new(&data,false);
        assert!(!store.select(&id,path.to_str().unwrap()).unwrap().enabled);fs::create_dir(&path).unwrap();assert_eq!(store.get(Some(&id)).reason.as_deref(),Some("WORKSPACE_RESELECT_REQUIRED"));assert!(store.lease(&id).is_err());
        assert!(store.select(&id,path.to_str().unwrap()).unwrap().enabled);
    }
}
