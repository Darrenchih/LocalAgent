//! Windows-only management supervisor. Not connected to Desktop or tool dispatch.
//! Every launch joins its private Job during CreateProcess, before the child runs.
use crate::protected_runtime::{self as contract, Binding, Fault, Limits, Supervisor, Transport};
use std::{collections::{BTreeMap,BTreeSet,VecDeque}, ffi::OsStr, fs::{self,File,OpenOptions},sync::Arc,
    io::Read, mem::{size_of,zeroed}, os::windows::{ffi::OsStrExt,fs::OpenOptionsExt,io::AsRawHandle},
    path::{Path,PathBuf}, ptr::{null,null_mut}, time::{Duration,Instant}};
use windows_sys::Win32::{Foundation::*,Security::{SECURITY_ATTRIBUTES,GetTokenInformation,TokenElevation,TOKEN_ELEVATION,TOKEN_QUERY},Storage::FileSystem::*,
    System::{IO::*,JobObjects::*,Pipes::*,ProcessStatus::*,Threading::*}};
type Result<T> = std::result::Result<T,Fault>;
fn err(code:&'static str)->Fault{Fault(code)}
fn wide(s:impl AsRef<OsStr>)->Vec<u16>{s.as_ref().encode_wide().chain(Some(0)).collect()}
fn norm(p:&Path)->String{p.to_string_lossy().trim_start_matches("\\\\?\\").replace('/',"\\").to_lowercase()}

struct Handle(HANDLE);
impl Handle{
    fn take(h:HANDLE)->Result<Self>{if h.is_null() || h==INVALID_HANDLE_VALUE{Err(err("NATIVE_HANDLE_FAILED"))}else{Ok(Self(h))}}
}
impl Drop for Handle{fn drop(&mut self){unsafe{CloseHandle(self.0);}}}
pub struct ObservedProcess(Handle);
/// A separately owned, non-inheritable cancellation handle for this private Job.
pub struct JobCancel(Handle);
unsafe impl Send for JobCancel{}
unsafe impl Sync for JobCancel{}
impl JobCancel{pub fn cancel(&self){unsafe{TerminateJobObject(self.0.0,43);}}}
/// Transitional same-session guards; never used to kill a different app instance.
pub struct CompatGuard{_stable:Handle,_legacy:Handle}
// These owned kernel handles have no mutable user-mode state; CloseHandle may
// run on the last Arc's thread. No borrowed or inheritable handle is exposed.
unsafe impl Send for CompatGuard{}
unsafe impl Sync for CompatGuard{}
impl CompatGuard{
    pub fn acquire()->Result<Self>{
        fn one(name:&str)->Result<Handle>{unsafe{
            let value=wide(name);let handle=Handle::take(CreateMutexW(null(),0,value.as_ptr()))?;
            if GetLastError()==ERROR_ALREADY_EXISTS{return Err(err("APP_ALREADY_RUNNING"));}Ok(handle)
        }}
        let stable=one("Local\\LocalAgentDesktop")?;let legacy=one("Local\\LocalAgentDesktop036")?;
        Ok(Self{_stable:stable,_legacy:legacy})
    }
}
impl ObservedProcess{
    pub fn open(pid:u32)->Result<Self>{unsafe{Ok(Self(Handle::take(OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,0,pid))?))}}
    pub fn wait(&self,timeout:Duration)->bool{unsafe{WaitForSingleObject(self.0.0,timeout.as_millis().min(u32::MAX as u128) as u32)==WAIT_OBJECT_0}}
}

