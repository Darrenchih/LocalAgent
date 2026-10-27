import hashlib,hmac,io,json,os,subprocess,tempfile,time,unittest,uuid
from unittest.mock import patch
from pathlib import Path
from protected_runtime.agent_broker import Broker,WindowsFiles,canonical,parts,serve
from protected_runtime.management import parse_frame,ProtocolError
from protected_runtime.agent_tools import TOOLS,validate_arguments,grant_canonical
from protected_runtime import powershell_templates

def uid(): return str(uuid.uuid4())

@unittest.skipUnless(os.name=='nt','Windows handle tests')
class BrokerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files=WindowsFiles(r'D:\Agent\desktop-agent\work\agent-integration\workspace')
        try:
            files.create_directory('work')
            files.create_directory('work/review043')
        finally: files.close()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='AgentBrokerTest',dir=r'D:\Agent\desktop-agent\work\agent-integration\workspace\work\review043')
        self.root=str(Path(self.tmp.name).resolve())
        self.b=Broker(); self.task=uid(); self.key='ab'*32
        result=self.call('init',dict(session_key=self.key,root=self.root,task_id=self.task))
        self.assertTrue(result['ok'],result)
    def tearDown(self):
        if self.b.files:self.b.files.close()
        self.tmp.cleanup()
    def call(self,method,params): return self.b.handle(dict(v=1,id=uid(),method=method,params=params))
    def signed(self,tool,args):
        g=dict(task_id=self.task,proposal_id=uid(),root=self.root,tool=tool,args=args,expires_ms=int(time.time()*1000)+30000)
        digest=hashlib.sha256(grant_canonical(g)).hexdigest(); nonce=uid()
        return dict(grant=g,permit=dict(nonce=nonce,grant_sha256=digest,mac=hmac.new(bytes.fromhex(self.key),(nonce+':'+digest).encode(),hashlib.sha256).hexdigest()))
    def test_real_write_read_and_powershell(self):
        payload='{"status":"ok","n":1}'
        self.assertTrue(self.call('execute',self.signed('write_file',dict(path='result.json',content=payload,expected_sha256='absent')))['ok'])
        result=self.call('execute',self.signed('read_file',dict(path='result.json')))
        self.assertEqual(result['result']['content'],payload)
        self.assertTrue(self.call('execute',self.signed('powershell',dict(template='test_json',path='result.json',expected={'status':'ok'})))['ok'])
        self.assertFalse(self.call('execute',self.signed('powershell',dict(template='test_json',path='result.json',expected={'n':2})))['ok'])    def test_json_validation_does_not_confuse_boolean_number_or_duplicate_keys(self):        for index,(text,expected) in enumerate([('{"enabled":1}',{'enabled':True}),('{"nested":[1]}',{'nested':[True]}),('{"enabled":false,"enabled":true}',{'enabled':True}),('{"value":NaN}',{})]):            path=f'typed-{index}.json'            self.assertTrue(self.call('execute',self.signed('write_file',dict(path=path,content=text,expected_sha256='absent')))['ok'])            self.assertFalse(self.call('execute',self.signed('powershell',dict(template='test_json',path=path,expected=expected)))['ok'])
    def test_tamper_replay_and_precondition(self):
        p=self.signed('write_file',dict(path='a.txt',content='safe',expected_sha256='absent'))
        self.assertTrue(self.call('execute',p)['ok'])
        self.assertFalse(self.call('execute',p)['ok'])
        p=self.signed('write_file',dict(path='a.txt',content='bad',expected_sha256='0'*64))
        self.assertFalse(self.call('execute',p)['ok'])
        self.assertEqual((Path(self.root)/'a.txt').read_text(),'safe')
        p=self.signed('read_file',dict(path='a.txt'));p['grant']['args']['path']='other.txt'
        self.assertFalse(self.call('execute',p)['ok'])
    def test_hardlink_alias_and_ancestor_pin(self):
        a=Path(self.root)/'Safe.txt';a.write_text('safe')
        self.assertFalse(self.call('execute',self.signed('read_file',dict(path='safe.txt')))['ok'])
        os.link(a,Path(self.root)/'link.txt')
        self.assertFalse(self.call('execute',self.signed('read_file',dict(path='Safe.txt')))['ok'])
        with self.assertRaises(OSError):os.rename(self.root,self.root+'-moved')
    def test_denied_paths_and_raw_script(self):
        for p in ['../escape','a:stream','CON.txt','CON .txt','NUL .json','file.','file ','C:\\outside','\\\\server\\a','a//b','a~1.txt']:
            self.assertFalse(self.call('execute',self.signed('read_file',dict(path=p)))['ok'],p)
        self.assertFalse(self.call('execute',self.signed('powershell',dict(template='script',path='x',expected={})))['ok'])
    def test_failed_proposal_consumed_and_session(self):
        p=self.signed('read_file',dict(path='missing.txt'))
        self.assertFalse(self.call('execute',p)['ok'])
        (Path(self.root)/'missing.txt').write_text('now exists')
        self.assertFalse(self.call('execute',p)['ok'])
        self.assertFalse(self.call('init',dict(session_key=self.key,root=self.root,task_id=self.task))['ok'])
        self.assertFalse(Broker().handle(dict(v=1,id=uid(),method='execute',params=p))['ok'])
    def test_resource_limit(self):
        self.assertFalse(self.call('execute',self.signed('write_file',dict(path='big',content='x'*32769,expected_sha256='absent')))['ok'])
        (Path(self.root)/'big').write_bytes(b'x'*32769)
        self.assertFalse(self.call('execute',self.signed('read_file',dict(path='big')))['ok'])
    def test_expiry_scope_and_proposal_reuse(self):
        def resign(p):
            digest=hashlib.sha256(grant_canonical(p['grant'])).hexdigest()
            p['permit']['grant_sha256']=digest
            p['permit']['mac']=hmac.new(bytes.fromhex(self.key),(p['permit']['nonce']+':'+digest).encode(),hashlib.sha256).hexdigest()
        for change,value in [('expires_ms',0),('expires_ms',int(time.time()*1000)+120000),('root',self.root+'x'),('task_id',uid())]:
            p=self.signed('read_file',dict(path='missing'))
            p['grant'][change]=value;resign(p)
            self.assertFalse(self.call('execute',p)['ok'])
        p=self.signed('write_file',dict(path='once',content='first',expected_sha256='absent'))
        self.assertTrue(self.call('execute',p)['ok'])
        p['permit']['nonce']=uid();resign(p)
        self.assertFalse(self.call('execute',p)['ok'])
    def test_existing_write_hash_and_exclusive_file(self):
        target=Path(self.root)/'value';target.write_bytes(b'old')
        p=self.signed('write_file',dict(path='value',content='new',expected_sha256=hashlib.sha256(b'old').hexdigest()))
        self.assertTrue(self.call('execute',p)['ok'])
        self.assertEqual(target.read_bytes(),b'new')
        with self.b.files.file('value'):
            self.assertFalse(self.call('execute',self.signed('read_file',dict(path='value')))['ok'])
    def test_duplicate_request_id(self):
        request=dict(v=1,id=uid(),method='hello',params={})
        self.assertTrue(self.b.handle(request)['ok'])
        self.assertFalse(self.b.handle(request)['ok'])
    def test_junction_rejected_for_read_write_and_init(self):
        target=Path(self.root)/'target';target.mkdir();(target/'sentinel').write_text('untouched')
        junction=Path(self.root)/'junction'
        created=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(target)],capture_output=True)
        self.assertEqual(created.returncode,0,created.stderr)
        try:
            self.assertFalse(self.call('execute',self.signed('read_file',dict(path='junction/sentinel')))['ok'])
            self.assertFalse(self.call('execute',self.signed('write_file',dict(path='junction/new',content='bad',expected_sha256='absent')))['ok'])
            other=Broker()
            self.assertFalse(other.handle(dict(v=1,id=uid(),method='init',params=dict(session_key=self.key,root=str(junction),task_id=self.task)))['ok'])
            self.assertIsNone(other.files)
            self.assertEqual((target/'sentinel').read_text(),'untouched')
            self.assertFalse((target/'new').exists())
        finally:os.rmdir(junction)
    def test_descendant_pin_prevents_directory_swap(self):
        folder=Path(self.root)/'folder';folder.mkdir();(folder/'item').write_text('original')
        with self.b.files.file('folder/item'):
            with self.assertRaises(OSError):os.rename(folder,Path(self.root)/'moved')
            with self.assertRaises(OSError):os.unlink(folder/'item')
            self.assertFalse(self.call('execute',self.signed('write_file',dict(path='folder/item',content='bad',expected_sha256=hashlib.sha256(b'original').hexdigest())))['ok'])
        self.assertEqual((folder/'item').read_text(),'original')
    def test_powershell_output_limit_timeout_and_fault_consumes_permit(self):
        (Path(self.root)/'data.json').write_text('{}')
        faults=[subprocess.CompletedProcess([],0,b'x'*65537,b''),subprocess.CompletedProcess([],1,b'',b'failed'),subprocess.TimeoutExpired('trusted template',5),OSError('launch denied')]
        for fault in faults:
            p=self.signed('powershell',dict(template='test_json',path='data.json',expected={}))
            kwargs={'side_effect':fault} if isinstance(fault,Exception) else {'return_value':fault}
            with patch('protected_runtime.agent_broker.subprocess.run',**kwargs) as run:
                self.assertFalse(self.call('execute',p)['ok'])
                args,options=run.call_args
                self.assertEqual(args[0][0],r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
                self.assertEqual(options['timeout'],5)
                self.assertEqual(options['input'],b'{}')
            self.assertFalse(self.call('execute',p)['ok'])

    def test_directory_create_list_and_literal_search(self):
        result=self.call('execute',self.signed('create_directory',{'path':'notes'}))
        self.assertEqual(result['result'],{'created':True})
        self.assertEqual(self.call('execute',self.signed('create_directory',{'path':'notes'}))['result'],{'created':False})
        self.assertFalse(self.call('execute',self.signed('create_directory',{'path':'missing/child'}))['ok'])
        payload='Needle .* literal\nneedle .* literal\nNeedle last'
        self.assertTrue(self.call('execute',self.signed('write_file',{'path':'notes/a.txt','content':payload,'expected_sha256':'absent'}))['ok'])
        listing=self.call('execute',self.signed('list_directory',{'path':'.'}))['result']
        self.assertEqual(listing,{'entries':[{'name':'notes','type':'directory'}],'truncated':False})
        result=self.call('execute',self.signed('search_text',{'path':'.','query':'Needle .*'}))['result']
        self.assertEqual(result,{'matches':[{'path':'notes/a.txt','line':1,'snippet':'Needle .* literal'}],'truncated':False,'files_scanned':1,'blocked':0})
        result=self.call('execute',self.signed('search_text',{'path':'notes','query':'Needle'}))['result']
        self.assertEqual([x['line'] for x in result['matches']],[1,3])
        with self.b.files.directory('notes'):
            with self.assertRaises(OSError): os.rename(Path(self.root)/'notes',Path(self.root)/'moved')

    def test_directory_tools_block_hardlinks_junctions_aliases(self):
        root=Path(self.root)
        (root/'safe.txt').write_text('needle')
        os.link(root/'safe.txt',root/'hard.txt')
        (root/'target').mkdir(); (root/'target'/'sentinel').write_text('needle')
        junction=root/'junction'
        created=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(root/'target')],capture_output=True)
        self.assertEqual(created.returncode,0,created.stderr)
        try:
            entries=self.call('execute',self.signed('list_directory',{'path':'.'}))['result']['entries']
            kinds={x['name']:x['type'] for x in entries}
            self.assertEqual(kinds['safe.txt'],'blocked'); self.assertEqual(kinds['hard.txt'],'blocked'); self.assertEqual(kinds['junction'],'blocked')
            result=self.call('execute',self.signed('search_text',{'path':'.','query':'needle'}))['result']
            self.assertEqual([x['path'] for x in result['matches']],['target/sentinel'])
            self.assertEqual(result['blocked'],3)
            for tool,args in [('list_directory',{'path':'junction'}),('search_text',{'path':'junction','query':'needle'}),('create_directory',{'path':'junction/escape'}),('list_directory',{'path':'TARGET'})]:
                self.assertFalse(self.call('execute',self.signed(tool,args))['ok'])
            self.assertEqual((root/'target'/'sentinel').read_text(),'needle')
            self.assertFalse((root/'target'/'escape').exists())
        finally: os.rmdir(junction)

    def test_search_and_listing_quotas(self):
        root=Path(self.root)
        (root/'many').mkdir()
        for i in range(129): (root/'many'/f'{i:03}.txt').write_text('other')
        listing=self.call('execute',self.signed('list_directory',{'path':'many'}))['result']
        self.assertEqual(len(listing['entries']),128); self.assertTrue(listing['truncated'])
        result=self.call('execute',self.signed('search_text',{'path':'many','query':'absent'}))['result']
        self.assertEqual(result['files_scanned'],128); self.assertTrue(result['truncated'])
        (root/'matches.txt').write_text(('x'*60+'needle'+'x'*200+'\n')*101)
        # Search a separate directory to avoid depending on filesystem enumeration order.
        (root/'hits').mkdir(); (root/'hits'/'a').write_text(('x'*60+'needle'+'x'*200+'\n')*101)
        result=self.call('execute',self.signed('search_text',{'path':'hits','query':'needle'}))['result']
        self.assertEqual(len(result['matches']),100); self.assertTrue(result['truncated'])
        self.assertTrue(all(len(x['snippet'])==200 and 'needle' in x['snippet'] for x in result['matches']))
        current=root/'deep'; current.mkdir()
        for i in range(9): current=current/f'd{i}'; current.mkdir()
        (current/'hidden').write_text('needle')
        result=self.call('execute',self.signed('search_text',{'path':'deep','query':'needle'}))['result']
        self.assertTrue(result['truncated']); self.assertEqual(result['matches'],[])

    def test_search_directory_byte_and_utf8_bounds(self):
        root=Path(self.root)
        (root/'dirs').mkdir()
        for i in range(64):
            directory=root/'dirs'/f'd{i:02}'; directory.mkdir(); (directory/'a').write_text('needle')
        result=self.call('execute',self.signed('search_text',{'path':'dirs','query':'needle'}))['result']
        self.assertTrue(result['truncated']); self.assertEqual(len(result['matches']),63)
        (root/'bytes').mkdir()
        for i in range(129): (root/'bytes'/f'{i:03}').write_bytes(b'x'*32768)
        result=self.call('execute',self.signed('search_text',{'path':'bytes','query':'needle'}))['result']
        self.assertTrue(result['truncated']); self.assertEqual(result['files_scanned'],128)
        (root/'invalid').mkdir()
        (root/'invalid'/'binary').write_bytes(b'\xffneedle')
        (root/'invalid'/'large').write_bytes(b'needle'+b'x'*32768)
        result=self.call('execute',self.signed('search_text',{'path':'invalid','query':'needle'}))['result']
        self.assertEqual(result['matches'],[]); self.assertEqual(result['blocked'],2)

    def test_canonical_grants_and_disabled_templates(self):
        root=Path(self.root); (root/'folder').mkdir(); (root/'folder'/'a').write_text('{}')
        for tool,args in [('read_file',{'path':'folder\\a'}),('write_file',{'path':'new','content':'bad','expected_sha256':None}),('powershell',{'path':'folder/a','template':'project_tests','expected':{}})]:
            proposal=self.signed(tool,args)
            with patch('protected_runtime.agent_broker.subprocess.run') as run:
                self.assertFalse(self.call('execute',proposal)['ok']); run.assert_not_called()
            self.assertFalse(self.call('execute',proposal)['ok'])
        self.assertFalse((root/'new').exists())
        catalog=self.call('catalog',{})['result']
        self.assertEqual(len(catalog['tools']),6)
        self.assertEqual({x['name']:x['enabled'] for x in catalog['powershell_templates']},{'test_json':True,'project_tests':False})

    def test_typed_float_grants_execute_and_type_mutations_fail(self):
        payload='{"n":1e-7,"z":1.0,"negative":-0.0,"unicode":"測試","obj":{"n":1},"list":[true,null,1]}'
        (Path(self.root)/'numeric.json').write_text(payload,encoding='utf-8')
        expected=json.loads(payload)
        result=self.call('execute',self.signed('powershell',{'template':'test_json','path':'numeric.json','expected':expected}))
        self.assertTrue(result['ok'],result)
        for before,after in [(1,1.0),(0.0,-0.0),(True,1),(None,'n'),(1e-7,{'float64':'3e7ad7f29abcaf48'}),([1],{'0':1})]:
            proposal=self.signed('powershell',{'template':'test_json','path':'numeric.json','expected':{'n':before}})
            original=grant_canonical(proposal['grant'])
            proposal['grant']['args']['expected']['n']=after
            self.assertNotEqual(original,grant_canonical(proposal['grant']))
            with patch('protected_runtime.agent_broker.subprocess.run') as run:
                result=self.call('execute',proposal)
                self.assertFalse(result['ok']); self.assertEqual(result['error']['code'],'AUTHORIZATION_DENIED'); run.assert_not_called()
        self.assertEqual((Path(self.root)/'numeric.json').read_text(encoding='utf-8'),payload)

    def test_search_skips_exclusively_open_files(self):
        target=Path(self.root)/'private'; target.write_text('needle')
        with self.b.files.file('private'):
            result=self.call('execute',self.signed('search_text',{'path':'.','query':'needle'}))['result']
            self.assertEqual(result['matches'],[]); self.assertEqual(result['blocked'],1)
        self.assertEqual(target.read_text(),'needle')

