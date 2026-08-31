"""Native container adapter backed exclusively by OpenWebUI terminal tools."""

from __future__ import annotations

import shlex
from typing import Any

from open_webui.utils.json_codec import JSONCodec


def native_container_tools(terminal_tools: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return `container.exec` only when the selected terminal exposes run_command."""
    runner = terminal_tools.get("run_command")
    if runner is None:
        return {}

    async def exec(
        command: list[str] | str | None = None,
        cmd: list[str] | str | None = None,
        commands: list[str] | str | None = None,
    ) -> str:
        supplied = command if command is not None else cmd if cmd is not None else commands
        if supplied is None:
            return JSONCodec.dumps({"error": "container.exec requires command"})
        if isinstance(supplied, list):
            if not supplied or not all(isinstance(part, str) for part in supplied):
                return JSONCodec.dumps({"error": "command must be a non-empty string array"})
            command_text = shlex.join(supplied)
        elif isinstance(supplied, str):
            command_text = supplied
        else:
            return JSONCodec.dumps({"error": "command must be a string array"})

        result = await runner["callable"](command=command_text)
        return result if isinstance(result, str) else JSONCodec.dumps(result)

    return {
        "container.exec": {
            "tool_id": runner.get("tool_id", "terminal:unknown"),
            "callable": exec,
            "spec": {
                "name": "container.exec",
                "description": "Execute a command through the selected authorized Open Terminal session.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                    "required": ["command"],
                },
            },
            "type": "terminal",
            "native_harmony_hidden": True,
        }
    }
