"""Unit tests with a mocked model; these never execute tools or prove E2E execution."""
import copy
import tempfile
import unittest
from pathlib import Path
from agent_core.service import AgentCore
from agent_core.storage import ConversationStore
from agent_core.providers.base import ModelProvider

class MockModel(ModelProvider):
    def __init__(self, turns): self.turns, self.contexts, self.schemas = turns, [], []
    def list_models(self): return ['unit-model']
    def chat(self, model, messages): raise AssertionError('tool turn required')
    def tool_turn(self, model, messages, tools):
        self.contexts.append(copy.deepcopy(messages))
        self.schemas.append(copy.deepcopy(tools))
        return self.turns.pop(0)

def call(name='read_file', args=None, id='call-a'):
    return {'id':id, 'type':'function', 'function':{'name':name,'arguments':args or {'path':'sample.json'}}}

class AgentLoopUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=ConversationStore(Path(self.temp.name)/'unit.db')
        self.conversation=self.store.create_conversation('unit','agent','unit-model')['id']
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def core(self, turns):
        self.model=MockModel(turns)
        return AgentCore(self.store,self.model)
    def start(self, core):
        # Historical tests below isolate execution-phase contracts. Gate behavior
        # is tested from real initialization in test_planning_gate.py. Explicit
        # plan fixtures still traverse the actual gate here.
        from unittest.mock import patch
        first = self.model.turns[0] if self.model.turns else {}
        calls = first.get('tool_calls', []) if isinstance(first, dict) else []
        has_plan = isinstance(calls, list) and any(isinstance(c, dict) and c.get('function', {}).get('name') == 'plan_task' for c in calls)
        advance = core.agent_loop._advance
        def execution_phase(task_id, task):
            if not has_plan: task['plan_used'] = True
            return advance(task_id, task)
        with patch.object(core.agent_loop, '_advance', side_effect=execution_phase):
            return core.dispatch('agent_start',dict(task_id='task',conversation_id=self.conversation,goal='Inspect JSON'))
    def test_proposal_result_enters_full_model_context_and_history(self):
        core=self.core([{'role':'assistant','content':'Inspecting','tool_calls':[call()]},{'role':'assistant','content':'Actual value is 7'}])
        proposal=self.start(core)
        self.assertEqual(proposal['kind'],'proposal')
        result=core.agent_continue('task',proposal['proposal_id'],{'ok':True,'content':'{"value":7}'})
        self.assertEqual(result['kind'],'finished')
        self.assertEqual(result['outcome'],'unverified')
        self.assertFalse(result['goal_validated'])
        messages=self.model.contexts[1]
        self.assertEqual(messages[-2]['tool_calls'][0]['id'],'call-a')
        self.assertEqual(messages[-1]['tool_call_id'],'call-a')
        self.assertIn('value',messages[-1]['content'])
        self.assertEqual([m['role'] for m in self.store.history(self.conversation)],['user','assistant'])
        self.assertGreaterEqual(len(core.agent_history(self.conversation)),3)
    def test_multiple_calls_queue_before_next_model_turn(self):
        core=self.core([{'content':'','tool_calls':[call(),call(args={'path':'second.json'},id='call-b')]},{'content':'done'}])
        p=self.start(core); q=core.agent_continue('task',p['proposal_id'],{'ok':True})
        self.assertEqual(len(self.model.contexts),1)
        self.assertNotEqual(p['proposal_id'],q['proposal_id'])
        core.agent_continue('task',q['proposal_id'],{'ok':True})
        self.assertEqual([m['tool_call_id'] for m in self.model.contexts[-1] if m['role']=='tool'],['call-a','call-b'])
    def test_real_ollama_function_index_metadata_is_validated_and_removed(self):
        item=call(); item['function']['index']=0
        core=self.core([{'role':'assistant','content':'','tool_calls':[item]}])
        self.assertEqual(self.start(core)['tool'],'read_file')
        from agent_core.agent_loop import validate_call
        self.assertNotIn('index',validate_call(item)['function'])
        for value in (True,-1,'0'):
            item['function']['index']=value
            with self.assertRaises(ValueError): validate_call(item)
    def test_json_object_string_expected_is_decoded_without_guessing_values(self):
        bad=call('powershell',{'template':'test_json','path':'config.json','expected':'{"enabled":true}'})
        core=self.core([{'content':'','tool_calls':[bad]}])
        result=self.start(core)
        self.assertEqual(result['args']['expected'],{'enabled':True})
        self.assertEqual(bad['function']['arguments']['expected'],'{"enabled":true}')
        self.assertEqual(result['metrics']['schema_errors'],1)
        self.assertEqual(result['metrics']['retries'],1)
        self.assertEqual(len(self.model.contexts),1)

    def test_outer_json_argument_strings_cannot_bypass_expected_repair_budget(self):
        import json
        calls=[]
        for index in range(4):
            item=call('powershell',{'template':'test_json','path':f'input{index}.json','expected':'{"verified":true}'},id=f'p{index}')
            item['function']['arguments']=json.dumps(item['function']['arguments'])
            calls.append(item)
        core=self.core([{'tool_calls':calls}])
        with self.assertRaisesRegex(ValueError,'repair limit'):self.start(core)
        progress=core.agent_progress('task')
        self.assertEqual(progress['tool_calls'],0)
        self.assertEqual(progress['metrics']['schema_errors'],4)
        self.assertEqual(progress['metrics']['retries'],4)
        repaired=[r['event'] for r in core.agent_history(self.conversation) if r['event'].get('kind')=='schema_format_repaired']
        self.assertEqual(sum(r['count'] for r in repaired),4)

    def test_expected_object_string_rejects_duplicate_nonfinite_deep_or_nonobject(self):
        from agent_core.agent_loop import validate_call
        for value in ('{"x":1,"x":2}','{"x":NaN}','{"x":1e999}','[]','null','{"x":'+'['*20+'0'+']'*20+'}'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_call(call('powershell',{'template':'test_json','path':'a.json','expected':value}))

    def test_empty_final_answer_retries_within_same_budget(self):
        core=self.core([{'content':''},{'content':'No operations were performed.'}])
        self.assertEqual(self.start(core)['content'],'No operations were performed.')
        self.assertIn('Do not invent',self.model.contexts[-1][-1]['content'])
    def test_null_new_file_precondition_normalizes_to_exclusive_create(self):
        from agent_core.agent_loop import validate_call
        original=call('write_file',{'path':'new.txt','content':'new','expected_sha256':None})
        normalized=validate_call(original)
        self.assertEqual(normalized['function']['arguments']['expected_sha256'],'absent')
        self.assertIsNone(original['function']['arguments']['expected_sha256'])
    def test_cancel_and_replay_fail_closed(self):
        core=self.core([{'content':'','tool_calls':[call()]}]); p=self.start(core)
        core.agent_cancel('task')
        with self.assertRaises(ValueError): core.agent_continue('task',p['proposal_id'],{})
    def test_cancel_before_start_cannot_be_lost(self):
        core=self.core([{'content':'must not run'}])
        self.assertTrue(core.agent_cancel('task')['cancelled'])
        with self.assertRaisesRegex(ValueError,'cancelled'): self.start(core)
        self.assertEqual(self.model.contexts,[])
    def test_unknown_or_extra_arguments_fail_closed(self):
        for c in [call('shell'),call(args={'path':'x','extra':True}),call('write_file',{'path':'x','content':'a','expected_sha256':'bad'})]:
            core=self.core([{'content':'','tool_calls':[c]} for _ in range(4)])
            with self.assertRaises(ValueError): self.start(core)
    def test_audit_is_persistent_and_conversation_scoped(self):
        core=self.core([])
        core.agent_audit('task',self.conversation,{'kind':'broker_denied'})
        other=self.store.create_conversation('other','agent','unit-model')['id']
        self.assertEqual(core.agent_history(other),[])
        self.assertEqual(core.agent_history(self.conversation)[0]['event']['kind'],'broker_denied')

    def test_old_goal_isolated_and_workspace_only_descriptive(self):
        self.store.add_message(self.conversation,'user','OLD CANCELLED GOAL')
        self.store.add_message(self.conversation,'assistant','OLD ANSWER')
        core=self.core([{'content':'No operations performed.'}]*2)
        result=core.agent_start('task',self.conversation,'CURRENT GOAL',workspace='native-workspace',workspace_revision='r1')
        self.assertEqual(result['outcome'],'no_operation')
        context=str(self.model.contexts[0])
        self.assertNotIn('OLD',context)
        self.assertIn('CURRENT GOAL',context)
        self.assertNotIn('native-workspace',context)
        self.assertEqual(len(self.store.history(self.conversation)),4)

    def test_successful_duplicate_rejected_with_actual_prior_result(self):
        core=self.core([{'tool_calls':[call()]},{'tool_calls':[call(id='duplicate')]},{'content':'Previously read value 7.'}])
        p=self.start(core)
        result=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'7'})
        self.assertEqual(result['kind'],'finished')
        self.assertEqual(result['tool_calls'],1)
        feedback=self.model.contexts[-1][-1]
        import json
        rejected=json.loads(feedback['content'])
        self.assertFalse(rejected['executed'])
        self.assertEqual(rejected['prior_result'],{'ok':True,'content':'7'})
        self.assertEqual(rejected['prior_proposal_id'],p['proposal_id'])
        self.assertEqual(len([r for r in core.agent_history(self.conversation) if r['event']['kind']=='proposal']),1)

    def test_write_invalidates_duplicate_context_and_read_can_repeat(self):
        write=call('write_file',{'path':'sample.json','content':'7','expected_sha256':'a'*64},id='write')
        core=self.core([{'tool_calls':[call()]},{'tool_calls':[write]},{'tool_calls':[call(id='reread')]}])
        p=self.start(core)
        q=core.agent_continue('task',p['proposal_id'],{'ok':True,'sha256':'a'*64})
        r=core.agent_continue('task',q['proposal_id'],{'ok':True})
        self.assertEqual(r['tool'],'read_file')
        self.assertEqual(r['tool_calls'],3)

    def test_summary_phase_never_offers_or_proposes_tools(self):
        core=self.core([{'tool_calls':[call()]},{'content':''},{'tool_calls':[call(id='summary-attempt')]},{'content':'Read returned 7.'}])
        p=self.start(core)
        result=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'7'})
        self.assertEqual(result['tool_calls'],1)
        self.assertEqual(self.model.schemas[-2:],[[],[]])
        self.assertEqual(len([r for r in core.agent_history(self.conversation) if r['event']['kind']=='proposal']),1)

    def test_chat_mode_change_stops_before_consuming_result(self):
        core=self.core([{'tool_calls':[call()]}]);p=self.start(core)
        self.store.update_settings(self.conversation,'chat','unit-model')
        with self.assertRaisesRegex(ValueError,'left Agent Mode'):
            core.agent_continue('task',p['proposal_id'],{'ok':True})
        self.assertEqual(core.agent_loop.tasks['task']['results'],[])

    def test_human_wait_excluded_from_active_budget_but_wall_independent(self):
        core=self.core([{'tool_calls':[call()]},{'content':'Finished.'}]);p=self.start(core)
        task=core.agent_loop.tasks['task'];task['started']-=301
        result=core.agent_continue('task',p['proposal_id'],{'ok':True})
        self.assertEqual(result['kind'],'finished')
        self.assertLess(task['active'],5)

    def test_active_budget_cancels_before_result_is_consumed(self):
        core=self.core([{'tool_calls':[call()]}]);p=self.start(core)
        core.agent_loop.tasks['task']['active']=180
        with self.assertRaisesRegex(ValueError,'active time limit'):
            core.agent_continue('task',p['proposal_id'],{'ok':True})

    def test_capability_catalog_is_pure_inert_and_bounded(self):
        core=self.core([])
        result=core.agent_capabilities([{'name':'external_read','description':'Remote tool','executable':True,'inputSchema':{'type':'object'}}])
        self.assertEqual({x['function']['name'] for x in result['builtin_tools']},{'read_file','write_file','powershell','list_directory','create_directory','search_text'})
        external=result['mcp_tools'][0]
        self.assertFalse(external['executable']);self.assertFalse(external['enabled'])
        self.assertTrue(external['approval_required'])
        self.assertNotIn('inputSchema',external)
        self.assertEqual(self.store.list_protected_events(self.conversation),[])
        with self.assertRaises(ValueError): core.agent_capabilities([{'name':'x'}]*129)

    def test_new_tools_use_shared_strict_schemas_and_canonical_paths(self):
        from agent_core.agent_loop import validate_call
        for name,args in [('list_directory',{'path':'.'}),('create_directory',{'path':'new-dir'}),('search_text',{'path':'.','query':'hello'})]:
            self.assertEqual(validate_call(call(name,args))['function']['arguments'],args)
        self.assertEqual(validate_call(call(args={'path':'dir\\file.txt'}))['function']['arguments']['path'],'dir/file.txt')
        with self.assertRaises(ValueError):
            validate_call(call('powershell',{'template':'arbitrary','path':'a.json','expected':{}}))
        for args in ({'path':'.','extra':True},{'path':'..'},{'path':'C:/outside'}):
            with self.assertRaises(ValueError): validate_call(call(args=args))

    def test_cross_file_hash_rejected_before_approval_and_progress_retained(self):
        wrong=call('write_file',{'path':'other.md','content':'oops','expected_sha256':'a'*64},id='wrong')
        core=self.core([{'tool_calls':[call()]},{'tool_calls':[wrong]}]*2+[{'content':'Stopped.'}])
        p=self.start(core)
        with self.assertRaises(ValueError):
            core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'# Status\nDISABLED','sha256':'a'*64})
        progress=core.dispatch('agent_progress',{'task_id':'task'})
        self.assertEqual(progress['tool_calls'],1)
        self.assertEqual(progress['metrics']['tool_successes'],1)
        self.assertEqual(progress['files'][0]['path'],'sample.json')

    def test_markdown_content_cannot_be_replaced_by_json_and_valid_preview(self):
        bad=call('write_file',{'path':'sample.json','content':'{"enabled":true}','expected_sha256':'a'*64},id='bad')
        good=call('write_file',{'path':'sample.json','content':'# Status\nENABLED','expected_sha256':'a'*64},id='good')
        core=self.core([{'tool_calls':[call()]},{'tool_calls':[bad]},{'tool_calls':[good]}])
        p=self.start(core)
        q=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'# Status\nDISABLED','sha256':'a'*64})
        self.assertEqual(q['preview']['before'],'# Status\nDISABLED')
        self.assertEqual(q['preview']['content_type'],'markdown')
        self.assertEqual(q['metrics']['schema_errors'],1)
        self.assertEqual(q['args']['content'],'# Status\nENABLED')

    def test_powershell_context_scopes_known_successful_write_not_fresh_read(self):
        steps=[{'id':'w','title':'Create input','dependencies':[],'tool':'write_file','path':'input.json'},{'id':'p','title':'Verify input','dependencies':['w'],'tool':'powershell','path':'input.json'}]
        core=self.core([{'tool_calls':[call('plan_task',{'steps':steps},id='plan')]},{'tool_calls':[call('write_file',{'path':'input.json','content':'{"verified":true}','expected_sha256':None},id='write')]},{'tool_calls':[call('powershell',{'template':'test_json','path':'input.json','expected':{'verified':True}},id='verify')]}])
        p=self.start(core);core.agent_continue('task',p['proposal_id'],{'ok':True,'sha256':'b'*64,'bytes_written':17})
        focus=self.model.contexts[-1][-1]['content']
        self.assertIn('known_successful_write',focus)
        self.assertIn('verified',focus)
        self.assertIn('b'*64,focus)
        self.assertIn('not a fresh read',focus)
        self.assertIn('expected',focus)

    def test_schema_rejection_records_bounded_proposal_shape(self):
        bad=call('powershell',{'template':'test_json','path':'input.json'})
        core=self.core([{'tool_calls':[bad]},{'content':'Stopped.'}]);self.start(core)
        event=next(r['event'] for r in core.agent_history(self.conversation) if r['event'].get('kind')=='schema_rejected')
        self.assertIn('test_json',event['proposal_shape_json'])
        self.assertLessEqual(len(event['proposal_shape_json']),16384)

    def test_new_write_scopes_read_maintenance_and_omits_other_file_data(self):
        steps=[{'id':'r','title':'Read old','dependencies':[],'tool':'read_file','path':'old.json'},{'id':'w','title':'Create report','dependencies':['r'],'tool':'write_file','path':'reports/new.json'}]
        core=self.core([{'tool_calls':[call('plan_task',{'steps':steps},id='plan')]},{'tool_calls':[call('read_file',{'path':'old.json'},id='old')]},{'tool_calls':[call('read_file',{'path':'unrelated.json'},id='wrong')]},{'tool_calls':[call('write_file',{'path':'reports/new.json','content':'{"verified":true}','expected_sha256':None},id='new')]}])
        p=self.start(core);q=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'OTHER FILE CONTENT','sha256':'a'*64})
        self.assertEqual(q['tool'],'write_file')
        self.assertEqual(q['tool_calls'],2)
        offered=next(t for t in self.model.schemas[-1] if t['function']['name']=='read_file')
        self.assertEqual(offered['function']['parameters']['properties']['path']['enum'],['reports/new.json'])
        self.assertNotIn('OTHER FILE CONTENT',str(self.model.contexts[-1]))
        self.assertNotIn('a'*64,str(self.model.contexts[-1]))
        self.assertIn('untrusted_target_read',self.model.contexts[-1][-1]['content'])

    def test_unchanged_write_is_rejected_without_approval(self):
        unchanged=call('write_file',{'path':'sample.json','content':'{"enabled":false}','expected_sha256':'a'*64},id='noop')
        core=self.core([{'tool_calls':[call()]},{'tool_calls':[unchanged]},{'content':'Stopped.'}])
        p=self.start(core);final=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"enabled":false}','sha256':'a'*64})
        self.assertEqual(final['kind'],'finished')
        self.assertEqual(final['tool_calls'],1)
        self.assertEqual(final['metrics']['schema_errors'],1)

    def test_next_write_context_contains_only_its_approved_read_version(self):
        import json
        steps=[{'id':'a','title':'Read config','dependencies':[],'tool':'read_file','path':'src/config.json'},{'id':'b','title':'Read status','dependencies':['a'],'tool':'read_file','path':'docs/status.md'},{'id':'c','title':'Update status to ENABLED','dependencies':['b'],'tool':'write_file','path':'docs/status.md'}]
        core=self.core([{'tool_calls':[call('plan_task',{'steps':steps},id='plan')]},{'tool_calls':[call('read_file',{'path':'src/config.json'},id='a')]},{'tool_calls':[call('read_file',{'path':'docs/status.md'},id='b')]},{'tool_calls':[call('write_file',{'path':'docs/status.md','content':'Feature is ENABLED.','expected_sha256':'b'*64},id='c')]}])
        p=self.start(core);q=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"label":"OTHER FILE"}','sha256':'a'*64})
        core.agent_continue('task',q['proposal_id'],{'ok':True,'content':'Feature is DISABLED.','sha256':'b'*64})
        focus=self.model.contexts[-1][-1]['content']
        self.assertIn('Feature is DISABLED.',focus)
        self.assertIn('b'*64,focus)
        self.assertNotIn('a'*64,focus)
        self.assertNotIn('OTHER FILE',focus)
        self.assertNotIn('OTHER FILE',str(self.model.contexts[-1]))
        self.assertNotIn('a'*64,str(self.model.contexts[-1]))
        self.assertIn('untrusted',focus.lower())

    def test_premature_empty_and_claimed_completion_keep_tools_enabled(self):
        for content in ('','All done successfully.'):
            with self.subTest(content=content):
                taskid='early-'+str(len(content))
                plan=call('plan_task',{'steps':[{'id':'r','title':'read','dependencies':[],'tool':'read_file','path':'sample.json'}]},id='plan')
                core=self.core([{'tool_calls':[plan]},{'content':content},{'tool_calls':[call()]}])
                proposal=core.agent_start(taskid,self.conversation,'Read sample.json')
                self.assertEqual(proposal['kind'],'proposal')
                self.assertEqual(proposal['tool'],'read_file')
                self.assertEqual(proposal['metrics']['premature_completions'],1)
                self.assertTrue(self.model.schemas[-1])
                self.assertFalse(core.agent_progress(taskid)['goal_validated'])

    def test_premature_completion_limit_fails_with_retained_incomplete_plan(self):
        plan=call('plan_task',{'steps':[{'id':'r','title':'read','dependencies':[],'tool':'read_file','path':'sample.json'}]},id='plan')
        core=self.core([{'tool_calls':[plan]}]+[{'content':'Finished.'} for _ in range(4)])
        with self.assertRaisesRegex(ValueError,'incomplete_plan'):self.start(core)
        progress=core.agent_progress('task')
        self.assertEqual(progress['plan'][0]['state'],'blocked')
        self.assertEqual(progress['tool_calls'],0)
        self.assertEqual(progress['metrics']['premature_completions'],4)
        self.assertFalse(progress['goal_validated'])

    def test_plain_markdown_paragraph_rejects_json_with_own_hash(self):
        read=call('read_file',{'path':'docs/status.md'})
        bad=call('write_file',{'path':'docs/status.md','content':'{"enabled":true}','expected_sha256':'a'*64},id='bad')
        core=self.core([{'tool_calls':[read]},{'tool_calls':[bad]},{'content':'Stopped.'}])
        p=self.start(core);final=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'Feature is DISABLED.','sha256':'a'*64})
        self.assertEqual(final['files'][0]['content_type'],'markdown')
        self.assertEqual(final['tool_calls'],1)
        self.assertEqual(final['metrics']['schema_errors'],1)

    def test_valid_json_in_markdown_path_is_classified_from_content(self):
        core=self.core([{'tool_calls':[call('read_file',{'path':'docs/data.md'})]},{'tool_calls':[call('write_file',{'path':'docs/data.md','content':'{"enabled":true}','expected_sha256':'a'*64},id='write')]}])
        p=self.start(core);q=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"enabled":false}','sha256':'a'*64})
        self.assertEqual(q['preview']['content_type'],'json')
        self.assertEqual(q['tool'],'write_file')

    def test_cancelled_known_result_is_recorded_once_without_model_advance(self):
        plan=call('plan_task',{'steps':[{'id':'r','title':'read','dependencies':[],'tool':'read_file','path':'sample.json'},{'id':'w','title':'write','dependencies':['r'],'tool':'write_file','path':'sample.json'}]},id='plan')
        core=self.core([{'tool_calls':[plan]},{'tool_calls':[call()]}])
        p=self.start(core);core.agent_cancel('task');turns=len(self.model.contexts)
        progress=core.dispatch('agent_record_result',{'task_id':'task','proposal_id':p['proposal_id'],'result':{'ok':True,'sha256':'a'*64,'content':'7'}})
        self.assertTrue(progress['cancelled'])
        self.assertEqual(progress['metrics']['tool_successes'],1)
        self.assertEqual(progress['plan'][0]['state'],'completed')
        self.assertEqual(progress['plan'][1]['state'],'blocked')
        self.assertFalse(progress['files'][0]['valid'])
        self.assertEqual(len(self.model.contexts),turns)
        with self.assertRaisesRegex(ValueError,'already consumed'):
            core.agent_record_result('task',p['proposal_id'],{'ok':True})
        with self.assertRaises(ValueError):core.agent_continue('task',p['proposal_id'],{'ok':True})

    def test_plan_result_summary_is_bounded_and_unplanned_mutation_rejected(self):
        plan=call('plan_task',{'steps':[{'id':'r','title':'read','dependencies':[],'tool':'read_file','path':'sample.json'}]},id='plan')
        unexpected=call('create_directory',{'path':'other'},id='unplanned')
        core=self.core([{'tool_calls':[plan]},{'tool_calls':[call()]},{'tool_calls':[unexpected]},{'content':'Stopped.'}])
        p=self.start(core)
        final=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'x'*32000,'sha256':'a'*64})
        self.assertNotIn('content',final['plan'][0]['result'])
        self.assertEqual(final['tool_calls'],1)
        self.assertEqual(final['metrics']['schema_errors'],0)
        self.assertEqual(self.model.schemas[-2:],[[],[]])
        self.assertTrue(any(r['event']['kind']=='summary_tool_rejected' for r in core.agent_history(self.conversation)))

    def test_plan_root_paths_match_tool_path_contract(self):
        from agent_core.agent_loop import validate_plan
        for tool in ('list_directory','search_text'):
            steps=validate_plan({'steps':[{'id':'s','title':'Inspect root','dependencies':[],'tool':tool,'path':'.'}]})
            self.assertEqual(steps[0]['path'],'.')
        for tool in ('read_file','write_file'):
            with self.assertRaises(ValueError):validate_plan({'steps':[{'id':'s','title':'Invalid root','dependencies':[],'tool':tool,'path':'.'}]})

    def test_initial_model_schema_has_execution_and_optional_plan(self):
        core=self.core([{'content':'No operations.'}]*2)
        result=core.agent_start('task',self.conversation,'Inspect JSON')
        self.assertEqual(result['outcome'],'no_operation')
        self.assertEqual({t['function']['name'] for t in self.model.schemas[0]},
                         {'plan_task','read_file','write_file','list_directory','create_directory','search_text','powershell'})

    def test_cancel_invalidates_versions_and_blocks_unfinished_plan(self):
        plan=call('plan_task',{'steps':[{'id':'r','title':'read','dependencies':[],'tool':'read_file','path':'sample.json'},{'id':'w','title':'write','dependencies':['r'],'tool':'write_file','path':'sample.json'}]})
        core=self.core([{'tool_calls':[plan]},{'tool_calls':[call(id='read')]},{'tool_calls':[call('write_file',{'path':'sample.json','content':'8','expected_sha256':'a'*64},id='write')]}])
        p=self.start(core); core.agent_continue('task',p['proposal_id'],{'ok':True,'sha256':'a'*64,'content':'7'})
        core.agent_cancel('task')
        progress=core.agent_progress('task')
        self.assertFalse(progress['files'][0]['valid'])
        self.assertEqual(progress['plan'][0]['state'],'completed')
        self.assertEqual(progress['plan'][1]['state'],'blocked')

    def test_new_attempt_cannot_reuse_old_read_version(self):
        core=self.core([{'tool_calls':[call()]},{'content':'Stopped.'}])
        p=self.start(core);core.agent_continue('task',p['proposal_id'],{'ok':True,'sha256':'a'*64,'content':'7'})
        self.model.turns=[{'tool_calls':[call('write_file',{'path':'sample.json','content':'8','expected_sha256':'a'*64},id=str(i))]} for i in range(4)]
        with self.assertRaisesRegex(ValueError,'schema repair limit'):
            core.agent_start('fresh',self.conversation,'Retry goal')
        self.assertEqual(core.agent_progress('fresh')['tool_calls'],0)
        self.assertEqual(core.agent_progress('fresh')['metrics']['schema_errors'],4)

    def test_invalid_plan_cycles_never_produce_execution_proposal(self):
        plan=call('plan_task',{'steps':[{'id':'a','title':'a','dependencies':['b'],'tool':'read_file','path':'x'},{'id':'b','title':'b','dependencies':['a'],'tool':'read_file','path':'y'}]})
        core=self.core([{'tool_calls':[plan]} for _ in range(4)])
        with self.assertRaisesRegex(ValueError,'schema repair limit'):self.start(core)
        self.assertEqual(core.agent_progress('task')['tool_calls'],0)

    def test_advisory_plan_dependencies_follow_actual_results(self):
        plan=call('plan_task',{'steps':[{'id':'inspect','title':'Read input','dependencies':[],'tool':'read_file','path':'sample.json'},{'id':'edit','title':'Edit input','dependencies':['inspect'],'tool':'write_file','path':'sample.json'}]},id='plan')
        write=call('write_file',{'path':'sample.json','content':'{"enabled":true}','expected_sha256':'a'*64},id='edit')
        core=self.core([{'tool_calls':[plan]},{'tool_calls':[call()]},{'tool_calls':[write]},{'content':'done'}])
        p=self.start(core)
        self.assertEqual(p['plan_step_id'],'inspect')
        self.assertEqual(p['plan'][1]['state'],'blocked')
        q=core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"enabled":false}','sha256':'a'*64})
        self.assertEqual(q['plan'][0]['state'],'completed')
        self.assertEqual(q['plan_step_id'],'edit')
        final=core.agent_continue('task',q['proposal_id'],{'ok':True,'sha256':'b'*64})
        self.assertEqual(final['plan'][1]['state'],'completed')
        self.assertFalse(final['files'][0]['valid'])
        self.assertFalse(final['goal_validated'])



