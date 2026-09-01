"""Ensure P1 receives the workspace selected by the authorized terminal."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from open_webui.utils.tools import get_terminal_tools


class TerminalWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_terminal_cwd_stays_private_to_native_adapter_metadata(self):
        connection = {"id": "chosen", "url": "http://terminal.invalid", "enabled": True, "auth_type": "none"}
        server = {
            "id": "chosen",
            "url": "http://terminal.invalid",
            "specs": [
                {
                    "name": "run_command",
                    "description": "Run a command.",
                    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                }
            ],
        }

        async def passthrough(callable, _extra_params):
            return callable

        with (
            patch("open_webui.utils.tools.Config.get", new=AsyncMock(return_value=[connection])),
            patch("open_webui.utils.tools.Groups.get_groups_by_member_id", new=AsyncMock(return_value=[])),
            patch("open_webui.utils.tools.has_connection_access", new=AsyncMock(return_value=True)),
            patch("open_webui.utils.tools.get_terminal_servers", new=AsyncMock(return_value=[server])),
            patch("open_webui.utils.tools.get_terminal_cwd", new=AsyncMock(return_value="/workspace/repo")),
            patch("open_webui.utils.tools.get_terminal_system_prompt", new=AsyncMock(return_value=None)),
            patch(
                "open_webui.utils.tools.get_async_tool_function_and_apply_extra_params",
                new=AsyncMock(side_effect=passthrough),
            ),
        ):
            tools, _ = await get_terminal_tools(
                request=SimpleNamespace(),
                terminal_id="chosen",
                user=SimpleNamespace(id="user-1"),
                extra_params={"__metadata__": {}},
            )

        run_command = tools["run_command"]
        self.assertEqual(run_command["terminal_cwd"], "/workspace/repo")
        self.assertIn("current working directory is: /workspace/repo", run_command["spec"]["description"])
        self.assertNotIn("terminal_cwd", run_command["spec"])


if __name__ == "__main__":
    unittest.main()
