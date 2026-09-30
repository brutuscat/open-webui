"""Native browser tool specs and dispatcher-facing callables."""

from __future__ import annotations

from typing import Any

from .browser import get_browser


def native_browser_tools(request: Any, user: dict[str, Any]) -> dict[str, dict[str, Any]]:
    browser = get_browser(request, user)

    return {
        "browser.search": {
            "tool_id": "builtin:browser.search",
            "callable": browser.search,
            "spec": {
                "name": "browser.search",
                "description": "Search the configured OpenWebUI web provider.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "topn": {"type": "integer"}, "source": {"type": "string"}}, "required": ["query"]},
            },
            "type": "builtin",
            "native_harmony_hidden": True,
        },
        "browser.open": {
            "tool_id": "builtin:browser.open",
            "callable": browser.open,
            "spec": {
                "name": "browser.open",
                "description": "Open or scroll a browser cursor.",
                "parameters": {"type": "object", "properties": {"cursor": {"type": "integer"}, "id": {"type": ["integer", "string"]}, "loc": {"type": "integer"}, "num_lines": {"type": "integer"}, "view_source": {"type": "boolean"}, "source": {"type": "string"}}},
            },
            "type": "builtin",
            "native_harmony_hidden": True,
        },
        "browser.find": {
            "tool_id": "builtin:browser.find",
            "callable": browser.find,
            "spec": {
                "name": "browser.find",
                "description": "Find text in a browser cursor.",
                "parameters": {"type": "object", "properties": {"cursor": {"type": "integer"}, "pattern": {"type": "string"}}, "required": ["pattern"]},
            },
            "type": "builtin",
            "native_harmony_hidden": True,
        },
    }
