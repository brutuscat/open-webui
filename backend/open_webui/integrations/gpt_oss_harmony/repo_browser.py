"""Native repo-browser tools backed exclusively by the selected terminal."""

from __future__ import annotations

import base64
import os
import re
import shlex
import time
from pathlib import PurePosixPath
from typing import Any

from open_webui.utils.json_codec import JSONCodec

from .metrics import emit_native_tool_event
from .terminal import EXEC_OUTPUT_TAIL, EXEC_WAIT_SECONDS, _result_metadata


MAX_DEPTH = 8
MAX_RESULTS = 100
MAX_LINE_SPAN = 500
MAX_PATCH_BYTES = 128 * 1024
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (?P<path>.+)$", re.MULTILINE)


def _configured_root() -> str | None:
    value = os.getenv("HARMONY_REPO_BROWSER_ROOT", "").strip()
    return value or None


def _configured_apply_patch() -> bool:
    return os.getenv("HARMONY_REPO_BROWSER_APPLY_PATCH", "").strip().lower() in {"1", "true", "yes"}


def _repository_root(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    root = PurePosixPath(value.strip())
    if not root.is_absolute() or ".." in root.parts or "\x00" in value:
        return None
    return root.as_posix()


def _selected_workspace(runner: dict[str, Any]) -> str | None:
    """Return the current workspace reported by the authorized terminal only."""
    return _repository_root(runner.get("terminal_cwd"))


def _relative_path(value: str | None, field: str = "path") -> str:
    if value is None:
        return "."
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    candidate = value.strip()
    if not candidate or candidate == ".":
        return "."
    if "\x00" in candidate or "\\" in candidate:
        raise ValueError(f"{field} must be a root-relative POSIX path")
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must stay inside the selected repository root")
    return path.as_posix()


def _bounded_int(value: Any, field: str, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{field} must be an integer")
    result = int(value)
    if result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _bash(repository_root: str, body: str) -> str:
    script = f"set -euo pipefail\ncd -- {shlex.quote(repository_root)}\n{body}"
    return f"bash -lc {shlex.quote(script)}"


def _safe_patch(patch: Any) -> str:
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError("patch must be a non-empty string")
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ValueError(f"patch exceeds the {MAX_PATCH_BYTES}-byte limit")
    if not patch.startswith("*** Begin Patch\n") or not patch.rstrip().endswith("*** End Patch"):
        raise ValueError("patch must use the *** Begin Patch / *** End Patch format")
    paths = _PATCH_PATH.findall(patch)
    if not paths:
        raise ValueError("patch must contain at least one file operation")
    for path in paths:
        _relative_path(path, "patch path")
    return patch


async def _runner_result(runner: dict[str, Any], command: str) -> Any:
    try:
        result = await runner["callable"](
            command=command,
            wait=EXEC_WAIT_SECONDS,
            tail=EXEC_OUTPUT_TAIL,
        )
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        result = await runner["callable"](command=command)
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return result if isinstance(result, str) else JSONCodec.dumps(result)


def _error_result(tool: str, error: Exception, input_bytes: int = 0) -> str:
    emit_native_tool_event(
        {
            "namespace": "repo_browser",
            "tool": tool,
            "success": False,
            "latency_ms": 0.0,
            "adapter_ms": 0.0,
            "backend_ms": 0.0,
            "input_bytes": input_bytes,
            "output_bytes": 0,
            "error_type": type(error).__name__,
        }
    )
    return JSONCodec.dumps({"error": str(error)})


async def _execute(runner: dict[str, Any], tool: str, command: str, input_bytes: int) -> Any:
    started = time.perf_counter()
    backend_started: float | None = None
    result: Any = None
    failure: Exception | None = None
    try:
        backend_started = time.perf_counter()
        result = await _runner_result(runner, command)
        return result
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
                    "namespace": "repo_browser",
                    "tool": tool,
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
                    "namespace": "repo_browser",
                    "tool": tool,
                    "success": False,
                    "latency_ms": elapsed_ms,
                    "adapter_ms": round(max(elapsed_ms - backend_ms, 0.0), 3),
                    "backend_ms": backend_ms,
                    "input_bytes": input_bytes,
                    "output_bytes": 0,
                    "error_type": type(failure).__name__,
                }
            )


def _entry(runner: dict[str, Any], name: str, description: str, parameters: dict[str, Any], callable: Any) -> dict[str, Any]:
    return {
        "tool_id": runner.get("tool_id", "terminal:unknown"),
        "callable": callable,
        "spec": {"name": name, "description": description, "parameters": parameters},
        "type": "terminal",
        "native_harmony_hidden": True,
    }


