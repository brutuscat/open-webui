"""Exercise the actual upstream dispatcher with Harmony targets and replay history."""
import ast
import copy
import json
import unittest
from pathlib import Path

class DispatchUpgradeTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[2]/'backend/open_webui/utils/tool_search.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='resolve_tool_call')
        namespace={'JSONCodec':json,'CALL_TOOL_NAME':'call_tool','SEARCH_TOOL_NAME':'search_tools'}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),namespace)
        self.resolve=namespace['resolve_tool_call']
        self.tools={'call_tool':{'tool_id':'builtin:call_tool'},'browser.open':{'native_harmony_hidden':True},'container.exec':{'native_harmony_hidden':True}}

    def call(self,target='browser.open',params=None):
        return {'id':'call-1','function':{'name':'call_tool','arguments':json.dumps({'tool_name':target,'params':json.dumps(params or {'ref':'https://github.com/open-webui/open-webui'})})}}

    def test_harmony_target_resolves_and_provider_history_is_preserved(self):
        call=self.call(); original=copy.deepcopy(call['function'])
        self.assertIsNone(self.resolve(call,self.tools))
        self.assertEqual(call['function']['name'],'browser.open')
        self.assertEqual(json.loads(call['function']['arguments']),{'ref':'https://github.com/open-webui/open-webui'})
        self.assertEqual(call['provider_function'],original)
        before=copy.deepcopy(call)
        self.assertIsNone(self.resolve(call,self.tools))
        self.assertEqual(call,before)

    def test_unavailable_and_recursive_targets_are_rejected_before_mutation(self):
        for target in ['missing_tool','call_tool','search_tools']:
            call=self.call(target); original=copy.deepcopy(call)
            self.assertTrue(self.resolve(call,self.tools).startswith('Error:'))
            self.assertEqual(call,original)

    def test_direct_harmony_calls_are_unchanged(self):
        call={'function':{'name':'container.exec','arguments':'{"cmd":"pwd"}'}}
        original=copy.deepcopy(call)
        self.assertIsNone(self.resolve(call,self.tools))
        self.assertEqual(call,original)

    def test_malformed_parameters_are_rejected(self):
        call=self.call()
        call['function']['arguments']=json.dumps({'tool_name':'browser.open','params':'[]'})
        self.assertIn('JSON object',self.resolve(call,self.tools))

if __name__=='__main__':
    unittest.main()
