import copy
import unittest
from agent_core.tool_contracts import model_tools
from protected_runtime.agent_tools import TOOLS, TOOL_PATH_KINDS


class SchemaIsolationTests(unittest.TestCase):
    def test_each_tool_has_only_its_target_role(self):
        original = copy.deepcopy(TOOLS)
        tools = model_tools()
        for tool in tools:
            fn = tool['function']
            description = fn['parameters']['properties']['path']['description']
            kind = TOOL_PATH_KINDS[fn['name']]
            self.assertEqual(description.count('Target must be a '), 1, fn['name'])
            self.assertIn('Target must be a '+kind+'.', description)
        self.assertEqual(TOOLS, original)

    def test_mutation_isolated_across_tools_and_invocations(self):
        tools = model_tools()
        tools[0]['function']['parameters']['properties']['path']['description'] = 'changed'
        self.assertNotEqual(tools[1]['function']['parameters']['properties']['path']['description'], 'changed')
        self.assertNotEqual(model_tools()[0]['function']['parameters']['properties']['path']['description'], 'changed')


if __name__ == '__main__':
    unittest.main()