/// Immutable byte identity plus held handles. This is not a hostile same-user sandbox.
pub struct PinnedBundle{root:PathBuf,entry:PathBuf,files:BTreeSet<String>,_pins:Vec<File>}
fn pin(path:&Path,directory:bool)->Result<File>{
    let mut options=OpenOptions::new();options.read(true).share_mode(if directory{FILE_SHARE_READ|FILE_SHARE_WRITE}else{FILE_SHARE_READ});
    options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | if directory{FILE_FLAG_BACKUP_SEMANTICS}else{0});
    let file=options.open(path).map_err(|_|err("INTEGRITY_MISMATCH"))?;
    let mut info:BY_HANDLE_FILE_INFORMATION=unsafe{zeroed()};
    if unsafe{GetFileInformationByHandle(file.as_raw_handle() as HANDLE,&mut info)}==0
        || info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT!=0
        || (info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY!=0)!=directory
        || (!directory && info.nNumberOfLinks!=1){return Err(err("INTEGRITY_MISMATCH"));}
    Ok(file)
}
fn collect(root:&Path,dir:&Path,pins:&mut Vec<File>,files:&mut BTreeMap<String,Vec<u8>>,until:Instant)->Result<()>{
    if dir.components().count()>root.components().count()+32 || pins.len()>8192{return Err(err("RESOURCE_LIMIT"));}
    for e in fs::read_dir(dir).map_err(|_|err("RUNTIME_MISSING"))?{
        if Instant::now()>=until{return Err(err("HEALTH_TIMEOUT"));}
        let path=e.map_err(|_|err("RUNTIME_MISSING"))?.path();
        let metadata=fs::symlink_metadata(&path).map_err(|_|err("INTEGRITY_MISMATCH"))?;
        if metadata.is_dir(){pins.push(pin(&path,true)?);collect(root,&path,pins,files,until)?;}
        else{
            let relative=path.strip_prefix(root).map_err(|_|err("INTEGRITY_MISMATCH"))?.to_str().ok_or(err("INTEGRITY_MISMATCH"))?.replace('\\',"/");
            let mut file=pin(&path,false)?;
            if metadata.len()>128*1024*1024 || files.len()>=4096{return Err(err("RESOURCE_LIMIT"));}
            let mut bytes=Vec::new();file.read_to_end(&mut bytes).map_err(|_|err("INTEGRITY_MISMATCH"))?;
            if files.values().map(Vec::len).sum::<usize>()+bytes.len()>256*1024*1024{return Err(err("RESOURCE_LIMIT"));}
            pins.push(file);files.insert(relative,bytes);
        }
    }
    Ok(())
}
impl PinnedBundle{
    fn verify_membership(&self)->Result<()>{
        fn names(root:&Path,dir:&Path,out:&mut BTreeSet<String>,directories:&mut usize)->Result<()>{
            *directories+=1;
            if *directories>4096 || dir.components().count()>root.components().count()+32{return Err(err("RESOURCE_LIMIT"));}
            for entry in fs::read_dir(dir).map_err(|_|err("INTEGRITY_MISMATCH"))?{
                let p=entry.map_err(|_|err("INTEGRITY_MISMATCH"))?.path();
                let meta=fs::symlink_metadata(&p).map_err(|_|err("INTEGRITY_MISMATCH"))?;
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes()&FILE_ATTRIBUTE_REPARSE_POINT!=0{return Err(err("INTEGRITY_MISMATCH"));}
                if meta.is_dir(){names(root,&p,out,directories)?;}else{
                    if out.len()>=4097{return Err(err("RESOURCE_LIMIT"));}out.insert(norm(&p));
                }
            }Ok(())
        }
        let mut current=BTreeSet::new();names(&self.root,&self.root,&mut current,&mut 0)?;
        let mut expected=self.files.clone();expected.insert(norm(&self.root.join("protected-runtime-manifest.json")));
        if current!=expected{return Err(err("INTEGRITY_MISMATCH"));}
        for file in &self._pins{unsafe{
            let mut info:BY_HANDLE_FILE_INFORMATION=zeroed();
            if GetFileInformationByHandle(file.as_raw_handle() as HANDLE,&mut info)==0
                || info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT!=0
                || (info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY==0 && info.nNumberOfLinks!=1){return Err(err("INTEGRITY_MISMATCH"));}
        }}Ok(())
    }
    pub fn open(root:&Path,expected_digest:&str,until:Instant)->Result<Self>{
        Self::open_profile(root,expected_digest,until,false)
    }
    pub fn open_agent(root:&Path,expected_digest:&str,until:Instant)->Result<Self>{
        Self::open_profile(root,expected_digest,until,true)
    }
    fn open_profile(root:&Path,expected_digest:&str,until:Instant,agent:bool)->Result<Self>{
        if !root.is_absolute(){return Err(err("RUNTIME_MISSING"));}
        use std::path::{Component,Prefix};
        if !matches!(root.components().next(),Some(Component::Prefix(p)) if matches!(p.kind(),Prefix::Disk(_)|Prefix::VerbatimDisk(_)))
            || root.components().any(|c|matches!(c,Component::ParentDir|Component::CurDir)){return Err(err("RUNTIME_MISSING"));}
        // Pin the supplied path before canonicalizing, so a junction cannot disappear
        // into canonical spelling and escape the reparse check.
        let mut pins=Vec::new();
        let mut ancestors:Vec<_>=root.ancestors().collect();ancestors.reverse();
        for ancestor in ancestors{pins.push(pin(ancestor,true)?);}
        let root=root.canonicalize().map_err(|_|err("RUNTIME_MISSING"))?;
        let mut inventory=BTreeMap::new();collect(&root,&root,&mut pins,&mut inventory,until)?;
        let raw=inventory.remove("protected-runtime-manifest.json").ok_or(err("RUNTIME_MISSING"))?;
        let binding=Binding::new(if agent{"localagent-agent-test-v1"}else{env!("PROTECTED_RUNTIME_BUNDLE_ID")},env!("PROTECTED_RUNTIME_ARTIFACT_VERSION"))?;
        let role=if agent{"protected-agent-broker"}else{"protected-host-management"};
        contract::validate_manifest_profile(&raw,expected_digest,&binding,&inventory,role,if agent{"approval_required"}else{"disabled"})?;
        let manifest:serde_json::Value=serde_json::from_slice(&raw).map_err(|_|err("PROTOCOL_INVALID"))?;
        let entry=root.join(manifest["roles"][role].as_str().ok_or(err("ROLE_MISMATCH"))?);
        let files=inventory.keys().map(|p|norm(&root.join(p))).collect();
        Ok(Self{root,entry,files,_pins:pins})
    }
    pub fn root(&self)->&Path{&self.root}
    pub fn entry(&self)->&Path{&self.entry}
    pub fn files(&self)->usize{self.files.len()}
}

