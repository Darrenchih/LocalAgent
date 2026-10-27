"""Approval-bound broker. Native parent owns the process job and session secret."""
import ctypes
from ctypes import wintypes as W
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from .management import ProtocolError, parse_frame, _uuid, _strict_json, Limits
from .agent_tools import TOOLS, validate_arguments, path_parts, grant_canonical
from . import powershell_templates

IDENTITY = dict(schema=1, contract_version=1, bundle_id='localagent-agent-test-v1', artifact_version='0.37.0', execution_mode='approval_required')
HEX = re.compile(r'[0-9a-f]{64}\Z')

def check(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ProtocolError('INVALID_ARGUMENT')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')

def parts(path):
    return path_parts(path)

def json_equal(actual, expected):
    # JSON booleans are not numbers, even though Python bool subclasses int.
    if type(actual) in (int,float) and type(expected) in (int,float): return actual==expected
    if type(actual) is not type(expected): return False
    if type(expected) is dict: return set(actual)==set(expected) and all(json_equal(actual[k],v) for k,v in expected.items())
    if type(expected) is list: return len(actual)==len(expected) and all(json_equal(a,b) for a,b in zip(actual,expected))
    return actual==expected

class WindowsFiles:
    def __init__(self, root):
        if os.name != 'nt':
            raise ProtocolError('WINDOWS_REQUIRED')
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k.CreateFileW.argtypes = [W.LPCWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
        self.k.CreateFileW.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CreateDirectoryW.argtypes = [W.LPCWSTR,ctypes.c_void_p]
        self.k.CreateDirectoryW.restype = W.BOOL
        self.k.GetFileInformationByHandle.argtypes = [W.HANDLE,ctypes.c_void_p]
        self.root = root
        self.pins = []
        try:
            drive, tail = os.path.splitdrive(root)
            if not re.fullmatch('[A-Za-z]:', drive) or not tail.startswith('\\'):
                raise ProtocolError('PATH_DENIED')
            current = drive + '\\'
            self.pins.append(self.open(current, True))
            for p in parts(tail[1:]):
                self.exact(current, p)
                current = os.path.join(current, p)
                self.pins.append(self.open(current, True))
        except Exception:
            self.close()
            raise
    def close(self):
        for h in reversed(self.pins): self.k.CloseHandle(h)
        self.pins = []
    def exact(self, parent, name, absent=False):
        names = [p for p in os.listdir(parent) if p.casefold() == name.casefold()]
        if names != [name] and not (absent and not names):
            raise ProtocolError('PATH_ALIAS')
    def open(self, path, directory=False, create=False, write=False):
        h = self.k.CreateFileW(path, 0x80000000 | (0x40000000 if write else 0), 3 if directory else 0, None, 1 if create else 3, 0x00200000 | (0x02000000 if directory else 0), None)
        if h == ctypes.c_void_p(-1).value:
            raise ProtocolError('FILE_OPEN_DENIED')
        # BY_HANDLE_FILE_INFORMATION contains attributes followed by timestamps,
        # volume serial, size, link count, and file index.
        data = (W.DWORD * 13)()
        if not self.k.GetFileInformationByHandle(h, data) or data[0] & 0x400 or (not directory and (data[0] & 0x10 or data[10] != 1)):
            self.k.CloseHandle(h)
            raise ProtocolError('UNSAFE_FILE')
        if directory and not data[0] & 0x10:
            self.k.CloseHandle(h)
            raise ProtocolError('UNSAFE_FILE')
        return h
    @contextmanager
    def file(self, path, create=False, write=False):
        import msvcrt
        values = parts(path)
        current, pins = self.root, []
        try:
            for p in values[:-1]:
                self.exact(current,p)
                current = os.path.join(current,p)
                pins.append(self.open(current,True))
            self.exact(current,values[-1],absent=create)
            h = self.open(os.path.join(current,values[-1]),create=create,write=write)
            fd = msvcrt.open_osfhandle(h, os.O_BINARY | (os.O_RDWR if write else os.O_RDONLY))
            with os.fdopen(fd, 'r+b' if write else 'rb') as f:
                yield f
        finally:
            for h in reversed(pins): self.k.CloseHandle(h)

    @contextmanager
    def directory(self, path):
        current, pins = self.root, []
        try:
            for name in path_parts(path, True):
                self.exact(current, name)
                current = os.path.join(current, name)
                pins.append(self.open(current, True))
            yield current
        finally:
            for handle in reversed(pins): self.k.CloseHandle(handle)

    def create_directory(self, path):
        names = parts(path)
        with self.directory('/'.join(names[:-1]) or '.') as parent:
            name=names[-1]
            self.exact(parent,name,absent=True)
            target=os.path.join(parent,name)
            created=bool(self.k.CreateDirectoryW(target,None))
            if not created and ctypes.get_last_error()!=183: raise ProtocolError('DIRECTORY_CREATE_DENIED')
            self.exact(parent,name)
            handle=self.open(target,True)
            self.k.CloseHandle(handle)
            return {'created':created}

    def list_directory(self, path):
        entries=[]
        with self.directory(path) as parent:
            with os.scandir(parent) as iterator:
                for entry in iterator:
                    if len(entries)>=128: return {'entries':entries,'truncated':True}
                    kind='blocked'
                    handle=None
                    try:
                        parts(entry.name)
                        self.exact(parent,entry.name)
                        # Attribute inspection determines only which safe handle to request;
                        # handle validation rejects reparses and multiply-linked files.
                        directory=bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x10)
                        handle=self.open(os.path.join(parent,entry.name),directory)
                        kind='directory' if directory else 'file'
                    except (ProtocolError,OSError): pass
                    finally:
                        if handle is not None: self.k.CloseHandle(handle)
                    entries.append({'name':entry.name,'type':kind})
        return {'entries':entries,'truncated':False}

    def search_text(self,path,query):
        matches=[]
        state={'files_scanned':0,'blocked':0,'directories':1,'bytes':0,'entries':0,'truncated':False}
        def walk(relative,depth):
            with self.directory(relative) as parent:
                with os.scandir(parent) as iterator:
                    for entry in iterator:
                        state['entries']+=1
                        if state['entries']>256:
                            state['truncated']=True; return
                        child=entry.name if relative=='.' else relative+'/'+entry.name
                        try:
                            parts(child)
                            self.exact(parent,entry.name)
                            directory=bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x10)
                            if directory:
                                # Validate even a directory excluded by traversal limits.
                                handle=self.open(os.path.join(parent,entry.name),True)
                                try:
                                    if depth>=8 or state['directories']>=64:
                                        state['truncated']=True; continue
                                    state['directories']+=1
                                    walk(child,depth+1)
                                finally: self.k.CloseHandle(handle)
                            else:
                                if state['files_scanned']>=128 or state['bytes']>=4194304:
                                    state['truncated']=True; return
                                with self.file(child) as stream:
                                    state['files_scanned']+=1
                                    if os.fstat(stream.fileno()).st_size>32768:
                                        state['blocked']+=1; continue
                                    remaining=4194304-state['bytes']
                                    data=stream.read(min(32768,remaining))
                                state['bytes']+=len(data)
                                try: text=data.decode('utf-8')
                                except UnicodeDecodeError:
                                    state['blocked']+=1; continue
                                for line_number,line in enumerate(text.splitlines(),1):
                                    offset=line.find(query)
                                    if offset>=0:
                                        if len(matches)>=100: state['truncated']=True; return
                                        start=max(0,offset-40)
                                        matches.append({'path':child,'line':line_number,'snippet':line[start:start+200]})
                        except (ProtocolError,OSError): state['blocked']+=1
                        if state['truncated'] and (len(matches)>=100 or state['files_scanned']>=128 or state['entries']>256): return
        walk(path,0)
        return {'matches':matches,'truncated':state['truncated'],'files_scanned':state['files_scanned'],'blocked':state['blocked']}

