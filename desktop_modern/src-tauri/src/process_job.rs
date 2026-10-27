use std::process::Child;
#[cfg(windows)]
use std::os::windows::io::AsRawHandle;
#[cfg(windows)]
use windows_sys::Win32::{Foundation::{HANDLE, CloseHandle, GetLastError, ERROR_ALREADY_EXISTS}, System::{JobObjects::{CreateJobObjectW, SetInformationJobObject, AssignProcessToJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE}, Threading::CreateMutexW}};

pub struct OwnedJob {
    #[cfg(windows)]
    handle: HANDLE,
}
// Owning kernel handles may be closed from any thread; no borrowed handles escape.
unsafe impl Send for OwnedJob {}
unsafe impl Sync for OwnedJob {}
impl OwnedJob {
    pub fn contain(child: &mut Child) -> Result<Self,String> {
        #[cfg(windows)]
        unsafe {
            #[link(name="ntdll")]
            extern "system" { fn NtResumeProcess(process: HANDLE) -> i32; }
            let handle=CreateJobObjectW(std::ptr::null(),std::ptr::null());
            if handle.is_null() { let _=child.kill();let _=child.wait();return Err("runtime_job_failed".into()); }
            let guard=Self{handle};
            let mut limits:JOBOBJECT_EXTENDED_LIMIT_INFORMATION=std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle,JobObjectExtendedLimitInformation,&limits as *const _ as _,std::mem::size_of_val(&limits) as u32)==0 || AssignProcessToJobObject(handle,child.as_raw_handle() as HANDLE)==0 || NtResumeProcess(child.as_raw_handle() as HANDLE)<0 {
                let _=child.kill();let _=child.wait();return Err("runtime_containment_failed".into());
            }
            Ok(guard)
        }
        #[cfg(not(windows))]
        { let _=child;Ok(Self{}) }
    }
}
impl Drop for OwnedJob { fn drop(&mut self) {
    #[cfg(windows)] unsafe { CloseHandle(self.handle); }
}}

pub struct SingleInstance {
    #[cfg(windows)]
    handle: HANDLE,
}
unsafe impl Send for SingleInstance {}
unsafe impl Sync for SingleInstance {}
impl SingleInstance {
    pub fn acquire() -> Result<Self,String> {
        #[cfg(windows)] unsafe {
            let name:Vec<u16>="Local\\LocalAgentDesktop036".encode_utf16().chain(Some(0)).collect();
            let handle=CreateMutexW(std::ptr::null(),0,name.as_ptr());
            if handle.is_null(){return Err("runtime_instance_lock_failed".into());}
            if GetLastError()==ERROR_ALREADY_EXISTS{CloseHandle(handle);return Err("app_already_running".into());}
            Ok(Self{handle})
        }
        #[cfg(not(windows))] {Ok(Self{})}
    }
}
impl Drop for SingleInstance {fn drop(&mut self){#[cfg(windows)] unsafe {CloseHandle(self.handle);}}}