fn pipe(parent_reads:bool)->Result<(Handle,Handle)>{unsafe{
    let name=wide(format!("\\\\.\\pipe\\LocalAgent-review040-{}",uuid::Uuid::new_v4()));
    let direction=if parent_reads{PIPE_ACCESS_INBOUND}else{PIPE_ACCESS_OUTBOUND};
    let server=Handle::take(CreateNamedPipeW(name.as_ptr(),direction|FILE_FLAG_OVERLAPPED|FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE|PIPE_READMODE_BYTE|PIPE_WAIT|PIPE_REJECT_REMOTE_CLIENTS,1,4096,4096,0,null()))?;
    let sa=SECURITY_ATTRIBUTES{nLength:size_of::<SECURITY_ATTRIBUTES>() as u32,lpSecurityDescriptor:null_mut(),bInheritHandle:1};
    let client=Handle::take(CreateFileW(name.as_ptr(),if parent_reads{GENERIC_WRITE}else{GENERIC_READ},0,&sa,
        OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,null_mut()))?;
    if ConnectNamedPipe(server.0,null_mut())==0 && GetLastError()!=ERROR_PIPE_CONNECTED{return Err(err("PIPE_CONNECT_FAILED"));}
    Ok((server,client))
}}
fn quote(s:&str)->String{
    let mut out=String::from("\"");let mut slash=0;
    for c in s.chars(){if c=='\\'{slash+=1;}else{if c=='"'{out.push_str(&"\\".repeat(slash*2+1));}else{out.push_str(&"\\".repeat(slash));}out.push(c);slash=0;}}
    out.push_str(&"\\".repeat(slash*2));out.push('"');out
}
fn environment(extra:&[(String,String)])->Result<Vec<u16>>{
    let mut values=BTreeMap::new();
    for k in ["SystemRoot","SystemDrive","WINDIR","TEMP","TMP"]{if let Some(v)=std::env::var_os(k){values.insert(k.to_uppercase(),v);}}
    values.insert("PATH".into(),"".into());values.insert("PYTHONNOUSERSITE".into(),"1".into());
    for (k,v) in extra{
        if !k.starts_with("REVIEW040_") || k.contains(['=','\0']) || v.contains('\0'){return Err(err("PROTOCOL_INVALID"));}
        values.insert(k.clone(),v.into());
    }
    let mut block=Vec::new();
    for (k,v) in values{block.extend(wide(format!("{k}={}",v.to_string_lossy())));}
    block.push(0);Ok(block)
}

