"""S loop contracts; scripted providers, no filesystem tool execution."""
import tempfile
import unittest
from pathlib import Path
from agent_core.service import AgentCore
from agent_core.storage import ConversationStore
from test_planning_gate import Script, plan


def read(path='config.json', call_id='read'):
    return {'tool_calls':[{'id':call_id,'function':{'name':'read_file','arguments':{'path':path}}}]}


class DirectLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=ConversationStore(Path(self.tmp.name)/'test.db')
        self.conv=self.store.create_conversation('test','agent','script')['id']
    def tearDown(self):
        self.store.close(); self.tmp.cleanup()
    def start(self, turns):
        self.provider=Script(turns)
        self.core=AgentCore(self.store,self.provider)
        return self.core.agent_start('task',self.conv,'Read the requested file and answer from actual data')
    def test_first_turn_can_propose_read_without_plan(self):
        p=self.start([read()])
        self.assertEqual(p['tool'],'read_file')
        offered={t['function']['name'] for t in self.provider.requests[0][1]}
        self.assertIn('read_file',offered); self.assertIn('plan_task',offered)
        self.assertEqual(p['metrics']['planning_requests'],0)
        self.assertEqual(p['metrics']['tool_successes'],0)
    def test_result_drives_next_target_and_final_is_unverified(self):
        p=self.start([read('pointer.json'),read('beta.txt','second'),{'content':'Actual number is 29'}])
        q=self.core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"next":"beta.txt"}'})
        self.assertEqual(q['args']['path'],'beta.txt')
        self.assertIn('beta.txt',self.provider.requests[1][0][-1]['content'])
        r=self.core.agent_continue('task',q['proposal_id'],{'ok':True,'content':'orchid 29'})
        self.assertEqual(r['outcome'],'unverified'); self.assertFalse(r['goal_validated'])
    def test_optional_plan_still_constrains_operations(self):
        p=self.start([plan(),read()])
        self.assertEqual(p['tool'],'read_file')
        self.assertEqual(p['plan_step_id'],'read')
        self.assertEqual(p['metrics']['valid_plans'],1)
    def test_plain_stop_never_means_validated_or_executed(self):
        r=self.start([{'content':'Done'}])
        self.assertEqual(r['outcome'],'no_operation')
        self.assertFalse(r['goal_validated']); self.assertEqual(r['tool_calls'],0)
    def test_unknown_tool_is_rejected_before_proposal(self):
        bad={'tool_calls':[{'id':'bad','function':{'name':'run_command','arguments':{'path':'config.json'}}}]}
        p=self.start([bad,read()])
        self.assertEqual(p['tool'],'read_file'); self.assertEqual(p['metrics']['schema_errors'],1)
    def test_direct_existing_write_still_requires_own_approved_hash(self):
        bad={'tool_calls':[{'id':'write','function':{'name':'write_file','arguments':{'path':'config.json','content':'{}','expected_sha256':'a'*64}}}]}
        p=self.start([bad,read()])
        self.assertEqual(p['tool'],'read_file'); self.assertEqual(p['metrics']['tool_successes'],0)
    def test_error_result_can_be_reported_honestly(self):
        p=self.start([read(),{'content':'Cannot read: FILE_NOT_FOUND'}])
        r=self.core.agent_continue('task',p['proposal_id'],{'ok':False,'error':'FILE_NOT_FOUND'})
        self.assertEqual(r['outcome'],'tool_error'); self.assertFalse(r['goal_validated'])

    def test_late_plan_repeating_current_read_is_repaired_without_deadlock(self):
        late=plan(); late['tool_calls'][0]['id']='late'
        p=self.start([read(),late,{'content':'Actual enabled is true'}])
        r=self.core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"enabled":true}','sha256':'a'*64})
        self.assertEqual(r['kind'],'finished')
        self.assertEqual(r['tool_calls'],1)
        self.assertEqual(r['plan'],[])
        self.assertIn('already has an actual successful result',self.provider.requests[-1][0][-1]['content'])

    def test_late_plan_cannot_reuse_a_direct_call_id(self):
        late=plan('other.txt'); late['tool_calls'][0]['id']='read'
        p=self.start([read(),late,{'content':'Report the actual result only'}])
        r=self.core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'7','sha256':'a'*64})
        self.assertEqual(r['kind'],'finished')
        self.assertEqual(r['plan'],[])
        ids=[c['id'] for m in self.provider.requests[-1][0] for c in m.get('tool_calls',[])]
        self.assertEqual(len(ids),len(set(ids)))

    def test_model_gets_relative_root_without_host_identifier_or_revision(self):
        import json
        self.provider=Script([read()]);self.core=AgentCore(self.store,self.provider)
        goal='Read the requested file'
        self.core.agent_start('task',self.conv,goal,workspace='D:/synthetic/selected-folder',workspace_revision='rev')
        messages=self.provider.requests[0][0]
        metadata=next(m['content'] for m in messages if m['content'].startswith('Native workspace descriptive metadata: '))
        value=json.loads(metadata.split(': ',1)[1])
        self.assertEqual(value['tool_root'],'.')
        self.assertNotIn('D:/synthetic/selected-folder',str(messages))
        self.assertNotIn('workspace_revision',str(messages))
        self.assertEqual(messages[-1]['content'],goal)


if __name__=='__main__': unittest.main()
