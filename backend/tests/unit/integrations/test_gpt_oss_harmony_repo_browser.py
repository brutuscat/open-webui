import inspect
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from open_webui.integrations.gpt_oss_harmony.detection import enable_native_namespaces
from open_webui.integrations.gpt_oss_harmony import repo_browser
from open_webui.integrations.gpt_oss_harmony.repo_browser import native_repo_browser_tools


class RepoBrowserTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _terminal_tools(runner, cwd="/repo"):
        return {"run_command": {"tool_id": "terminal:chosen", "callable": runner, "terminal_cwd": cwd}}

    async def test_tools_require_authorized_runner_and_explicit_root(self):
        async def runner(**kwargs):
            return {"status": "done", "exit_code": 0}

        self.assertEqual(native_repo_browser_tools({}, repository_root="/repo"), {})
        self.assertEqual(native_repo_browser_tools(self._terminal_tools(runner), repository_root=None), {})
        self.assertEqual(native_repo_browser_tools(self._terminal_tools(runner), repository_root="relative"), {})
        self.assertEqual(native_repo_browser_tools(self._terminal_tools(runner, cwd="/other"), repository_root="/repo"), {})

    async def test_adapter_has_no_openwebui_subprocess_or_filesystem_execution_path(self):
        source = inspect.getsource(repo_browser)

        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("os.popen", source)
        self.assertNotIn("from pathlib import Path", source)

    async def test_runtime_configuration_requires_the_selected_terminal_workspace(self):
        async def runner(**kwargs):
            return {"status": "done", "exit_code": 0}

        with patch.dict(
            os.environ,
            {"HARMONY_REPO_BROWSER_ROOT": "/repo", "HARMONY_REPO_BROWSER_APPLY_PATCH": "true"},
            clear=False,
        ):
            tools = native_repo_browser_tools(self._terminal_tools(runner), repository_root=None)
        self.assertIn("repo_browser.apply_patch", tools)

    async def test_full_reference_surface_and_template_namespace(self):
        async def runner(**kwargs):
            return {"status": "done", "exit_code": 0}

        tools = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo", apply_patch_enabled=True)
        self.assertEqual(
            set(tools),
            {
                "repo_browser.print_tree",
                "repo_browser.search",
                "repo_browser.open_file",
                "repo_browser.list_dir",
                "repo_browser.apply_patch",
                "apply_patch",
            },
        )
        self.assertEqual(tools["repo_browser.print_tree"]["spec"]["parameters"]["required"], ["path", "depth"])
        self.assertEqual(tools["repo_browser.search"]["spec"]["parameters"]["required"], ["path", "query"])
        self.assertEqual(tools["repo_browser.open_file"]["spec"]["parameters"]["required"], ["path"])
        self.assertEqual(tools["repo_browser.list_dir"]["spec"]["parameters"]["required"], ["path"])
        self.assertTrue(
            all(
                tools[name]["spec"]["parameters"].get("additionalProperties") is False
                for name in tools
                if name not in {"repo_browser.apply_patch", "apply_patch"}
            )
        )
        self.assertEqual(
            tools["repo_browser.open_file"]["spec"]["parameters"]["properties"]["line_numbers"]["type"],
            "boolean",
        )
        self.assertNotIn("required", tools["repo_browser.apply_patch"]["spec"]["parameters"])
        self.assertNotIn("additionalProperties", tools["repo_browser.apply_patch"]["spec"]["parameters"])
        self.assertIn("*** Begin Patch", tools["repo_browser.apply_patch"]["spec"]["parameters"]["properties"]["patch"]["default"])
        self.assertEqual(tools["apply_patch"]["spec"]["name"], "apply_patch")
        self.assertIs(tools["apply_patch"]["callable"], tools["repo_browser.apply_patch"]["callable"])
        self.assertTrue(all(tool["native_harmony_hidden"] for tool in tools.values()))

        form_data = {"chat_template_kwargs": {"reasoning_effort": "medium"}}
        enable_native_namespaces(form_data, {"browser", "container", "repo_browser"})
        self.assertEqual(
            form_data["chat_template_kwargs"]["builtin_tools"],
            ["browser", "container", "repo_browser"],
        )

    async def test_read_only_surface_omits_apply_patch_when_helper_is_unavailable(self):
        async def runner(**kwargs):
            return {"status": "done", "exit_code": 0}

        tools = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo", apply_patch_enabled=False)
        self.assertNotIn("repo_browser.apply_patch", tools)
        self.assertEqual(len(tools), 4)

    async def test_all_read_operations_delegate_only_to_selected_terminal_runner(self):
        seen = []

        async def runner(**kwargs):
            seen.append(kwargs)
            return {"status": "done", "exit_code": 0, "output": ["ok"]}

        tools = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo")
        await tools["repo_browser.print_tree"]["callable"](path="src", depth=3)
        await tools["repo_browser.search"]["callable"](path="src", query="TODO", max_results=5)
        await tools["repo_browser.open_file"]["callable"](path="src/app.py", line_start=2, line_end=4, line_numbers=True)
        await tools["repo_browser.list_dir"]["callable"](path="", depth=1)

        self.assertEqual(len(seen), 4)
        self.assertTrue(all(call["wait"] == 30 and call["tail"] == 200 for call in seen))
        self.assertIn("find -- src -maxdepth 3 -print", seen[0]["command"])
        self.assertIn("grep -rIn", seen[1]["command"])
        self.assertNotIn("grep -RIn", seen[1]["command"])
        self.assertIn("sed -n 2,4p -- src/app.py | nl -ba -v 2", seen[2]["command"])
        self.assertIn("find -- . -maxdepth 1 -print", seen[3]["command"])
        self.assertTrue(all("/repo" in call["command"] for call in seen))
        self.assertTrue(all('root_real="$(realpath -e -- .)"' in call["command"] for call in seen))
        self.assertTrue(all('target_real="$(realpath -e -- ' in call["command"] for call in seen))
        self.assertTrue(all("repository path escapes the selected root" in call["command"] for call in seen))

    async def test_open_file_tolerates_observed_argument_aliases_without_advertising_them(self):
        seen = []

        async def runner(**kwargs):
            seen.append(kwargs)
            return {"status": "done", "exit_code": 0}

        tool = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo")["repo_browser.open_file"]
        await tool["callable"](file_path="README.md", start_line=4, end_line=6)

        self.assertEqual(tool["spec"]["parameters"]["required"], ["path"])
        self.assertIn("sed -n 4,6p -- README.md", seen[0]["command"])

    async def test_path_traversal_and_absolute_paths_are_rejected_before_terminal_execution(self):
        seen = []

        async def runner(**kwargs):
            seen.append(kwargs)
            return {"status": "done", "exit_code": 0}

        tools = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo", apply_patch_enabled=True)
        rejected = [
            await tools["repo_browser.print_tree"]["callable"](path="../outside", depth=1),
            await tools["repo_browser.search"]["callable"](path="/etc", query="passwd"),
            await tools["repo_browser.open_file"]["callable"](path="../secret"),
            await tools["repo_browser.list_dir"]["callable"](path=".."),
            await tools["repo_browser.apply_patch"]["callable"](
                patch="*** Begin Patch\n*** Add File: ../outside.txt\n+blocked\n*** End Patch\n"
            ),
            await tools["apply_patch"]["callable"](
                patch="*** Begin Patch\n*** Add File: ../outside-alias.txt\n+blocked\n*** End Patch\n"
            ),
        ]

        self.assertEqual(seen, [])
        self.assertTrue(all("error" in json.loads(result) for result in rejected))

    async def test_open_file_line_range_is_bounded(self):
        seen = []

        async def runner(**kwargs):
            seen.append(kwargs)
            return {"status": "done", "exit_code": 0}

        tool = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo")["repo_browser.open_file"]
        result = await tool["callable"](path="README.md", line_start=1, line_end=1000)

        self.assertEqual(seen, [])
        self.assertIn("line range may not exceed", json.loads(result)["error"])

    async def test_apply_patch_uses_encoded_input_and_preserves_terminal_response_headers(self):
        terminal_response = ({"status": "done", "exit_code": 0, "output": ["Done!"]}, {"content-type": "application/json"})
        seen = []

        async def runner(**kwargs):
            seen.append(kwargs)
            return terminal_response

        patch_text = "*** Begin Patch\n*** Add File: created.txt\n+created\n*** End Patch\n"
        tool = native_repo_browser_tools(
            self._terminal_tools(runner), repository_root="/repo", apply_patch_enabled=True
        )["repo_browser.apply_patch"]
        self.assertIs(await tool["callable"](patch=patch_text), terminal_response)

        self.assertIn("base64 -d | apply_patch", seen[0]["command"])
        self.assertNotIn("created.txt", seen[0]["command"])
        self.assertNotIn("+created", seen[0]["command"])

    async def test_metrics_are_content_free_and_preserve_wait_state(self):
        async def runner(**kwargs):
            return ({"status": "running", "truncated": True, "output": ["secret"]}, {"content-type": "application/json"})

        tool = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo")["repo_browser.search"]
        with patch("open_webui.integrations.gpt_oss_harmony.repo_browser.emit_native_tool_event") as emit:
            await tool["callable"](path="", query="TOP_SECRET")

        event = emit.call_args.args[0]
        self.assertEqual(event["namespace"], "repo_browser")
        self.assertEqual(event["tool"], "search")
        self.assertFalse(event["success"])
        self.assertTrue(event["wait_expired"])
        self.assertTrue(event["output_truncated"])
        self.assertNotIn("command", event)
        self.assertNotIn("output", event)
        self.assertNotIn("TOP_SECRET", json.dumps(event))

    async def test_runner_falls_back_for_older_terminal_schemas(self):
        seen = []

        async def runner(command):
            seen.append(command)
            return {"status": "done", "exit_code": 0}

        tool = native_repo_browser_tools(self._terminal_tools(runner), repository_root="/repo")["repo_browser.list_dir"]
        await tool["callable"](path="src")

        self.assertEqual(len(seen), 1)
        self.assertIn("find -- src", seen[0])
