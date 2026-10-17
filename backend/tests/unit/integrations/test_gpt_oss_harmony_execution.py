import unittest

from open_webui.integrations.gpt_oss_harmony.detection import CAPABILITY
from open_webui.integrations.gpt_oss_harmony.execution import native_executor_guidance


class HarmonyExecutorGuidanceTests(unittest.TestCase):
    tools = {
        "python": {"tool_id": "builtin:python", "native_harmony_hidden": True},
        "container.exec": {"type": "terminal", "native_harmony_hidden": True},
    }

    def test_guidance_requires_both_authorized_native_tools(self):
        native = {"info": {"meta": {"capabilities": {CAPABILITY: True}}}}
        for tools in [{}, {"python": self.tools["python"]}, {"container.exec": self.tools["container.exec"]}]:
            self.assertIsNone(native_executor_guidance(native, tools))
        guidance = native_executor_guidance(native, self.tools)
        self.assertIn("both python and container.exec are available", guidance)
        self.assertIn("explicit", guidance)

    def test_ordinary_models_do_not_receive_native_execution_policy(self):
        for model in [{}, {"info": {"meta": {"capabilities": {CAPABILITY: False}}}}]:
            self.assertIsNone(native_executor_guidance(model, self.tools))

    def test_unrelated_tools_with_the_same_names_are_not_mislabeled(self):
        native = {"info": {"meta": {"capabilities": {CAPABILITY: True}}}}
        self.assertIsNone(native_executor_guidance(native, {**self.tools, "python": {"tool_id": "custom"}}))
        self.assertIsNone(native_executor_guidance(native, {**self.tools, "container.exec": {"type": "function"}}))


if __name__ == '__main__':
    unittest.main()