class ProtocolTests(unittest.TestCase):
    def test_typed_grant_canonical_cross_language_fixture(self):
        fixture={'n':1e-7,'z':1.0,'negative':-0.0,'unicode':'測試','obj':{'n':1},'list':[True,None,1]}
        expected=b'o6{s4:lista3[tni1;]s1:nd3e7ad7f29abcaf48;s8:negatived8000000000000000;s3:objo1{s1:ni1;}s7:unicodes6:'+bytes.fromhex('e6b8ace8a9a6')+b's1:zd3ff0000000000000;}'
        self.assertEqual(grant_canonical(fixture),expected)
        self.assertEqual(hashlib.sha256(expected).hexdigest(),'c749474e61cef5be421839248143d2ab2855d1deda6d8b14d8bf50cbe6f879ec')
        self.assertEqual(grant_canonical(dict(reversed(list(fixture.items())))),expected)
        values=[None,False,True,0,1,0.0,-0.0,1.0,'1','i1;',[],{},[1],[1.0],{'n':1},{'n':1.0},{'d':'3ff0000000000000'}]
        encodings=[grant_canonical(value) for value in values]
        self.assertEqual(len(set(encodings)),len(values))
        for invalid in [float('nan'),float('inf'),float('-inf'),{1:'value'},(1,),b'value',{'nested':[float('nan')]}]:
            with self.assertRaises(ProtocolError): grant_canonical(invalid)

    def test_shared_schemas_and_normalization(self):
        self.assertEqual({tool['function']['name'] for tool in TOOLS},{'read_file','write_file','list_directory','create_directory','search_text','powershell'})
        for tool in TOOLS:
            schema=tool['function']['parameters']
            self.assertEqual(set(schema['required']),set(schema['properties']))
            self.assertFalse(schema['additionalProperties'])
        original={'path':'folder\\new','content':'hello','expected_sha256':None}
        self.assertEqual(validate_arguments('write_file',original),{'path':'folder/new','content':'hello','expected_sha256':'absent'})
        self.assertIsNone(original['expected_sha256'])
        for name,args in [('read_file',{'path':'.'}),('create_directory',{'path':'.'}),('search_text',{'path':'.','query':''}),('search_text',{'path':'.','query':'a\nb'}),('list_directory',{'path':'.','extra':True}),('externalcommand',{'path':'x'}),('powershell',{'template':'project_tests','path':'x','expected':{}})]:
            with self.assertRaises(ProtocolError): validate_arguments(name,args)
        for name in ['project_tests','unknown','test_json;whoami']:
            with self.assertRaises(ProtocolError): powershell_templates.command(name)

    def test_encoded_response_limit(self):
        request=dict(v=1,id=uid(),method='hello',params={})
        result=dict(v=1,id=request['id'],ok=True,result={'content':'\x00'*32768})
        out=io.BytesIO()
        with patch.object(Broker,'handle',return_value=result):
            self.assertEqual(serve(io.BytesIO(canonical(request)+b'\n'),out),0)
        self.assertLessEqual(len(out.getvalue()),65536)
        response=parse_frame(out.getvalue())
        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'],'RESOURCE_LIMIT')
        self.assertEqual(response['id'],request['id'])
    def test_strict(self):
        for raw in [b'{"v":1,"v":1}\n',b'{"v":NaN}\n',b'{}\r\n',b'{}\n{}\n']:
            with self.assertRaises(ProtocolError):parse_frame(raw)

if __name__=='__main__':unittest.main()
