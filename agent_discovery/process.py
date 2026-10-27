"""Private Windows process ownership; derived from audited suspended-launch primitive.
No authorization, Gate, Broker or tool execution code is imported.
"""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import threading

class BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('process_limit',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
class IoCounters(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint64) for name in ('read_operations','write_operations','other_operations','read_bytes','write_bytes','other_bytes')]
class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',IoCounters),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]

def api():
    if os.name!='nt':raise RuntimeError('windows_required')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[wintypes.LPVOID,wintypes.LPCWSTR];k.CreateJobObjectW.restype=wintypes.HANDLE
    k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];k.CreateFileW.restype=wintypes.HANDLE
    k.GetFinalPathNameByHandleW.argtypes=[wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD]
    return k

class PinnedExecutable:
    """Deny writes/deletes until CreateProcess and Job assignment have completed."""
    def __init__(self,path):self.path=Path(path);self.handle=None;self.k=api()
    def __enter__(self):
        self.handle=self.k.CreateFileW(str(self.path),0x80000000,1,None,3,0,None)
        if self.handle==ctypes.c_void_p(-1).value:raise RuntimeError('executable_unavailable')
        final=ctypes.create_unicode_buffer(32768)
        length=self.k.GetFinalPathNameByHandleW(self.handle,final,len(final),0)
        if not length or length>=len(final) or Path(final.value.removeprefix('\\\\?\\')).resolve()!=self.path.resolve():
            self.__exit__();raise RuntimeError('executable_identity_invalid')
        with self.path.open('rb') as stream:self.digest=hashlib.file_digest(stream,'sha256').hexdigest()
        return self
    def __exit__(self,*_):
        if self.handle and self.handle!=ctypes.c_void_p(-1).value:self.k.CloseHandle(self.handle);self.handle=None

class OwnedProcess:
    def __init__(self,executable,args,expected_hash,cwd):
        self.lock=threading.RLock()
        self.k=api();self.job=self.k.CreateJobObjectW(None,None);self.process=None
        if not self.job:raise RuntimeError('containment_failed')
        limits=ExtendedLimits();limits.basic.flags=0x2000
        try:
            if not self.k.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)):raise RuntimeError('containment_failed')
            with PinnedExecutable(executable) as pinned:
                if pinned.digest!=expected_hash:raise RuntimeError('executable_changed')
                env={key:value for key,value in os.environ.items() if key.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PATH'}}
                self.process=subprocess.Popen([executable,*args],cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=0x4|subprocess.CREATE_NO_WINDOW)
                if not self.k.AssignProcessToJobObject(self.job,int(self.process._handle)):raise RuntimeError('containment_failed')
                native=ctypes.WinDLL('ntdll');native.NtResumeProcess.argtypes=[wintypes.HANDLE];native.NtResumeProcess.restype=wintypes.LONG
                if native.NtResumeProcess(int(self.process._handle))<0:raise RuntimeError('resume_failed')
        except BaseException:
            self.close();raise
    def close(self):
        with self.lock:self._close()
    def _close(self):
        p=self.process
        if p:
            try:
                if p.stdin:p.stdin.close()
                p.wait(timeout=.3)
            except (OSError,subprocess.TimeoutExpired):pass
        if self.job:self.k.CloseHandle(self.job);self.job=None
        if p:
            if p.poll() is None:p.kill()
            p.wait(timeout=3)
            for stream in (p.stdout,p.stderr):
                if stream:
                    try:stream.close()
                    except OSError:pass
