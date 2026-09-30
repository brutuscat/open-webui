"""Native GPT-OSS ``python`` backed by OpenWebUI's Jupyter executor."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from open_webui.utils.json_codec import JSONCodec

from .metrics import emit_native_tool_event


DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024


def jupyter_python_enabled(config: Mapping[str, Any]) -> bool:
    """Expose native Python only for the authenticated server-side Jupyter path."""
    return (
        config.get("code_interpreter.engine") == "jupyter"
        and isinstance(config.get("code_interpreter.jupyter.url"), str)
        and bool(config.get("code_interpreter.jupyter.url").strip())
        and config.get("code_interpreter.jupyter.auth") == "token"
        and isinstance(config.get("code_interpreter.jupyter.auth_token"), str)
        and bool(config.get("code_interpreter.jupyter.auth_token").strip())
    )


def _configured_output_limit() -> int:
    raw = os.getenv("HARMONY_PYTHON_MAX_OUTPUT_BYTES", str(DEFAULT_MAX_OUTPUT_BYTES))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_OUTPUT_BYTES
    return value if value > 0 else DEFAULT_MAX_OUTPUT_BYTES


def _bounded_result(result: Any, maximum: int) -> str:
    """Keep a structured OpenWebUI result under the isolated tool-output cap."""
    rendered = result if isinstance(result, str) else JSONCodec.dumps(result, ensure_ascii=False)
    if len(rendered.encode("utf-8")) <= maximum:
        return rendered

    try:
        payload = JSONCodec.loads(rendered)
    except JSONCodec.JSONDecodeError:
        return JSONCodec.dumps(
            {"error": "python output exceeded the configured result limit", "truncated": True},
            ensure_ascii=False,
        )

    if not isinstance(payload, dict):
        return JSONCodec.dumps(
            {"error": "python output exceeded the configured result limit", "truncated": True},
            ensure_ascii=False,
        )

    bounded: dict[str, Any] = {
        key: value for key, value in payload.items() if key not in {"stdout", "stderr", "result"}
    }
    bounded["truncated"] = True
    suffix = "\n…[truncated]"

    for key in ("stdout", "stderr", "result"):
        value = payload.get(key, "")
        if not isinstance(value, str):
            value = str(value)
        bounded[key] = value
        rendered_bounded = JSONCodec.dumps(bounded, ensure_ascii=False)
        if len(rendered_bounded.encode("utf-8")) <= maximum:
            continue

        marker_bytes = len(suffix.encode("utf-8"))
        current_bytes = len(value.encode("utf-8"))
        while current_bytes > 0 and len(rendered_bounded.encode("utf-8")) > maximum:
            overflow = len(rendered_bounded.encode("utf-8")) - maximum
            current_bytes = max(0, current_bytes - overflow - marker_bytes)
            shortened = value.encode("utf-8")[:current_bytes].decode("utf-8", "ignore")
            bounded[key] = shortened + suffix if shortened else suffix
            rendered_bounded = JSONCodec.dumps(bounded, ensure_ascii=False)

    rendered_bounded = JSONCodec.dumps(bounded, ensure_ascii=False)
    if len(rendered_bounded.encode("utf-8")) <= maximum:
        return rendered_bounded

    # Empty later fields can add just enough JSON overhead to cross the cap.
    # Trim the retained text fields once more, preserving a valid structured
    # result and its explicit truncation marker.
    for key in ("result", "stderr", "stdout"):
        value = bounded.get(key)
        while isinstance(value, str) and value and len(rendered_bounded.encode("utf-8")) > maximum:
            overflow = len(rendered_bounded.encode("utf-8")) - maximum
            shortened = value.encode("utf-8")[: max(0, len(value.encode("utf-8")) - overflow)].decode(
                "utf-8", "ignore"
            )
            if shortened == value:
                shortened = ""
            bounded[key] = shortened
            value = shortened
            rendered_bounded = JSONCodec.dumps(bounded, ensure_ascii=False)
        if len(rendered_bounded.encode("utf-8")) <= maximum:
            return rendered_bounded

    # The structured fallback is intentionally small enough for the deployed
    # 1 MiB limit and prevents an oversized original result from leaking past
    # the adapter boundary.
    fallback = JSONCodec.dumps(
        {"error": "python output exceeded the configured result limit", "truncated": True},
        ensure_ascii=False,
    )
    return fallback if len(fallback.encode("utf-8")) <= maximum else fallback[:maximum]


def _normalize_jupyter_unavailable(result: Any) -> Any:
    """Turn the builtin's connection-error convention into a tool error.

    ``execute_code`` catches backend connection failures and reports them in
    ``stderr`` with an ``Error:`` prefix. Python exceptions from a running
    kernel instead contain a traceback and remain ordinary execution results.
    """
    rendered = result if isinstance(result, str) else JSONCodec.dumps(result, ensure_ascii=False)
    try:
        payload = JSONCodec.loads(rendered)
    except JSONCodec.JSONDecodeError:
        return result

    if not isinstance(payload, dict):
        return result
    stderr = payload.get("stderr")
    if not isinstance(stderr, str) or not stderr.lstrip().startswith("Error:"):
        return result

    return {
        "error": "Jupyter execution unavailable",
        "stderr": stderr,
        "stdout": payload.get("stdout", ""),
        "result": payload.get("result", ""),
    }


def _result_success(result: str) -> bool:
    try:
        payload = JSONCodec.loads(result)
    except JSONCodec.JSONDecodeError:
        return False
    return isinstance(payload, dict) and not payload.get("error")


def native_python_tools(execute_code_tool: dict[str, Any], max_output_bytes: int | None = None) -> dict[str, dict[str, Any]]:
    """Rename OpenWebUI's injected ``execute_code`` callable to native ``python``."""
    callable_ = execute_code_tool.get("callable")
    if callable_ is None:
        return {}

    output_limit = max_output_bytes if max_output_bytes is not None else _configured_output_limit()

    async def python(code: str | None = None) -> str:
        started = time.perf_counter()
        source_bytes = len(code.encode("utf-8")) if isinstance(code, str) else 0
        result: str | None = None
        failure: Exception | None = None
        try:
            if not isinstance(code, str) or not code.strip():
                result = JSONCodec.dumps({"error": "python requires non-empty code"}, ensure_ascii=False)
                return result

            # The wrapped builtin supplies request/user/event context and invokes
            # OpenWebUI's configured Jupyter engine.  This adapter never shells
            # out or creates a competing execution path.
            result = _bounded_result(_normalize_jupyter_unavailable(await callable_(code=code)), output_limit)
            return result
        except Exception as exc:
            failure = exc
            result = JSONCodec.dumps({"error": f"Jupyter execution unavailable: {exc}"}, ensure_ascii=False)
            return result
        finally:
            event: dict[str, Any] = {
                "namespace": "python",
                "tool": "python",
                "success": bool(result and _result_success(result)),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "input_bytes": source_bytes,
                "output_bytes": len(result.encode("utf-8")) if result else 0,
            }
            if failure is not None:
                event["error_type"] = type(failure).__name__
            emit_native_tool_event(event)

    return {
        "python": {
            "tool_id": "builtin:python",
            "callable": python,
            "spec": {
                "name": "python",
                "description": "Execute stateless Python through the isolated Jupyter service. Use print for visible output.",
                "parameters": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                    "additionalProperties": False,
                },
            },
            "type": "builtin",
            "native_harmony_hidden": True,
        }
    }