class Broker:
    def __init__(self):
        self.files = None
        self.nonces, self.proposals, self.ids = set(), set(), set()
    def handle(self, request):
        rid = request.get('id') if type(request) is dict else None
        try:
            check(request, ('v','id','method','params'))
            _uuid(rid)
            if type(request['v']) is not int or request['v'] != 1 or rid in self.ids or len(self.ids) >= 65536:
                raise ProtocolError('REQUEST_DENIED')
            self.ids.add(rid)
            method, p = request['method'], request['params']
            if method == 'hello':
                check(p, ())
                result = dict(IDENTITY, role='protected-agent-broker')
            elif method == 'init':
                if self.files is not None: raise ProtocolError('ALREADY_INITIALIZED')
                check(p, ('session_key','root','task_id'))
                if type(p['session_key']) is not str or not HEX.fullmatch(p['session_key']): raise ProtocolError('INVALID_KEY')
                _uuid(p['task_id'])
                if type(p['root']) is not str or not os.path.isabs(p['root']) or os.path.normpath(p['root']) != p['root']: raise ProtocolError('PATH_DENIED')
                files = WindowsFiles(p['root'])
                self.files, self.key, self.root, self.task = files, bytes.fromhex(p['session_key']), p['root'], p['task_id']
                result = {'initialized':True}
            elif method == 'catalog':
                check(p, ())
                result = {'tools':TOOLS,'powershell_templates':powershell_templates.catalog()}
            elif method == 'execute': result = self.execute(p)
            else: raise ProtocolError('METHOD_DENIED')
            return dict(v=1,id=rid,ok=True,result=result)
        except (ProtocolError, OSError, ValueError, TypeError, subprocess.SubprocessError) as e:
            return dict(v=1,id=rid,ok=False,error=dict(code=e.code if isinstance(e,ProtocolError) else 'EXECUTION_FAILED',message='Request denied or execution failed'))
    def execute(self,p):
        if self.files is None: raise ProtocolError('NOT_INITIALIZED')
        check(p, ('grant','permit'))
        g, permit = p['grant'], p['permit']
        check(g, ('task_id','proposal_id','root','tool','args','expires_ms'))
        check(permit, ('nonce','grant_sha256','mac'))
        _uuid(g['proposal_id']); _uuid(permit['nonce'])
        digest = hashlib.sha256(grant_canonical(g)).hexdigest()
        if (type(permit['mac']) is not str or not HEX.fullmatch(permit['mac']) or permit['grant_sha256'] != digest
            or not hmac.compare_digest(permit['mac'], hmac.new(self.key,(permit['nonce']+':'+digest).encode(),hashlib.sha256).hexdigest())
            or g['root'] != self.root or g['task_id'] != self.task
            or type(g['expires_ms']) is not int or not time.time()*1000 < g['expires_ms'] <= time.time()*1000+60000
            or permit['nonce'] in self.nonces or g['proposal_id'] in self.proposals or len(self.nonces)>=65536):
            raise ProtocolError('AUTHORIZATION_DENIED')
        self.nonces.add(permit['nonce']); self.proposals.add(g['proposal_id'])
        a = validate_arguments(g['tool'],g['args'])
        if canonical(a)!=canonical(g['args']): raise ProtocolError('NONCANONICAL_ARGUMENT')
        if g['tool']=='list_directory': return self.files.list_directory(a['path'])
        if g['tool']=='create_directory': return self.files.create_directory(a['path'])
        if g['tool']=='search_text': return self.files.search_text(a['path'],a['query'])
        if g['tool'] == 'write_file':
            check(a, ('path','content','expected_sha256'))
            if type(a['content']) is not str or len(a['content'].encode()) > 32768: raise ProtocolError('RESOURCE_LIMIT')
            expected = a['expected_sha256']
            if type(expected) is not str or (expected != 'absent' and not HEX.fullmatch(expected)): raise ProtocolError('INVALID_ARGUMENT')
            with self.files.file(a['path'], create=expected=='absent',write=True) as f:
                old = f.read(32769)
                if len(old)>32768 or (expected!='absent' and hashlib.sha256(old).hexdigest()!=expected): raise ProtocolError('PRECONDITION_FAILED')
                data = a['content'].encode('utf-8')
                f.seek(0); f.write(data); f.truncate(); f.flush(); os.fsync(f.fileno())
            return {'bytes_written':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        if g['tool'] == 'read_file': check(a, ('path',))
        elif g['tool'] == 'powershell':
            check(a, ('template','path','expected'))
            command = powershell_templates.command(a['template'])
        else: raise ProtocolError('TOOL_DENIED')
        with self.files.file(a['path']) as f: data=f.read(32769)
        if len(data)>32768: raise ProtocolError('RESOURCE_LIMIT')
        if g['tool']=='read_file': return {'content':data.decode('utf-8'),'sha256':hashlib.sha256(data).hexdigest()}
        try: value=_strict_json(data,byte_limit=32768,limits=Limits())
        except ProtocolError: raise ProtocolError('TEST_FAILED') from None
        # No model-controlled command text or paths enter PowerShell's command.
        completed = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-Command',command],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,creationflags=0x08000000)
        if len(completed.stdout)+len(completed.stderr)>65536 or completed.returncode!=0 or completed.stdout!=b'json-valid': raise ProtocolError('TEST_FAILED')
        if type(value) is not dict or any(k not in value or not json_equal(value[k],v) for k,v in a['expected'].items()): raise ProtocolError('TEST_FAILED')
        return {'passed':True,'template':'test_json'}

def serve(inp,out):
    broker=Broker()
    try:
        while True:
            raw=inp.readline(65537)
            if not raw: return 0
            try: response=broker.handle(parse_frame(raw))
            except ProtocolError: return 2
            encoded=canonical(response)+b'\n'
            if len(encoded)>65536:
                encoded=canonical(dict(v=1,id=response['id'],ok=False,error=dict(code='RESOURCE_LIMIT',message='Encoded response exceeds frame limit')))+b'\n'
            out.write(encoded); out.flush()
    finally:
        if broker.files: broker.files.close()

