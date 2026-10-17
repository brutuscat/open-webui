#!/usr/bin/env python3
"""Small, root-confined implementation of HarmonyAgent's apply_patch helper.

The helper is intentionally standard-library only and is installed solely in
the optional isolated Open Terminal-derived image. Its working directory is
the repository root selected by the OpenWebUI adapter.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class PatchError(ValueError):
    """A patch was invalid or would escape the selected repository root."""


@dataclass(frozen=True)
class Operation:
    kind: str
    path: str
    body: tuple[str, ...]


_OPERATION = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
_MISSING = object()


def _inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PatchError("patch path escapes the selected repository root") from exc


def _target(root: Path, value: str) -> Path:
    if not value or "\x00" in value or "\\" in value:
        raise PatchError("patch path must be a non-empty relative POSIX path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise PatchError("patch path escapes the selected repository root")

    candidate = root.joinpath(*relative.parts)
    # Resolve existing parents and targets so a symlink cannot redirect writes
    # outside the repository even though the textual path looks relative.
    _inside(candidate.parent.resolve(), root)
    _inside(candidate.resolve(strict=False), root)
    return candidate


def parse_patch(value: str) -> list[Operation]:
    lines = value.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise PatchError("patch must use the *** Begin Patch / *** End Patch format")

    operations: list[Operation] = []
    index = 1
    last = len(lines) - 1
    while index < last:
        match = _OPERATION.match(lines[index])
        if not match:
            raise PatchError(f"expected a file operation at patch line {index + 1}")
        kind, path = match.groups()
        index += 1
        body: list[str] = []
        while index < last and not _OPERATION.match(lines[index]):
            body.append(lines[index])
            index += 1
        operations.append(Operation(kind=kind, path=path, body=tuple(body)))

    if not operations:
        raise PatchError("patch must contain at least one file operation")
    return operations


def _add(body: tuple[str, ...]) -> str:
    if any(not line.startswith("+") for line in body):
        raise PatchError("an added file may contain only '+' lines")
    if not body:
        return ""
    return "\n".join(line[1:] for line in body) + "\n"


def _hunks(body: tuple[str, ...]) -> list[tuple[str, ...]]:
    hunks: list[tuple[str, ...]] = []
    current: list[str] | None = None
    for line in body:
        if line.startswith("@@"):
            if current is not None:
                hunks.append(tuple(current))
            current = []
            continue
        if current is None:
            raise PatchError("an update must begin with a @@ hunk marker")
        if not line or line[0] not in {" ", "+", "-"}:
            raise PatchError("update lines must start with space, '+', or '-'")
        current.append(line)
    if current is not None:
        hunks.append(tuple(current))
    if not hunks:
        raise PatchError("an update must contain at least one hunk")
    return hunks


def _update(value: str, body: tuple[str, ...]) -> str:
    lines = value.splitlines()
    had_final_newline = value.endswith("\n")
    cursor = 0
    for hunk in _hunks(body):
        old = [line[1:] for line in hunk if line[0] in {" ", "-"}]
        new = [line[1:] for line in hunk if line[0] in {" ", "+"}]
        if old:
            start = next(
                (
                    offset
                    for offset in range(cursor, len(lines) - len(old) + 1)
                    if lines[offset : offset + len(old)] == old
                ),
                None,
            )
            if start is None:
                raise PatchError("update context was not found")
        else:
            start = cursor
        lines[start : start + len(old)] = new
        # Consecutive unified hunks commonly share one trailing/leading context
        # line, so retain that line as a valid next search position.
        cursor = max(start + len(new) - 1, 0)
    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if had_final_newline else "")


def _read_current(path: Path):
    if not path.exists():
        return _MISSING
    if not path.is_file():
        raise PatchError("patch target must be a regular file")
    return path.read_text(encoding="utf-8")


def apply(operations: list[Operation], root: Path) -> None:
    staged: dict[Path, object] = {}
    preserve_modes: dict[Path, int] = {}
    for operation in operations:
        target = _target(root, operation.path)
        current = staged.get(target, _read_current(target))
        if operation.kind == "Add":
            if current is not _MISSING:
                raise PatchError("cannot add a file that already exists")
            staged[target] = _add(operation.body)
        elif operation.kind == "Update":
            if current is _MISSING:
                raise PatchError("cannot update a file that does not exist")
            if not isinstance(current, str):
                raise PatchError("invalid staged file content")
            if target.exists():
                preserve_modes[target] = stat.S_IMODE(target.stat().st_mode)
            staged[target] = _update(current, operation.body)
        elif operation.kind == "Delete":
            if operation.body:
                raise PatchError("a delete operation may not contain patch lines")
            if current is _MISSING:
                raise PatchError("cannot delete a file that does not exist")
            staged[target] = _MISSING
        else:  # pragma: no cover - regex limits the operation set
            raise PatchError("unsupported patch operation")

    # Validate every target before the first mutation. This makes a bad later
    # hunk fail without partially applying an earlier operation.
    for target in staged:
        _inside(target.parent.resolve(), root)
        _inside(target.resolve(strict=False), root)

    for target, content in staged.items():
        if content is _MISSING:
            target.unlink()
            continue
        if not isinstance(content, str):
            raise PatchError("invalid staged file content")
        target.parent.mkdir(parents=True, exist_ok=True)
        _inside(target.parent.resolve(), root)
        descriptor, temporary = tempfile.mkstemp(prefix=".apply_patch-", dir=target.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            if target in preserve_modes:
                os.chmod(temporary, preserve_modes[target])
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def main() -> int:
    try:
        patch = sys.stdin.read()
        root = Path.cwd().resolve()
        apply(parse_patch(patch), root)
    except PatchError as exc:
        print(f"apply_patch: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # Keep unexpected implementation errors non-destructive.
        print(f"apply_patch: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("Done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