pub struct NativeProcess{
    job:Handle,process:Handle,thread:Option<Handle>,stdin:Handle,stdout:Handle,stderr:Handle,
    output:VecDeque<u8>,diagnostics:VecDeque<u8>,epoch:Instant,_bundle:Option<PinnedBundle>,pid:u32,
    cleanup_until:Option<Instant>,
}
impl NativeProcess{
    fn spawn(program:&Path,args:&[&str],cwd:&Path,suspended:bool,bundle:Option<PinnedBundle>,extra:&[(String,String)],epoch:Instant)->Result<Self>{unsafe{
        if !program.is_absolute() || args.iter().any(|s|s.contains('\0')){return Err(err("PROTOCOL_INVALID"));}
        let mut token=null_mut();
        if OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&mut token)==0{return Err(err("CONTAINMENT_FAILED"));}
        let token=Handle::take(token)?;let mut elevation:TOKEN_ELEVATION=zeroed();let mut returned=0;
        if GetTokenInformation(token.0,TokenElevation,&mut elevation as *mut _ as _,size_of_val(&elevation) as u32,&mut returned)==0
            || elevation.TokenIsElevated!=0{return Err(err("ELEVATION_REFUSED"));}
        if let Some(ref b)=bundle{b.verify_membership()?;}
        let job=Handle::take(CreateJobObjectW(null(),null()))?;
        let mut limits:JOBOBJECT_EXTENDED_LIMIT_INFORMATION=zeroed();limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if args==["--agent-stdio"]{
            limits.BasicLimitInformation.LimitFlags|=JOB_OBJECT_LIMIT_ACTIVE_PROCESS|JOB_OBJECT_LIMIT_JOB_MEMORY|JOB_OBJECT_LIMIT_PROCESS_MEMORY|JOB_OBJECT_LIMIT_JOB_TIME;
            limits.BasicLimitInformation.ActiveProcessLimit=4;
            limits.BasicLimitInformation.PerJobUserTimeLimit=30*10_000_000;
            limits.JobMemoryLimit=512*1024*1024;limits.ProcessMemoryLimit=256*1024*1024;
        }
        if SetInformationJobObject(job.0,JobObjectExtendedLimitInformation,&limits as *const _ as _,size_of_val(&limits) as u32)==0{return Err(err("CONTAINMENT_FAILED"));}
        let (stdin,child_in)=pipe(false)?;let (stdout,child_out)=pipe(true)?;let (stderr,child_err)=pipe(true)?;
        let mut bytes=0;InitializeProcThreadAttributeList(null_mut(),2,0,&mut bytes);
        if bytes==0{return Err(err("CONTAINMENT_FAILED"));}
        let mut storage=vec![0usize;(bytes+size_of::<usize>()-1)/size_of::<usize>()];
        let list=storage.as_mut_ptr() as LPPROC_THREAD_ATTRIBUTE_LIST;
        if InitializeProcThreadAttributeList(list,2,0,&mut bytes)==0{return Err(err("CONTAINMENT_FAILED"));}
        struct Attributes(LPPROC_THREAD_ATTRIBUTE_LIST);impl Drop for Attributes{fn drop(&mut self){unsafe{DeleteProcThreadAttributeList(self.0);}}}
        let _attributes=Attributes(list);
        let mut jobs=[job.0];let mut inherited=[child_in.0,child_out.0,child_err.0];
        if UpdateProcThreadAttribute(list,0,PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,jobs.as_mut_ptr() as _,size_of_val(&jobs),null_mut(),null())==0
            || UpdateProcThreadAttribute(list,0,PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,inherited.as_mut_ptr() as _,size_of_val(&inherited),null_mut(),null())==0{return Err(err("CONTAINMENT_FAILED"));}
        let mut info:STARTUPINFOEXW=zeroed();info.StartupInfo.cb=size_of_val(&info) as u32;
        info.StartupInfo.dwFlags=STARTF_USESTDHANDLES;info.StartupInfo.hStdInput=child_in.0;info.StartupInfo.hStdOutput=child_out.0;info.StartupInfo.hStdError=child_err.0;info.lpAttributeList=list;
        let app=wide(program);let mut command=wide(std::iter::once(quote(&program.to_string_lossy())).chain(args.iter().map(|a|quote(a))).collect::<Vec<_>>().join(" "));
        let env=environment(extra)?;let dir=wide(cwd);let mut pi:PROCESS_INFORMATION=zeroed();
        if CreateProcessW(app.as_ptr(),command.as_mut_ptr(),null(),null(),1,
            CREATE_SUSPENDED|CREATE_NO_WINDOW|CREATE_UNICODE_ENVIRONMENT|EXTENDED_STARTUPINFO_PRESENT,
            env.as_ptr() as _,dir.as_ptr(),&info.StartupInfo,&mut pi)==0{
            eprintln!("review040 CreateProcess error={}",GetLastError());return Err(err("CONTAINMENT_FAILED"));
        }
        let mut process=Self{job,process:Handle::take(pi.hProcess)?,thread:Some(Handle::take(pi.hThread)?),
            stdin,stdout,stderr,output:VecDeque::new(),diagnostics:VecDeque::new(),epoch,_bundle:bundle,pid:pi.dwProcessId,cleanup_until:None};
        if !process.in_job()?{return Err(err("CONTAINMENT_FAILED"));}
        if !suspended{process.resume()?;}Ok(process)
    }}
    pub fn launch(bundle:PinnedBundle,epoch:Instant)->Result<Self>{
        let program=bundle.entry.clone();let cwd=bundle.root.clone();Self::spawn(&program,&["--management-stdio"],&cwd,false,Some(bundle),&[],epoch)
    }
    pub fn launch_agent(bundle:PinnedBundle,epoch:Instant)->Result<Self>{
        let program=bundle.entry.clone();let cwd=bundle.root.clone();Self::spawn(&program,&["--agent-stdio"],&cwd,false,Some(bundle),&[],epoch)
    }
    pub fn cancellation(&self)->Result<JobCancel>{unsafe{
        let mut owned=null_mut();
        if DuplicateHandle(GetCurrentProcess(),self.job.0,GetCurrentProcess(),&mut owned,0,0,DUPLICATE_SAME_ACCESS)==0{return Err(err("NATIVE_HANDLE_FAILED"));}
        Ok(JobCancel(Handle::take(owned)?))
    }}
    #[cfg(test)]
    pub fn fixture(program:&Path,args:&[&str],suspended:bool)->Result<Self>{
        Self::spawn(program,args,program.parent().ok_or(err("RUNTIME_MISSING"))?,suspended,None,&[],Instant::now())
    }
    #[cfg(test)]
    pub fn fixture_env(program:&Path,args:&[&str],extra:&[(String,String)])->Result<Self>{
        Self::spawn(program,args,program.parent().ok_or(err("RUNTIME_MISSING"))?,false,None,extra,Instant::now())
    }
    pub fn pid(&self)->u32{self.pid}
    pub fn resume(&mut self)->Result<()>{unsafe{
        let thread=self.thread.take().ok_or(err("PROTOCOL_INVALID"))?;
        if ResumeThread(thread.0)==u32::MAX{return Err(err("CONTAINMENT_FAILED"));}Ok(())
    }}
    pub fn in_job(&self)->Result<bool>{unsafe{let mut yes=0;if IsProcessInJob(self.process.0,self.job.0,&mut yes)==0{return Err(err("CONTAINMENT_FAILED"));}Ok(yes!=0)}}
    pub fn contains(&self,p:&ObservedProcess)->Result<bool>{unsafe{let mut yes=0;if IsProcessInJob(p.0.0,self.job.0,&mut yes)==0{return Err(err("CONTAINMENT_FAILED"));}Ok(yes!=0)}}
    pub fn exited(&self)->bool{unsafe{WaitForSingleObject(self.process.0,0)==WAIT_OBJECT_0}}
    pub fn active_count(&self)->Result<u32>{unsafe{
        let mut info:JOBOBJECT_BASIC_ACCOUNTING_INFORMATION=zeroed();
        if QueryInformationJobObject(self.job.0,JobObjectBasicAccountingInformation,&mut info as *mut _ as _,size_of_val(&info) as u32,null_mut())==0{return Err(err("CONTAINMENT_FAILED"));}Ok(info.ActiveProcesses)
    }}
    pub fn terminate(&mut self,timeout:Duration)->Result<()>{unsafe{
        let proposed=Instant::now()+timeout;
        let until=self.cleanup_until.map_or(proposed,|earlier|earlier.min(proposed));
        self.cleanup_until=Some(until);
        if TerminateJobObject(self.job.0,40)==0{return Err(err("CONTAINMENT_FAILED"));}
        loop{if self.exited() && self.active_count()?==0{return Ok(());}if Instant::now()>=until{return Err(err("SHUTDOWN_TIMEOUT"));}std::thread::sleep(Duration::from_millis(5));}
    }}
    fn io(&mut self,handle:HANDLE,data:&mut [u8],write:bool,until:Instant,drain:bool)->Result<usize>{unsafe{
        if Instant::now()>=until{self.terminate(Duration::from_secs(2))?;return Err(err("IO_TIMEOUT"));}
        let event=Handle::take(CreateEventW(null(),1,0,null()))?;
        let mut ov=Box::new(zeroed::<OVERLAPPED>());ov.hEvent=event.0;let mut count=0;
        let started=if write{WriteFile(handle,data.as_ptr(),data.len() as u32,&mut count,&mut *ov)}else{ReadFile(handle,data.as_mut_ptr(),data.len() as u32,&mut count,&mut *ov)};
        if started==0 && GetLastError()!=ERROR_IO_PENDING{return Err(err("PEER_EXITED"));}
        if started==0{
            loop{
                if WaitForSingleObject(event.0,0)==WAIT_OBJECT_0{break;}
                if drain{if let Err(e)=self.drain_stderr(until){
                    CancelIoEx(handle,&*ov);let _=self.terminate(Duration::from_secs(2));
                    let remaining=self.cleanup_until.unwrap_or(until).saturating_duration_since(Instant::now()).as_millis().min(2000) as u32;
                    if WaitForSingleObject(event.0,remaining)!=WAIT_OBJECT_0{std::process::abort();}
                    let _=GetOverlappedResult(handle,&*ov,&mut count,0);return Err(e);
                }}
                if Instant::now()>=until{
                    // Cancel is only a request: completion is mandatory before either
                    // the OVERLAPPED or its backing buffer may be released.
                    CancelIoEx(handle,&*ov);let killed=self.terminate(Duration::from_secs(2));
                    let remaining=self.cleanup_until.unwrap_or(until).saturating_duration_since(Instant::now()).as_millis().min(2000) as u32;
                    if WaitForSingleObject(event.0,remaining)!=WAIT_OBJECT_0{std::process::abort();}
                    let _=GetOverlappedResult(handle,&*ov,&mut count,0);killed?;return Err(err("IO_TIMEOUT"));
                }
                WaitForSingleObject(event.0,10);
            }
        }
        if GetOverlappedResult(handle,&*ov,&mut count,0)==0{return Err(err("PEER_EXITED"));}
        Ok(count as usize)
    }}
    fn drain_stderr(&mut self,until:Instant)->Result<()>{unsafe{
        for _ in 0..16{
            let mut available=0;
            if PeekNamedPipe(self.stderr.0,null_mut(),0,null_mut(),&mut available,null_mut())==0 || available==0{return Ok(());}
            let mut bytes=vec![0;available.min(4096) as usize];
            let n=self.io(self.stderr.0,&mut bytes,false,until,false)?;
            if n==0{return Ok(());}self.diagnostics.extend(&bytes[..n]);
            while self.diagnostics.len()>65536{self.diagnostics.pop_front();}
        }Ok(())
    }}
    pub fn diagnostic_bytes(&self)->usize{self.diagnostics.len()}
    pub fn write_all(&mut self,bytes:&[u8],until:Instant)->Result<()>{
        let result=(||{for chunk in bytes.chunks(65536){let mut data=chunk.to_vec();let mut at=0;while at<data.len(){let n=self.io(self.stdin.0,&mut data[at..],true,until,true)?;if n==0{return Err(err("PEER_EXITED"));}at+=n;}}Ok(())})();
        if result.is_err(){let _=self.terminate(Duration::from_secs(2));}result
    }
    pub fn read_frame(&mut self,until:Instant)->Result<Vec<u8>>{
        let result=(||{loop{
            if let Some(end)=self.output.iter().position(|b|*b==b'\n'){
                if end+1>65536{return Err(err("PROTOCOL_INVALID"));}return Ok(self.output.drain(..=end).collect());
            }
            if self.output.len()>=65536{return Err(err("PROTOCOL_INVALID"));}
            let mut data=vec![0;4096.min(65536-self.output.len())];let n=self.io(self.stdout.0,&mut data,false,until,true)?;
            if n==0{return Err(err("PEER_EXITED"));}self.output.extend(&data[..n]);
        }})();
        if result.is_err(){let _=self.terminate(Duration::from_secs(2));}result
    }
    pub fn wait_natural_exit(&mut self,until:Instant)->Result<()>{
        while Instant::now()<until{self.drain_stderr(until)?;if self.exited() && self.active_count()?==0{return Ok(());}std::thread::sleep(Duration::from_millis(5));}
        Err(err("SHUTDOWN_TIMEOUT"))
    }
    fn wait_exit_with_grace(&mut self,grace:Instant,until:Instant)->Result<()>{
        if self.wait_natural_exit(grace.min(until)).is_ok(){return Ok(());}
        self.cleanup_until=Some(self.cleanup_until.map_or(until,|earlier|earlier.min(until)));
        self.terminate(until.saturating_duration_since(Instant::now()))
    }
    pub fn wait_exit(&mut self,until:Instant)->Result<()>{
        let grace=until.checked_sub(Duration::from_secs(2)).unwrap_or_else(Instant::now).max(Instant::now());
        self.wait_exit_with_grace(grace,until)
    }
    pub fn verify_loaded_modules(&self)->Result<Vec<String>>{unsafe{
        let bundle=self._bundle.as_ref().ok_or(err("RUNTIME_MISSING"))?;
        bundle.verify_membership()?;
        let mut image=vec![0u16;32768];let mut length=image.len() as u32;
        if QueryFullProcessImageNameW(self.process.0,0,image.as_mut_ptr(),&mut length)==0{return Err(err("ROLE_MISMATCH"));}
        if norm(Path::new(&String::from_utf16_lossy(&image[..length as usize])))!=norm(&bundle.entry){return Err(err("ROLE_MISMATCH"));}
        let mut handles=vec![null_mut();1024];let mut needed=0;
        if K32EnumProcessModules(self.process.0,handles.as_mut_ptr(),(handles.len()*size_of::<HMODULE>()) as u32,&mut needed)==0
            || needed as usize>handles.len()*size_of::<HMODULE>(){return Err(err("INTEGRITY_MISMATCH"));}
        let system=PathBuf::from(std::env::var_os("SystemRoot").ok_or(err("RUNTIME_MISSING"))?);
        let system32=norm(&system.join("System32"))+"\\";let winsxs=norm(&system.join("WinSxS"))+"\\";
        let mut result=Vec::new();
        for module in handles.into_iter().take(needed as usize/size_of::<HMODULE>()){
            let mut name=vec![0u16;32768];let n=K32GetModuleFileNameExW(self.process.0,module,name.as_mut_ptr(),name.len() as u32);
            if n==0 || n as usize>=name.len(){return Err(err("INTEGRITY_MISMATCH"));}
            let path=norm(Path::new(&String::from_utf16_lossy(&name[..n as usize])));
            if !bundle.files.contains(&path) && !path.starts_with(&system32) && !path.starts_with(&winsxs){return Err(err("INTEGRITY_MISMATCH"));}
            result.push(path);
        }Ok(result)
    }}
    #[cfg(test)]
    pub fn kill_primary_only(&self)->Result<()>{unsafe{if TerminateProcess(self.process.0,41)==0{return Err(err("PEER_EXITED"));}Ok(())}}
}
impl Drop for NativeProcess{fn drop(&mut self){let _=self.terminate(Duration::from_secs(2));}}
impl Transport for NativeProcess{
    fn write(&mut self,bytes:&[u8],absolute_deadline:u64)->Result<u64>{
        let until=self.epoch+Duration::from_millis(absolute_deadline);self.write_all(bytes,until)?;
        Ok(self.epoch.elapsed().as_millis() as u64)
    }
}