class ProviderToolTurnUnitTests(unittest.TestCase):
    def test_advisory_plan_has_bounded_larger_output_budget(self):
        from unittest.mock import patch
        from agent_core.providers.ollama import OllamaProvider
        from agent_core.agent_loop import PLAN_TOOL
        provider=OllamaProvider()
        with patch.object(provider,'_request',return_value={'message':{'content':''}}) as request:
            provider.tool_turn('unit',[],[PLAN_TOOL])
            self.assertEqual(request.call_args.args[1]['options']['num_predict'],1536)
            self.assertEqual(request.call_args.kwargs['timeout'],15)

    def test_ollama_preserves_tool_turn_and_caps_timeout(self):
        from unittest.mock import patch
        from agent_core.providers.ollama import OllamaProvider
        provider = OllamaProvider(timeout=999)
        self.assertEqual(provider.timeout,999)
        assistant={'role':'assistant','content':'','tool_calls':[call()]}
        messages=[{'role':'tool','tool_call_id':'x','content':'actual'}]
        with patch.object(provider,'_request',return_value={'message':assistant}) as request:
            self.assertEqual(provider.tool_turn('unit',messages,[]),assistant)
            self.assertEqual(request.call_args.args[1]['messages'],messages)
            self.assertEqual(request.call_args.kwargs['timeout'],15)
            self.assertFalse(request.call_args.args[1]['think'])
            self.assertEqual(request.call_args.args[1]['options'],{'num_predict':512,'temperature':0})
        self.assertEqual(OllamaProvider().timeout,120)

