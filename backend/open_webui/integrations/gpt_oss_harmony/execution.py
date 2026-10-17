"""Caller-owned guidance for independent Jupyter and terminal executors."""

from collections.abc import Mapping
from typing import Any

from .detection import is_native_harmony_model


def native_executor_guidance(model: dict[str, Any], tools: Mapping[str, dict[str, Any]]) -> str | None:
    python = tools.get("python", {})
    terminal = tools.get("container.exec", {})
    if (
        not is_native_harmony_model(model)
        or python.get("tool_id") != "builtin:python"
        or python.get("native_harmony_hidden") is not True
        or terminal.get("type") != "terminal"
        or terminal.get("native_harmony_hidden") is not True
    ):
        return None
    return (
        "Execution tools: both python and container.exec are available in this conversation. "
        "The native python tool executes code in Jupyter; container.exec executes shell commands "
        "in a separate terminal environment. For Python calculations and data analysis, call python "
        "by default. Use container.exec when the user explicitly requests the terminal, shell commands, "
        "or terminal files. Honor an explicit tool choice: do not substitute shell Python for a "
        "requested python/Jupyter call, or Jupyter for a requested terminal command. Execute the "
        "requested tool before reporting its result."
    )
