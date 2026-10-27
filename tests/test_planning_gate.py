"""Planning protocol regression tests; model responses are scripted, no tools run."""
import copy
import tempfile
import unittest
from pathlib import Path
from agent_core.service import AgentCore
from agent_core.storage import ConversationStore
from agent_core.providers.base import ModelProvider


def plan(path='config.json'):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'plan', 'function': {
        'name': 'plan_task', 'arguments': {'steps': [{'id': 'read', 'title': 'Inspect requested file',
        'dependencies': [], 'tool': 'read_file', 'path': path}]}}}]}


class Script(ModelProvider):
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    def list_models(self): return ['script']
    def chat(self, model, messages): return 'Hello'
    def tool_turn(self, model, messages, tools):
        self.requests.append(copy.deepcopy((messages, tools)))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class PlanningGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.temp.name) / 'test.db')
        self.conv = self.store.create_conversation('test', 'agent', 'script')['id']
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def core(self, turns):
        self.script = Script(turns)
        return AgentCore(self.store, self.script)
    def start(self, core): return core.agent_start('task', self.conv, 'Read config.json. Do not change anything.')

    def test_initial_valid_plan_produces_only_requested_read(self):
        core = self.core([plan(), {'tool_calls': [{'id': 'r', 'function': {'name': 'read_file', 'arguments': {'path': 'config.json'}}}]}])
        proposal = self.start(core)
        self.assertEqual(proposal['tool'], 'read_file')
        self.assertEqual(proposal['metrics']['planning_requests'], 1)
        self.assertEqual(proposal['metrics']['planning_retries'], 0)

    def test_empty_reply_gets_bounded_summary_without_execution(self):
        core=self.core([{'content':''}]*3)
        with self.assertRaisesRegex(ValueError,'summary limit'):
            self.start(core)
        self.assertEqual(len(self.script.requests),3)
        self.assertEqual(core.agent_progress('task')['tool_calls'],0)
        self.assertEqual(self.script.requests[-1][1],[])

    def test_repeated_invalid_optional_plan_stops_at_schema_budget(self):
        bad={'tool_calls':[{'function':{'name':'plan_task','arguments':{'steps':[]}}}]}
        core=self.core([bad]*4)
        with self.assertRaisesRegex(ValueError,'schema repair limit'):
            self.start(core)
        self.assertEqual(len(self.script.requests),4)
        self.assertEqual(core.agent_progress('task')['tool_calls'],0)
        self.assertEqual(core.agent_progress('task')['plan'],[])

    def test_successful_model_finish_is_unverified_not_completed(self):
        core = self.core([plan(), {'tool_calls': [{'id': 'r', 'function': {'name': 'read_file', 'arguments': {'path': 'config.json'}}}]}, {'content': 'enabled is true'}])
        p = self.start(core)
        result = core.agent_continue('task', p['proposal_id'], {'ok': True, 'content': '{"enabled":true}'})
        self.assertEqual(result['kind'], 'finished')
        self.assertEqual(result['outcome'], 'unverified')
        self.assertFalse(result['goal_validated'])

    def test_provider_failure_is_not_retried_as_missing_plan(self):
        from agent_core.providers.ollama import OllamaError
        for error in ('model_timeout: timed out', 'provider_error: invalid JSON response'):
            core = self.core([OllamaError(error), plan()])
            with self.assertRaisesRegex(OllamaError, error):
                core.agent_start(error, self.conv, 'Read requested file')
            self.assertEqual(len(self.script.requests), 1)

    def test_chat_mode_does_not_require_tool_plan(self):
        core = self.core([])
        chat = self.store.create_conversation('chat', 'chat', 'script')['id']
        result = core.send_message(chat, 'Hello')
        self.assertIn('Hello', str(result))
        self.assertEqual(self.script.requests, [])

    def test_invalid_provider_envelope_is_explicit_provider_error(self):
        from unittest.mock import patch
        from agent_core.providers.ollama import OllamaProvider, OllamaError
        for payload in ([], None, 3, 'text'):
            with self.subTest(payload=payload), patch.object(OllamaProvider, '_request', return_value=payload):
                with self.assertRaisesRegex(OllamaError, 'provider_error:'):
                    OllamaProvider().tool_turn('script', [], [])

    def test_invalid_tool_call_container_is_provider_error_not_plan_failure(self):
        for value in (None, {}, 'text'):
            core = self.core([{'role':'assistant','content':'','tool_calls':value}])
            with self.assertRaisesRegex(ValueError, 'provider_error:'):
                core.agent_start(str(id(core)), self.conv, 'Read requested file')
            self.assertEqual(len(self.script.requests), 1)