def native_repo_browser_tools(
    terminal_tools: dict[str, dict[str, Any]],
    repository_root: str | None = None,
    apply_patch_enabled: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Return native repo tools only for an authorized runner and explicit root."""
    runner = terminal_tools.get("run_command")
    root = _repository_root(repository_root if repository_root is not None else _configured_root())
    workspace = _selected_workspace(runner) if runner is not None else None
    # A static allow-list alone is not enough: the same configured path must be
    # the workspace currently selected by this authorized terminal connection.
    # This keeps the adapter from widening a terminal session's existing scope.
    if runner is None or root is None or workspace != root:
        return {}
    patch_enabled = _configured_apply_patch() if apply_patch_enabled is None else apply_patch_enabled

    async def print_tree(path: str | None = None, depth: int | float | None = None) -> Any:
        try:
            target = _relative_path(path)
            max_depth = _bounded_int(depth, "depth", 3, 0, MAX_DEPTH)
        except ValueError as exc:
            return _error_result("print_tree", exc, len(str(path).encode("utf-8")))
        command = _bash(root, f"find -- {shlex.quote(target)} -maxdepth {max_depth} -print | LC_ALL=C sort")
        return await _execute(runner, "print_tree", command, len(str(path).encode("utf-8")))

    async def search(path: str | None = None, query: str | None = None, max_results: int | float | None = None) -> Any:
        try:
            target = _relative_path(path)
            if not isinstance(query, str) or not query:
                raise ValueError("query must be a non-empty string")
            limit = _bounded_int(max_results, "max_results", 20, 1, MAX_RESULTS)
        except ValueError as exc:
            return _error_result("search", exc, len(str(query).encode("utf-8")))
        body = "\n".join(
            [
                'result_file="$(mktemp)"',
                'trap \'rm -f "$result_file"\' EXIT',
                f"if grep -RIn --binary-files=without-match --exclude-dir=.git -- {shlex.quote(query)} {shlex.quote(target)} >\"$result_file\"; then",
                "  :",
                "else",
                "  status=$?",
                '  if [ "$status" -ne 1 ]; then cat "$result_file"; exit "$status"; fi',
                "fi",
                f"head -n {limit} \"$result_file\"",
            ]
        )
        command = _bash(root, body)
        return await _execute(runner, "search", command, len((path or "").encode("utf-8")) + len(query.encode("utf-8")))

    async def open_file(
        path: str | None = None,
        line_start: int | float | None = None,
        line_end: int | float | None = None,
        line_numbers: bool = False,
        file_path: str | None = None,
        start_line: int | float | None = None,
        end_line: int | float | None = None,
    ) -> Any:
        try:
            target = _relative_path(path if path is not None else file_path)
            if target == ".":
                raise ValueError("path is required")
            start = _bounded_int(line_start if line_start is not None else start_line, "line_start", 1, 1, 1_000_000)
            end = _bounded_int(line_end if line_end is not None else end_line, "line_end", start + MAX_LINE_SPAN - 1, start, 1_000_000)
            if end - start + 1 > MAX_LINE_SPAN:
                raise ValueError(f"line range may not exceed {MAX_LINE_SPAN} lines")
            if not isinstance(line_numbers, bool):
                raise ValueError("line_numbers must be a boolean")
        except ValueError as exc:
            selected_path = path if path is not None else file_path
            return _error_result("open_file", exc, len(str(selected_path).encode("utf-8")))
        body = f"sed -n {shlex.quote(f'{start},{end}p')} -- {shlex.quote(target)}"
        if line_numbers:
            body += f" | nl -ba -v {start}"
        command = _bash(root, body)
        return await _execute(runner, "open_file", command, len(target.encode("utf-8")))

    async def list_dir(path: str | None = None, depth: int | float | None = None) -> Any:
        try:
            target = _relative_path(path)
            max_depth = _bounded_int(depth, "depth", 1, 0, MAX_DEPTH)
        except ValueError as exc:
            return _error_result("list_dir", exc, len(str(path).encode("utf-8")))
        command = _bash(root, f"find -- {shlex.quote(target)} -maxdepth {max_depth} -print | LC_ALL=C sort")
        return await _execute(runner, "list_dir", command, len(str(path).encode("utf-8")))

    tools = {
        "repo_browser.print_tree": _entry(
            runner,
            "repo_browser.print_tree",
            "Print a tree view of the repository file structure.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative path; use an empty string for repository root."},
                    "depth": {"type": "number", "description": "Maximum directory depth to display."},
                },
                "required": ["path", "depth"],
                "additionalProperties": False,
            },
            print_tree,
        ),
        "repo_browser.search": _entry(
            runner,
            "repo_browser.search",
            "Search repository file contents for a query string.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative directory; use an empty string for repository root."},
                    "query": {"type": "string", "description": "Search query or pattern."},
                    "max_results": {"type": "number", "description": "Maximum matching lines to return."},
                },
                "required": ["path", "query"],
                "additionalProperties": False,
            },
            search,
        ),
        "repo_browser.open_file": _entry(
            runner,
            "repo_browser.open_file",
            "Open a repository file within a bounded line range.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative path to the file."},
                    "line_start": {"type": "number", "description": "First 1-indexed line to display."},
                    "line_end": {"type": "number", "description": "Last 1-indexed line to display."},
                    "line_numbers": {"type": "boolean", "description": "Prefix output lines with their file line numbers."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            open_file,
        ),
        "repo_browser.list_dir": _entry(
            runner,
            "repo_browser.list_dir",
            "List repository directory entries with optional depth control.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative directory; use an empty string for repository root."},
                    "depth": {"type": "number", "description": "Maximum directory depth to traverse."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            list_dir,
        ),
    }

    if patch_enabled:
        async def apply_patch(patch: str | None = None) -> Any:
            try:
                safe_patch = _safe_patch(patch)
            except ValueError as exc:
                return _error_result("apply_patch", exc, len(str(patch).encode("utf-8")))
            encoded_patch = base64.b64encode(safe_patch.encode("utf-8")).decode("ascii")
            command = _bash(root, f"printf %s {shlex.quote(encoded_patch)} | base64 -d | apply_patch")
            return await _execute(runner, "apply_patch", command, len(safe_patch.encode("utf-8")))

        patch_entry = _entry(
            runner,
            "repo_browser.apply_patch",
            "Patch a file",
            {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "Formatted patch code",
                        "default": "*** Begin Patch\n*** End Patch\n",
                    }
                },
            },
            apply_patch,
        )
        tools["repo_browser.apply_patch"] = patch_entry
        # GPT-OSS also exposes apply_patch as a top-level recipient.  It is an
        # alias, not a second executor: both names share the same root checks,
        # helper image, selected terminal, and response handling.
        tools["apply_patch"] = {
            **patch_entry,
            "spec": {**patch_entry["spec"], "name": "apply_patch"},
        }

    return tools
