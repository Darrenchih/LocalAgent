import json,sys,tempfile,unittest
from pathlib import Path
from agent_discovery.service import DiscoveryService, DiscoveryError

FIXTURE=Path(__file__).parent/'fixtures'/'mcp_inert.py'
class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.service=DiscoveryService(self.root,timeout=.2)
    def tearDown(self):
        self.service.close(); self.tmp.cleanup()
    def save(self,mode='ok'):
        return self.service.save_server({'id':'fixture','name':'Inert fixture','executable':sys.executable,'args':[str(FIXTURE.resolve()),mode]})
    def test_persist_without_launch_and_no_auto_reconnect(self):
        self.save(); self.assertEqual(self.service.list_servers()[0]['state'],'disconnected')
        second=DiscoveryService(self.root); self.assertEqual(second.list_servers()[0]['state'],'disconnected'); second.close()
    def test_real_initialize_list_and_disconnect(self):
        self.save(); state=self.service.connect('fixture'); self.assertEqual(state['state'],'connected')
        self.assertEqual(state['tools'][0]['name'],'inert_echo'); self.assertNotIn('instructions',state)
        pid=state['pid']; self.assertGreater(pid,0)
        self.service.disconnect('fixture'); self.assertEqual(self.service.status('fixture')['state'],'disconnected')
    def test_explicit_execution_denial(self):
        for action in ['tools/call','tool_call','grant','permit','broker_dispatch','shell','connect_from_model']:
            with self.subTest(action=action),self.assertRaisesRegex(DiscoveryError,'formal_execution_disabled_review036'):
                self.service.dispatch(action,{})
    def test_invalid_configs_never_launch(self):
        for value in [dict(id='../bad',name='x',executable=sys.executable,args=[]),dict(id='x',name='x',executable='python',args=[]),dict(id='x',name='x',executable=sys.executable,args='-c x'),dict(id='x',name='x',executable=sys.executable,args=[],env={'SECRET':'sensitive'})]:
            with self.subTest(value=value),self.assertRaises(DiscoveryError):self.service.save_server(value)
    def test_transport_failures_are_bounded_and_sanitized(self):
        for mode in ['timeout','exit','malformed','oversize','bad_version','bad_schema','wrong_id','pages']:
            with self.subTest(mode=mode):
                self.save(mode)
                with self.assertRaises(DiscoveryError) as error:self.service.connect('fixture')
                self.assertNotIn(str(FIXTURE),str(error.exception)); self.assertEqual(self.service.status('fixture')['state'],'error')
    def test_server_requests_cannot_grant_authority(self):
        self.save('request'); state=self.service.connect('fixture');self.assertEqual(state['state'],'connected')
        self.assertFalse(self.service.health()['execution_enabled'])
    def test_corrupt_persistence_is_fail_closed(self):
        self.save(); path=self.root/'servers.json'; data=json.loads(path.read_text());data['servers'][0].pop('sha256');path.write_text(json.dumps(data))
        with self.assertRaisesRegex(DiscoveryError,'config_invalid'):DiscoveryService(self.root)
    def test_duplicate_connect_reaps_previous_session(self):
        self.save(); first=self.service.connect('fixture')['pid']; second=self.service.connect('fixture')['pid'];self.assertNotEqual(first,second)
        self.assertEqual(len(self.service.sessions),1)
    def test_shutdown_refuses_future_start(self):
        self.save();self.service.close()
        with self.assertRaisesRegex(DiscoveryError,'runtime_stopped'):self.service.connect('fixture')
    def test_hash_change_refuses_start(self):
        self.save(); self.service.configs['fixture']['sha256']='0'*64
        with self.assertRaisesRegex(DiscoveryError,'executable_changed'):self.service.connect('fixture')

    def test_transport_itself_rejects_tools_call_before_send(self):
        self.save();self.service.connect('fixture')
        with self.assertRaisesRegex(DiscoveryError,'formal_execution_disabled_review036'):
            self.service.sessions['fixture'].rpc('tools/call',{})
    def test_private_job_reaps_server_descendant(self):
        import ctypes
        from ctypes import wintypes
        self.save('child');state=self.service.connect('fixture');pid=state['tools'][0]['inputSchema']['x-child-pid']
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
        k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];k.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=k.OpenProcess(0x100000,False,pid);self.assertTrue(handle)
        try:
            self.service.disconnect('fixture');self.assertEqual(k.WaitForSingleObject(handle,2000),0)
        finally:k.CloseHandle(handle)

    def test_blocked_server_stdin_cannot_hang_runtime(self):
        import subprocess
        code="""import sys
from pathlib import Path
from agent_discovery.service import DiscoveryService,DiscoveryError
s=DiscoveryService(Path(sys.argv[1]),timeout=.15)
s.save_server({'id':'fixture','name':'fixture','executable':sys.executable,'args':[sys.argv[2],'write_block']})
try:
    s.connect('fixture')
    raise AssertionError('hostile server accepted')
except DiscoveryError:
    print('bounded_failure',flush=True)
finally:s.close()
"""
        for mode in ['write_block','write_flood']:
            with self.subTest(mode=mode):
                result=subprocess.run([sys.executable,'-c',code.replace("'write_block'",repr(mode)),str(self.root/mode),str(FIXTURE.resolve())],capture_output=True,text=True,timeout=3)
                self.assertEqual(result.returncode,0,result.stderr);self.assertIn('bounded_failure',result.stdout)
    def test_persistence_budget_is_checked_before_commit(self):
        for index in range(4):
            self.service.save_server({'id':f'large{index}','name':'fixture','executable':sys.executable,'args':['a'*2000]*30})
        original=(self.root/'servers.json').read_bytes()
        with self.assertRaisesRegex(DiscoveryError,'config_limit_exceeded'):
            self.service.save_server({'id':'overflow','name':'fixture','executable':sys.executable,'args':['a'*2000]*30})
        self.assertEqual((self.root/'servers.json').read_bytes(),original)
        self.assertEqual(len(self.service.list_servers()),4)
        second=DiscoveryService(self.root);self.assertEqual(len(second.list_servers()),4);second.close()
