import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from open_webui.integrations.gpt_oss_harmony.detection import CAPABILITY
from open_webui.integrations.gpt_oss_harmony.python import jupyter_python_enabled, native_python_tools
from open_webui.utils.code_interpreter import JupyterCodeExecuter
from open_webui.utils.tools import get_builtin_tools


class HarmonyPythonTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _jupyter_config(**overrides):
        config = {
            "code_interpreter.enable": True,
            "code_interpreter.engine": "jupyter",
            "code_interpreter.jupyter.url": "http://harmony-jupyter:8888",
            "code_interpreter.jupyter.auth": "token",
            "code_interpreter.jupyter.auth_token": "isolated-token",
        }
        config.update(overrides)
        return config

    @staticmethod
    def _model():
        return {
            "info": {
                "meta": {
                    "capabilities": {CAPABILITY: True, "code_interpreter": True},
                    "builtinTools": {"code_interpreter": True},
                }
            }
        }

    def test_only_authenticated_jupyter_is_eligible(self):
        self.assertTrue(jupyter_python_enabled(self._jupyter_config()))
        self.assertFalse(jupyter_python_enabled(self._jupyter_config(**{"code_interpreter.engine": "pyodide"})))
        self.assertFalse(jupyter_python_enabled(self._jupyter_config(**{"code_interpreter.jupyter.auth_token": ""})))
        self.assertFalse(jupyter_python_enabled(self._jupyter_config(**{"code_interpreter.jupyter.auth": ""})))

    async def test_raw_multiline_code_delegates_to_the_existing_callable(self):
        seen = []

        async def execute_code(*, code):
            seen.append(code)
            return json.dumps({"status": "success", "stdout": "π=3.14", "stderr": "", "result": ""})

        tool = native_python_tools({"callable": execute_code}, max_output_bytes=1024)["python"]
        source = "import math\nprint('π=', round(math.pi, 2))"
        result = await tool["callable"](code=source)

        self.assertEqual(seen, [source])
        self.assertEqual(json.loads(result)["stdout"], "π=3.14")
        self.assertTrue(tool["native_harmony_hidden"])
        self.assertEqual(tool["spec"]["name"], "python")

    async def test_invalid_or_failed_calls_are_bounded_and_content_free_in_metrics(self):
        async def execute_code(*, code):
            raise RuntimeError("service unavailable")

        tool = native_python_tools({"callable": execute_code}, max_output_bytes=1024)["python"]
        with patch("open_webui.integrations.gpt_oss_harmony.python.emit_native_tool_event") as emit:
            self.assertIn("requires non-empty", await tool["callable"]())
            self.assertIn("Jupyter execution unavailable", await tool["callable"](code="print('TOP_SECRET')"))

        for call in emit.call_args_list:
            event = call.args[0]
            self.assertNotIn("TOP_SECRET", json.dumps(event))
            self.assertNotIn("code", event)
            self.assertIn("input_bytes", event)

    async def test_structured_output_is_truncated_without_breaking_json(self):
        async def execute_code(*, code):
            del code
            return json.dumps({"status": "success", "stdout": "x" * 2000, "stderr": "", "result": ""})

        result = await native_python_tools({"callable": execute_code}, max_output_bytes=512)["python"]["callable"](
            code="print('large')"
        )
        payload = json.loads(result)
        self.assertTrue(payload["truncated"])
        self.assertIn("stdout", payload)
        self.assertLessEqual(len(result.encode("utf-8")), 512)

    async def test_unavailable_jupyter_is_a_bounded_tool_error(self):
        async def execute_code(*, code):
            del code
            return json.dumps(
                {"status": "success", "stdout": "", "stderr": "Error: connection refused", "result": ""}
            )

        result = await native_python_tools({"callable": execute_code}, max_output_bytes=512)["python"]["callable"](
            code="print('hello')"
        )
        payload = json.loads(result)
        self.assertEqual(payload["error"], "Jupyter execution unavailable")
        self.assertLessEqual(len(result.encode("utf-8")), 512)

    async def test_python_syntax_failure_preserves_executor_stderr(self):
        async def execute_code(*, code):
            del code
            return json.dumps({"status": "success", "stdout": "", "stderr": "Traceback: SyntaxError", "result": ""})

        result = await native_python_tools({"callable": execute_code})["python"]["callable"](code="if:")
        payload = json.loads(result)
        self.assertNotIn("error", payload)
        self.assertEqual(payload["stderr"], "Traceback: SyntaxError")

    async def test_current_jupyter_header_messages_complete_the_existing_executor(self):
        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.messages = []

            async def send(self, message):
                self.sent.append(json.loads(message))
                message_id = self.sent[-1]["header"]["msg_id"]
                self.messages = [
                    json.dumps({"header": {"msg_type": "status"}, "parent_header": {"msg_id": message_id}, "content": {"execution_state": "busy"}}),
                    json.dumps({"header": {"msg_type": "stream"}, "parent_header": {"msg_id": message_id}, "content": {"name": "stdout", "text": "header-ok\n"}}),
                    json.dumps({"header": {"msg_type": "status"}, "parent_header": {"msg_id": message_id}, "content": {"execution_state": "idle"}}),
                ]

            async def recv(self):
                return self.messages.pop(0)

        executor = JupyterCodeExecuter("http://harmony-jupyter:8888", "print('header-ok')")
        try:
            await executor.execute_in_jupyter(FakeWebSocket())
            self.assertEqual(executor.result.stdout, "header-ok")
        finally:
            await executor.session.close()

    async def test_jupyter_token_uses_authorization_headers_not_urls(self):
        executor = JupyterCodeExecuter("http://harmony-jupyter:8888", "print('safe')", token="isolated-token")
        try:
            await executor.sign_in()
            websocket_url, headers = executor.init_ws()
            self.assertNotIn("isolated-token", websocket_url)
            self.assertEqual(headers["Authorization"], "token isolated-token")
            self.assertEqual(dict(executor.session.headers)["Authorization"], "token isolated-token")
        finally:
            await executor.session.close()

    async def test_native_model_replaces_execute_code_only_with_jupyter(self):
        async def wrapped_execute_code(*, code):
            return json.dumps({"status": "success", "stdout": code, "stderr": "", "result": ""})

        with patch(
            "open_webui.utils.tools.Config.get_many",
            new=AsyncMock(return_value=self._jupyter_config()),
        ), patch(
            "open_webui.utils.tools.has_permission", new=AsyncMock(return_value=True)
        ), patch(
            "open_webui.utils.tools.get_async_tool_function_and_apply_extra_params",
            new=AsyncMock(return_value=wrapped_execute_code),
        ):
            tools = await get_builtin_tools(
                SimpleNamespace(state=SimpleNamespace()),
                {"__user__": {"id": "user-1"}},
                {"code_interpreter": True},
                self._model(),
            )

        self.assertIn("python", tools)
        self.assertNotIn("execute_code", tools)

    async def test_native_model_never_uses_pyodide_as_python(self):
        config = self._jupyter_config(**{"code_interpreter.engine": "pyodide"})
        with patch("open_webui.utils.tools.Config.get_many", new=AsyncMock(return_value=config)), patch(
            "open_webui.utils.tools.has_permission", new=AsyncMock(return_value=True)
        ):
            tools = await get_builtin_tools(
                SimpleNamespace(state=SimpleNamespace()),
                {"__user__": {"id": "user-1"}},
                {"code_interpreter": True},
                self._model(),
            )

        self.assertNotIn("python", tools)
        self.assertNotIn("execute_code", tools)


if __name__ == "__main__":
    unittest.main()
