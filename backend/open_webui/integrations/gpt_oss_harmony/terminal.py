"""Native container adapter backed exclusively by OpenWebUI terminal tools."""

from __future__ import annotations

import shlex
import time
from typing import Any

from open_webui.utils.json_codec import JSONCodec

from .metrics import emit_native_tool_event

EXEC_WAIT_SECONDS = 30
EXEC_OUTPUT_TAIL = 200


def _result_metadata(result: Any) -> tuple[bool, int | None, bool, bool, bool, int]:
    """Extract safe execution metadata without retaining terminal output."""
    payload = result[0] if isinstance(result, tuple) and result else result
    if isinstance(payload, str):
        try:
            payload = JSONCodec.loads(payload)
        except JSONCodec.JSONDecodeError:
            return True, None, False, False, False, len(payload.encode("utf-8"))

    if not isinstance(payload, dict):
        return True, None, False, False, False, len(str(payload).encode("utf-8"))

    exit_code = payload.get("exit_code", payload.get("exitCode"))
    if not isinstance(exit_code, int):
        exit_code = None
    timed_out = bool(payload.get("timed_out") or payload.get("timedOut"))
    status = str(payload.get("status", "")).lower()
    timed_out = timed_out or status in {"timeout", "timed_out"}
    wait_expired = status == "running"
    success = exit_code == 0 if exit_code is not None else not timed_out and not wait_expired and not payload.get("error")
    return (
        success,
        exit_code,
        timed_out,
        wait_expired,
        bool(payload.get("truncated")),
        len(JSONCodec.dumps(payload).encode("utf-8")),
    )


def native_container_tools(terminal_tools: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return `container.exec` only when the selected terminal exposes run_command."""
    runner = terminal_tools.get("run_command")
    if runner is None:
        return {}

    async def exec(
        command: list[str] | str | None = None,
        cmd: list[str] | str | None = None,
        commands: list[str] | str | None = None,
    ) -> Any:
        started = time.perf_counter()
        supplied = command if command is not None else cmd if cmd is not None else commands
        input_bytes = len(str(supplied).encode("utf-8")) if supplied is not None else 0
        backend_started: float | None = None
        result: Any = None
        failure: Exception | None = None

        try:
            if supplied is None:
                result = JSONCodec.dumps({"error": "container.exec requires command"})
                return result
            if isinstance(supplied, list):
                if not supplied or not all(isinstance(part, str) for part in supplied):
                    result = JSONCodec.dumps({"error": "command must be a non-empty string array"})
                    return result
                command_text = shlex.join(supplied)
            elif isinstance(supplied, str):
                command_text = supplied
            else:
                result = JSONCodec.dumps({"error": "command must be a string array"})
                return result

            backend_started = time.perf_counter()
            try:
                # Open Terminal's bounded wait returns stdout, stderr, exit code,
                # and truncation metadata in the same native tool result for normal
                # commands. The compatibility fallback preserves older terminals.
                result = await runner["callable"](
                    command=command_text,
                    wait=EXEC_WAIT_SECONDS,
                    tail=EXEC_OUTPUT_TAIL,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                result = await runner["callable"](command=command_text)
            # OpenWebUI terminal callables return ``(body, response_headers)``.
            # Keep that established transport shape so middleware can process the
            # headers (for files, embeds, and output) without serializing aiohttp's
            # CIMultiDictProxy response object.
            if isinstance(result, tuple) and len(result) == 2:
                return result
            return result if isinstance(result, str) else JSONCodec.dumps(result)
        except Exception as exc:
            failure = exc
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            backend_ms = round((time.perf_counter() - backend_started) * 1000, 3) if backend_started else 0.0
            if result is not None:
                success, exit_code, timed_out, wait_expired, output_truncated, output_bytes = _result_metadata(result)
                emit_native_tool_event(
                    {
                        "namespace": "container",
                        "tool": "exec",
                        "success": success,
                        "latency_ms": elapsed_ms,
                        "adapter_ms": round(max(elapsed_ms - backend_ms, 0.0), 3),
                        "backend_ms": backend_ms,
                        "input_bytes": input_bytes,
                        "output_bytes": output_bytes,
                        "exit_code": exit_code,
                        "timed_out": timed_out,
                        "wait_expired": wait_expired,
                        "output_truncated": output_truncated,
                    }
                )
            elif failure is not None:
                emit_native_tool_event(
                    {
                        "namespace": "container",
                        "tool": "exec",
                        "success": False,
                        "latency_ms": elapsed_ms,
                        "adapter_ms": round(max(elapsed_ms - backend_ms, 0.0), 3),
                        "backend_ms": backend_ms,
                        "input_bytes": input_bytes,
                        "output_bytes": 0,
                        "error_type": type(failure).__name__,
                    }
                )

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
