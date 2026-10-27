"""Discovery-only MCP client; never exports a general RPC or tools/call API."""
from pathlib import Path
import json,math,os,queue,re,threading,time
from .process import OwnedProcess,PinnedExecutable

VERSION='0.37.0'
PROTOCOL='2025-11-25'
MAX_LINE=65536
class DiscoveryError(ValueError):pass

def strict_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise DiscoveryError('protocol_invalid')
            result[key]=value
        return result
    def finite_float(value):
        number=float(value)
        if not math.isfinite(number):raise DiscoveryError('protocol_invalid')
        return number
    try:
        value=json.loads(text,object_pairs_hook=pairs,parse_float=finite_float,parse_constant=lambda _:(_ for _ in ()).throw(DiscoveryError('protocol_invalid')))
        stack=[(value,0)];count=0
        while stack:
            item,depth=stack.pop();count+=1
            if depth>16 or count>8192:raise DiscoveryError('protocol_budget_exceeded')
            if isinstance(item,dict):stack.extend((v,depth+1) for v in item.values())
            elif isinstance(item,list):stack.extend((v,depth+1) for v in item)
        return value
    except (ValueError,TypeError,RecursionError):raise DiscoveryError('protocol_invalid') from None

def config(value,stored=False):
    required={'id','name','executable','args'}|({'sha256'} if stored else set())
    if not isinstance(value,dict) or set(value)!=required:raise DiscoveryError('config_invalid')
    if not isinstance(value['id'],str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,47}',value['id']):raise DiscoveryError('config_invalid')
    if not isinstance(value['name'],str) or not 1<=len(value['name'])<=100:raise DiscoveryError('config_invalid')
    path=value['executable']
    if not isinstance(path,str) or len(path)>2048 or '\x00' in path:raise DiscoveryError('config_invalid')
    p=Path(path)
    if not p.is_absolute() or path.startswith(('\\\\','//')) or (not stored and not p.is_file()) or p.suffix.lower()!='.exe':raise DiscoveryError('executable_invalid')
    p=p.resolve()
    if p.name.lower() in {'cmd.exe','powershell.exe','pwsh.exe','wscript.exe','cscript.exe','mshta.exe','rundll32.exe','regsvr32.exe','bash.exe','sh.exe'}:raise DiscoveryError('shell_disabled')
    args=value['args']
    if not isinstance(args,list) or len(args)>32 or any(not isinstance(a,str) or len(a)>2048 or '\x00' in a or '\n' in a or '\r' in a for a in args):raise DiscoveryError('config_invalid')
    if stored and (not isinstance(value['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['sha256'])):raise DiscoveryError('config_invalid')
    return {**value,'executable':str(p),'args':list(args)}

class Session:
    def __init__(self,cfg,cwd,timeout):
        self.owned=OwnedProcess(cfg['executable'],cfg['args'],cfg['sha256'],cwd)
        self.process=self.owned.process
        try:os.set_blocking(self.process.stdin.fileno(),False)
        except OSError:
            self.owned.close();raise DiscoveryError('nonblocking_pipe_unavailable') from None
        self.timeout=timeout;self.messages=queue.Queue(maxsize=32);self.counter=0;self.tools=[];self.output_bytes=0;self.stop=threading.Event()
        def offer(value):
            while not self.stop.is_set():
                try:self.messages.put(value,timeout=.1);return
                except queue.Full:pass
        def read():
            try:
                while not self.stop.is_set():
                    line=self.process.stdout.readline(MAX_LINE+1)
                    if not line:offer(DiscoveryError('server_disconnected'));return
                    if len(line)>MAX_LINE:offer(DiscoveryError('response_too_large'));return
                    offer(line)
            except (OSError,ValueError):pass
        def drain():
            try:
                while self.process.stderr.read(4096):pass
            except (OSError,ValueError):pass
        threading.Thread(target=read,daemon=True).start();threading.Thread(target=drain,daemon=True).start()
        def supervise():
            self.process.wait()
            self.owned.close()
        threading.Thread(target=supervise,daemon=True).start()
    def send(self,message,deadline=None):
        raw=(json.dumps(message,separators=(',',':'))+'\n').encode('utf-8')
        if len(raw)>MAX_LINE:raise DiscoveryError('request_too_large')
        deadline=min(deadline,time.monotonic()+self.timeout) if deadline is not None else time.monotonic()+self.timeout
        offset=0
        while offset<len(raw):
            if time.monotonic()>=deadline:raise DiscoveryError('server_write_timeout')
            try:
                written=os.write(self.process.stdin.fileno(),raw[offset:])
                if written:offset+=written
                else:time.sleep(.005)
            except BlockingIOError:time.sleep(.005)
            except (OSError,ValueError):raise DiscoveryError('server_disconnected') from None
    def rpc(self,method,params):
        if method not in {'initialize','tools/list'}:raise DiscoveryError('formal_execution_disabled_review036')
        self.counter+=1;ident=self.counter
        deadline=time.monotonic()+self.timeout
        self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params},deadline)
        for _ in range(32):
            if time.monotonic()>=deadline:raise DiscoveryError('server_timeout')
            try:raw=self.messages.get(timeout=max(.001,deadline-time.monotonic()))
            except queue.Empty:raise DiscoveryError('server_timeout') from None
            if isinstance(raw,Exception):raise raw
            self.output_bytes+=len(raw)
            if self.output_bytes>MAX_LINE*8:raise DiscoveryError('response_too_large')
            try:msg=strict_json(raw.decode('utf-8'))
            except UnicodeError:raise DiscoveryError('protocol_invalid') from None
            if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0':raise DiscoveryError('protocol_invalid')
            if 'method' in msg:
                if not isinstance(msg['method'],str):raise DiscoveryError('protocol_invalid')
                if 'id' in msg:
                    if type(msg['id']) not in (int,str) or (isinstance(msg['id'],str) and len(msg['id'])>128):raise DiscoveryError('protocol_invalid')
                    self.send({'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32601,'message':'Client requests disabled'}},deadline)
                continue
            if type(msg.get('id')) is not int or msg['id']!=ident:raise DiscoveryError('response_id_invalid')
            if ('result' in msg)==('error' in msg):raise DiscoveryError('protocol_invalid')
            if 'error' in msg:raise DiscoveryError('server_rpc_error')
            if not isinstance(msg['result'],dict):raise DiscoveryError('protocol_invalid')
            return msg['result']
        raise DiscoveryError('message_limit_exceeded')
    def discover(self):
        result=self.rpc('initialize',{'protocolVersion':PROTOCOL,'capabilities':{},'clientInfo':{'name':'LocalAgentDiscovery','version':VERSION}})
        if result.get('protocolVersion')!=PROTOCOL or not isinstance(result.get('serverInfo'),dict) or not isinstance(result.get('capabilities'),dict):raise DiscoveryError('protocol_version_invalid')
        if not isinstance(result['capabilities'].get('tools'),dict):raise DiscoveryError('tools_capability_missing')
        self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        cursor=None;seen=set()
        for _ in range(8):
            result=self.rpc('tools/list',{} if cursor is None else {'cursor':cursor})
            tools=result.get('tools')
            if not isinstance(tools,list) or len(tools)+len(self.tools)>256:raise DiscoveryError('tools_schema_invalid')
            for item in tools:
                if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not 1<=len(item['name'])<=128 or not isinstance(item.get('description',''),str) or not isinstance(item.get('inputSchema'),dict) or item['inputSchema'].get('type')!='object':raise DiscoveryError('tools_schema_invalid')
                self.tools.append({'name':item['name'],'description':item.get('description',''),'inputSchema':item['inputSchema']})
            cursor=result.get('nextCursor')
            if cursor is None:return
            if not isinstance(cursor,str) or not cursor or len(cursor)>2048 or cursor in seen:raise DiscoveryError('pagination_invalid')
            seen.add(cursor)
        raise DiscoveryError('pagination_limit_exceeded')
    def close(self):self.stop.set();self.owned.close()

class DiscoveryService:
    def __init__(self,root,timeout=3):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.path=self.root/'servers.json';self.configs={};self.sessions={};self.errors={};self.timeout=timeout;self.closed=False;self.lock=threading.RLock()
        if self.path.exists():
            try:
                if self.path.stat().st_size>262144:raise ValueError()
                data=strict_json(self.path.read_text(encoding='utf-8'))
                if not isinstance(data,dict) or set(data)!={'version','servers'} or (type(data['version']) is not int or data['version']!=1) or not isinstance(data['servers'],list) or len(data['servers'])>32:raise ValueError()
                for value in data['servers']:
                    value=config(value,stored=True)
                    if value['id'] in self.configs:raise ValueError()
                    self.configs[value['id']]=value
            except (ValueError,OSError):raise DiscoveryError('config_invalid') from None
    def health(self):return {'component':'agent-discovery','version':VERSION,'protocol':'1.0','status':'ok','execution_enabled':False,'formal_runtime':'not_deployed'}
    def save_server(self,value):
        with self.lock:
            if self.closed:raise DiscoveryError('runtime_stopped')
            cfg=config(value)
            if cfg['id'] not in self.configs and len(self.configs)>=32:raise DiscoveryError('config_limit_exceeded')
            with PinnedExecutable(cfg['executable']) as pinned:cfg['sha256']=pinned.digest
            candidate={**self.configs,cfg['id']:cfg}
            encoded=json.dumps({'version':1,'servers':list(candidate.values())},ensure_ascii=True).encode('utf-8')
            if len(encoded)>262144:raise DiscoveryError('config_limit_exceeded')
            temp=self.path.with_suffix('.tmp');temp.write_bytes(encoded);os.replace(temp,self.path)
            self.disconnect(cfg['id']);self.configs=candidate
            return self.status(cfg['id'])
    def status(self,ident):
        with self.lock:
            if ident not in self.configs:raise DiscoveryError('server_not_found')
            session=self.sessions.get(ident)
            if session and session.process.poll() is not None:
                self.disconnect(ident);self.errors[ident]='server_disconnected';session=None
            return {**self.configs[ident],'state':'connected' if session else 'error' if ident in self.errors else 'disconnected','error':self.errors.get(ident),'tools':session.tools if session else [],'pid':session.process.pid if session else None}
    def list_servers(self):
        with self.lock:return [self.status(key) for key in self.configs]
    def connect(self,ident):
        with self.lock:
            if self.closed:raise DiscoveryError('runtime_stopped')
            if ident not in self.configs:raise DiscoveryError('server_not_found')
            self.disconnect(ident);session=None
            try:
                cfg=config(self.configs[ident],stored=True);session=Session(cfg,self.root,self.timeout);session.discover();self.sessions[ident]=session
                return self.status(ident)
            except Exception as error:
                if session:session.close()
                code=str(error) if isinstance(error,DiscoveryError) or str(error) in {'executable_changed','containment_failed','resume_failed','executable_unavailable','executable_identity_invalid'} else 'connection_failed'
                self.errors[ident]=code;raise DiscoveryError(code) from None
    def disconnect(self,ident):
        with self.lock:
            session=self.sessions.pop(ident,None)
            if session:session.close()
            self.errors.pop(ident,None)
            return {'state':'disconnected'}
    def close(self):
        with self.lock:
            self.closed=True
            for key in list(self.sessions):self.disconnect(key)
    def dispatch(self,action,params):
        specs={'health':(set(),self.health),'list_servers':(set(),self.list_servers),'save_server':({'value'},lambda value:self.save_server(value)),'connect':({'id'},lambda id:self.connect(id)),'disconnect':({'id'},lambda id:self.disconnect(id)),'status':({'id'},lambda id:self.status(id))}
        if action not in specs:raise DiscoveryError('formal_execution_disabled_review036')
        keys,handler=specs[action]
        if not isinstance(params,dict) or set(params)!=keys:raise DiscoveryError('request_invalid')
        return handler(**params)
