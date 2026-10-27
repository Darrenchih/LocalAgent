"""Contract/gate regressions; scripted model replies, no real tools executed."""
import copy
import unittest
import test_planning_gate as gate
plan = gate.plan
from agent_core.agent_loop import validate_plan
from protected_runtime.agent_tools import TOOLS as BUILTINS


def step(id, tool, path, dependencies=None):
    return {'id': id, 'title': 'Requested operation', 'dependencies': dependencies or [], 'tool': tool, 'path': path}


def proposal(path='config.json', id='read'):
    return {'tool_calls': [{'id': id, 'function': {'name': 'read_file', 'arguments': {'path': path}}}]}


class ContractAlignmentTests(unittest.TestCase):
    def test_model_descriptions_do_not_change_executable_parameter_contracts(self):
        from agent_core.tool_contracts import model_tools
        original = copy.deepcopy(BUILTINS)
        def structural(value):
            if isinstance(value, dict): return {k: structural(v) for k, v in value.items() if k != 'description'}
            if isinstance(value, list): return [structural(v) for v in value]
            return value
        offered = model_tools()
        self.assertEqual(structural(offered), structural(original))
        self.assertEqual(BUILTINS, original)  # shared P/D dictionaries must not be mutated
        by_name = {t['function']['name']: t['function'] for t in offered}
        self.assertIn('directory', by_name['search_text']['parameters']['properties']['path']['description'])
        self.assertIn('file', by_name['read_file']['parameters']['properties']['path']['description'])

    def test_conflicting_file_directory_and_parent_roles_are_rejected(self):
        for entries in (
            [step('a','read_file','prefs.json'), step('b','search_text','prefs.json',['a'])],
            [step('a','read_file','Notes'), step('b','list_directory','notes',['a'])],
            [step('a','read_file','folder'), step('b','read_file','folder/child.txt',['a'])],
            [step('a','create_directory','new'), step('b','write_file','new',['a'])],
        ):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'path contract'):
                validate_plan({'steps': entries})

    def test_path_kind_is_not_guessed_from_extension_or_actual_filesystem(self):
        for entries in (
            [step('a','search_text','cache.json')],
            [step('a','list_directory','.')],
            [step('a','read_file','notes/entry.txt'), step('b','list_directory','notes',['a'])],
        ):
            self.assertEqual(len(validate_plan({'steps': entries})), len(entries))

    def test_approved_file_ledger_prevents_later_directory_proposal(self):
        from agent_core.tool_contracts import validate_known_target
        with self.assertRaisesRegex(ValueError,'directory'):
            validate_known_target('search_text','NOTES/entry.txt',{'notes/entry.txt':{'valid':False}})
        validate_known_target('read_file','notes/entry.txt',{'notes/entry.txt':{'valid':True}})


class AlignmentLoopTests(unittest.TestCase):
    setUp = gate.PlanningGateTests.setUp
    tearDown = gate.PlanningGateTests.tearDown
    core = gate.PlanningGateTests.core
    start = gate.PlanningGateTests.start
    def test_unknown_planned_tool_feedback_names_error_and_retains_valid_tools(self):
        invalid = plan(); invalid['tool_calls'][0]['function']['arguments']['steps'][0]['tool'] = 'summarize'
        core = self.core([invalid, plan(), proposal()])
        p = self.start(core)
        self.assertEqual(p['kind'], 'proposal')
        feedback = self.script.requests[1][0][-1]['content']
        self.assertIn('summarize', feedback)
        self.assertIn('unknown planned tool', feedback)
        self.assertIn('plan_task',[t['function']['name'] for t in self.script.requests[1][1]])
        self.assertNotIn('summarize',[t['function']['name'] for t in self.script.requests[1][1]])

    def test_conflicting_plan_is_rejected_before_any_native_proposal(self):
        invalid = plan()
        invalid['tool_calls'][0]['function']['arguments']['steps'].append(step('search','search_text','config.json',['read']))
        core = self.core([invalid]*4)
        with self.assertRaisesRegex(ValueError, 'schema repair limit'):
            self.start(core)
        self.assertEqual(core.agent_progress('task')['tool_calls'], 0)
        self.assertIn('path contract',self.script.requests[1][0][-1]['content'])

    def test_complete_read_plan_enters_summary_only_without_extra_execution(self):
        extra = {'tool_calls':[{'id':'extra','function':{'name':'search_text','arguments':{'path':'.','query':'value'}}}]}
        core = self.core([plan(), proposal(), extra, {'content':'The read returned value 7.'}])
        p = self.start(core)
        result = core.agent_continue('task',p['proposal_id'],{'ok':True,'content':'{"value":7}','sha256':'a'*64})
        self.assertEqual(self.script.requests[2][1], [])
        self.assertEqual(self.script.requests[3][1], [])
        self.assertEqual(result['tool_calls'], 1)
        self.assertFalse(result['goal_validated'])
        self.assertEqual(result['outcome'], 'unverified')

    def test_later_failure_keeps_actual_earlier_read(self):
        two = plan(); two['tool_calls'][0]['function']['arguments']['steps'].append(step('b','read_file','other.json',['read']))
        core = self.core([two, proposal(), proposal('other.json','read-b'), {'content':'Done'}])
        a = self.start(core)
        b = core.agent_continue('task',a['proposal_id'],{'ok':True,'content':'{"value":7}','sha256':'a'*64})
        with self.assertRaisesRegex(ValueError, 'incomplete_plan'):
            core.agent_continue('task',b['proposal_id'],{'ok':False,'error':'ACCESS_DENIED'})
        progress = core.agent_progress('task')
        self.assertEqual(progress['plan'][0]['state'], 'completed')
        self.assertEqual(progress['metrics']['tool_successes'], 1)
        self.assertEqual(progress['metrics']['tool_failures'], 1)