pub struct NativeSession{pub process:NativeProcess,supervisor:Supervisor,epoch:Instant,_guard:Option<Arc<CompatGuard>>}
impl NativeSession{
    #[cfg(test)]
    pub fn fixture_session(program:&Path,args:&[&str])->Result<Self>{
        let epoch=Instant::now();let mut supervisor=Supervisor::new(Binding::new("review040-management-v1","0.36.0")?,&uuid::Uuid::new_v4().to_string(),Limits::default())?;
        supervisor.begin(0)?;supervisor.verified(0)?;
        let process=NativeProcess::spawn(program,args,program.parent().ok_or(err("RUNTIME_MISSING"))?,false,None,&[],epoch)?;
        Ok(Self{process,supervisor,epoch,_guard:None})
    }
    pub fn start(root:&Path,digest:&str)->Result<Self>{
        Self::start_with_guard(root,digest,Arc::new(CompatGuard::acquire()?))
    }
    pub fn start_with_guard(root:&Path,digest:&str,guard:Arc<CompatGuard>)->Result<Self>{
        let epoch=Instant::now();let mut supervisor=Supervisor::new(Binding::new(env!("PROTECTED_RUNTIME_BUNDLE_ID"),env!("PROTECTED_RUNTIME_ARTIFACT_VERSION"))?,&uuid::Uuid::new_v4().to_string(),Limits::default())?;
        supervisor.begin(0)?;let bundle=PinnedBundle::open(root,digest,epoch+Duration::from_secs(10))?;
        supervisor.verified(epoch.elapsed().as_millis() as u64)?;
        let process=NativeProcess::launch(bundle,epoch)?;let mut this=Self{process,supervisor,epoch,_guard:Some(guard)};
        this.call("health")?;this.verify_loaded_modules()?;Ok(this)
    }
    pub fn maintain(&mut self)->Result<bool>{
        if self.process.exited(){return Err(err("PEER_EXITED"));}
        let now=self.epoch.elapsed().as_millis() as u64;
        if self.supervisor.health_due(now){self.call("health")?;self.verify_loaded_modules()?;return Ok(true);}
        self.supervisor.tick(now)?;Ok(false)
    }
    pub fn call(&mut self,method:&str)->Result<serde_json::Value>{
        let result=(||{
            let now=self.epoch.elapsed().as_millis() as u64;let id=uuid::Uuid::new_v4().to_string();
            if method=="shutdown"{self.supervisor.request_shutdown(&id,now)?;}else{self.supervisor.enqueue(method,&id,now)?;}
            let end=self.supervisor.next_deadline().ok_or(err("PROTOCOL_INVALID"))?;
            self.supervisor.send_next(&mut self.process,now)?;
            loop{let frame=self.process.read_frame(self.epoch+Duration::from_millis(end))?;
                self.supervisor.receive(&frame,self.epoch.elapsed().as_millis() as u64)?;
                if self.supervisor.outstanding()==0{return serde_json::from_slice::<serde_json::Value>(&frame).map(|v|v["result"].clone()).map_err(|_|err("PROTOCOL_INVALID"));}
            }
        })();
        if result.is_err(){let _=self.process.terminate(Duration::from_secs(2));}result
    }
    pub fn verify_loaded_modules(&mut self)->Result<Vec<String>>{
        match self.process.verify_loaded_modules(){Ok(v)=>Ok(v),Err(e)=>{let _=self.process.terminate(Duration::from_secs(2));Err(e)}}
    }
    pub fn shutdown(&mut self)->Result<()>{
        let start=Instant::now();let until=start+Duration::from_secs(5);
        self.process.cleanup_until=Some(until);
        let ack=self.call("shutdown");
        let stopped=self.process.wait_exit_with_grace(start+Duration::from_secs(2),until);
        if ack.is_ok() && stopped.is_ok(){self.supervisor.confirm_exit(self.epoch.elapsed().as_millis() as u64)?;}
        stopped?;ack.map(|_|())
    }
}