class AgentLoopLimitsUnitTests(AgentLoopUnitTests):
    def test_cancel_while_model_inflight_discards_proposal(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        core=self.core([])
        def turn(*args):
            entered.set(); release.wait(2)
            return {'content':'','tool_calls':[call()]}
        self.model.tool_turn=turn
        errors=[]
        def run():
            try: self.start(core)
            except ValueError as error: errors.append(str(error))
        worker=threading.Thread(target=run); worker.start()
        self.assertTrue(entered.wait(2)); core.agent_cancel('task'); release.set(); worker.join(2)
        self.assertFalse(worker.is_alive()); self.assertTrue(errors)
        self.assertFalse(any(row['event']['kind']=='proposal' for row in core.agent_history(self.conversation)))
    def test_replayed_result_does_not_advance(self):
        core=self.core([{'content':'','tool_calls':[call(),call(args={'path':'second.json'},id='call-b')]}])
        p=self.start(core); core.agent_continue('task',p['proposal_id'],{'ok':False,'error':'denied'})
        with self.assertRaises(ValueError): core.agent_continue('task',p['proposal_id'],{'ok':True})
    def test_tool_budget_validates_entire_batch_before_proposing(self):
        core=self.core([{'content':'','tool_calls':[call(id=str(i)) for i in range(25)]}])
        with self.assertRaisesRegex(ValueError,'tool limit'): self.start(core)
        self.assertFalse(any(row['event']['kind']=='proposal' for row in core.agent_history(self.conversation)))
    def test_time_budget_blocks_continuation(self):
        core=self.core([{'content':'','tool_calls':[call()]}]); p=self.start(core)
        core.agent_loop.tasks['task']['started']-=901
        with self.assertRaisesRegex(ValueError,'time limit'): core.agent_continue('task',p['proposal_id'],{})
    def test_model_turn_budget_is_bounded(self):
        core=self.core([{'content':'','tool_calls':[call(id=str(i))]} for i in range(21)])
        p=self.start(core)
        for _ in range(19): p=core.agent_continue('task',p['proposal_id'],{'ok':False})
        with self.assertRaisesRegex(ValueError,'model turn limit'): core.agent_continue('task',p['proposal_id'],{'ok':False})
        self.assertEqual(len(self.model.contexts),20)

if __name__ == '__main__':
    unittest.main()
