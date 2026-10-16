"""Prepare a pinned OWU upgrade in a separate clone; preserve the source checkout."""

from __future__ import annotations

import argparse
import ast
import sys
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_TAG = "v0.12.0"
UPSTREAM_SHA = "15818d88fd7bde1b73de33590f50d15d21e8d7c1"
UPSTREAM_URL = "https://github.com/open-webui/open-webui.git"
CONFLICT = re.compile(r"^<<<<<<<[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n?", re.M | re.S)


def run(*args: str, cwd: Path, capture: bool = False) -> bytes:
    result = subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else b""


def clone_source(source: Path, candidate: Path) -> None:
    shallow = run("git", "rev-parse", "--is-shallow-repository", cwd=source, capture=True).strip() == b"true"
    # Local object copies omit the shallow boundary of linked worktrees.
    mode = "--no-local" if shallow else "--no-hardlinks"
    run("git", "clone", mode, "--no-checkout", str(source), str(candidate), cwd=source)
    if shallow and run("git", "rev-parse", "--is-shallow-repository", cwd=candidate, capture=True).strip() != b"true":
        raise RuntimeError("Candidate clone lost the source's shallow history boundary.")


def resolve_conflicts(path: str, text: str) -> str:
    """Combine only the conflicts reviewed against the pinned release."""
    def resolve(match: re.Match[str]) -> str:
        ours, theirs = match.groups()
        if path == "backend/open_webui/utils/tools.py":
            if "'terminal_cwd': terminal_cwd," in ours and theirs.strip() == "'user_shell_tools': connection_config.get('user_shell_tools', 'auto'),":
                return ours + theirs
        if path == ".env.example" and not theirs.strip():
            if "PUBLIC_BETTER_STACK_RUM_TOKEN=''" in ours and "PUBLIC_BETTER_STACK_SENTRY_DSN=''" in ours:
                return ours
        if path == "Dockerfile":
            if ours.strip() == 'ARG PUBLIC_BETTER_STACK_RUM_TOKEN=""\nARG PUBLIC_BETTER_STACK_SENTRY_DSN=""' and theirs.strip() == 'ARG BUILD_CHANNEL\nARG USE_SLIM\nARG UID\nARG GID':
                return theirs + ours
            build = 'RUN PUBLIC_BETTER_STACK_RUM_TOKEN="${PUBLIC_BETTER_STACK_RUM_TOKEN}" PUBLIC_BETTER_STACK_SENTRY_DSN="${PUBLIC_BETTER_STACK_SENTRY_DSN}" npm run build'
            if ours.strip() == build and "ENV APP_BUILD_CHANNEL=${BUILD_CHANNEL}" in theirs and "RUN npm run build &&" in theirs:
                return theirs.replace("RUN npm run build &&", build + " &&", 1)
            if "./backend/requirements-gpt-oss-harmony.txt" in ours and theirs.strip() == "COPY --chown=$UID:$GID ./backend/requirements*.txt ./":
                return theirs
        raise RuntimeError(f"Unreviewed conflict in {path}; resolve it manually in the candidate clone.")

    resolved = CONFLICT.sub(resolve, text)
    if re.search(r"^(<<<<<<<|=======|>>>>>>>)", resolved, re.M):
        raise RuntimeError(f"Unresolved conflict markers in {path}")
    return resolved


def guard_native_tool_deferral(text: str) -> str:
    before = "        if (config['defer_builtin_tools'] or tool.get('type') != 'builtin')\n"
    after = before + "        and not tool.get('native_harmony_hidden')\n"
    if after in text:
        return text
    if text.count(before) != 1:
        raise RuntimeError("Tool Search implementation changed; review the native schema deferral guard.")
    return text.replace(before, after, 1)


def validate_seams(files: dict[str, str]) -> None:
    expected = {
        "backend/open_webui/utils/tools.py": ["'terminal_cwd': terminal_cwd", "'user_shell_tools':", "native_browser_tools(request, user)", "native_python_tools(tool_entry)"],
        "backend/open_webui/utils/middleware.py": ["native_container_tools(terminal_tools)", "native_repo_browser_tools(terminal_tools)", "enable_native_namespaces(form_data, namespaces)", "browser_citation_sources(request, tool_function_name, tool_result)"],
        "backend/open_webui/utils/code_interpreter.py": ["dict(self.session.headers)", "max_size=2 * 1024 * 1024", "message_data.get('header', {}).get('msg_type')"],
        "backend/open_webui/utils/tool_search.py": ["and not tool.get('native_harmony_hidden')"],
        "Dockerfile": ["PUBLIC_BETTER_STACK_RUM_TOKEN", "PUBLIC_BETTER_STACK_SENTRY_DSN", "APP_BUILD_CHANNEL", "requirements*.txt", "-r requirements-gpt-oss-harmony.txt", 'ENV NODE_OPTIONS="--max-old-space-size=4096"'],
        "src/app.html": ["better-stack-rum-token", "better-stack-sentry-dsn"],
        "src/routes/+layout.svelte": ["betterStackUserSignature", "betterstack('user', betterStackUser)"],
        "src/lib/components/layout/Overlay/AccountPending.svelte": ["user.set(null)"],
    }
    for name, needles in expected.items():
        for needle in needles:
            if needle not in files[name]:
                raise RuntimeError(f"Preservation check failed: {name}: {needle}")
        if name.endswith(".py"):
            ast.parse(files[name], filename=name)


def integrate(candidate: Path, checkpoint: str) -> None:
    result = subprocess.run(["git", "merge", "--no-commit", "--no-ff", UPSTREAM_SHA], cwd=candidate)
    conflicted = run("git", "diff", "--name-only", "--diff-filter=U", cwd=candidate, capture=True).decode().splitlines()
    if result.returncode and not conflicted:
        raise RuntimeError("Upstream merge failed before conflict resolution.")
    # Validate every resolution before writing any of them.
    resolutions = {name: resolve_conflicts(name, (candidate / name).read_text(encoding="utf-8")) for name in conflicted}
    for name, text in resolutions.items():
        (candidate / name).write_text(text, encoding="utf-8", newline="\n")
        run("git", "add", "--", name, cwd=candidate)
    search_path = candidate / "backend/open_webui/utils/tool_search.py"
    search_path.write_text(guard_native_tool_deferral(search_path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    seam_paths = ["backend/open_webui/utils/" + name + ".py" for name in ("tools", "middleware", "code_interpreter", "tool_search")]
    seam_paths += ["Dockerfile", "src/app.html", "src/routes/+layout.svelte", "src/lib/components/layout/Overlay/AccountPending.svelte"]
    validate_seams({name: (candidate / name).read_text(encoding="utf-8") for name in seam_paths})
    # New upstream must not silently alter or remove any adapter, test, vendor, or runtime asset.
    for name in ("backend/open_webui/integrations/gpt_oss_harmony", "backend/open_webui/vendor", "backend/requirements-gpt-oss-harmony.txt", "backend/tests/unit/integrations", "tools/gpt-oss-harmony", "docs/gpt-oss-harmony", "docs/better-stack-rum.md"):
        if run("git", "diff", checkpoint, "--", name, cwd=candidate, capture=True):
            raise RuntimeError(f"Preserved asset changed unexpectedly: {name}")
    if json.loads((candidate / "package.json").read_text(encoding="utf-8"))["version"] != "0.12.0":
        raise RuntimeError("Candidate version does not match the pinned upstream.")
    run("git", "add", "--", "backend/open_webui/utils/tool_search.py", cwd=candidate)
    run("git", "diff", "--cached", "--check", cwd=candidate)
    run("git", "commit", "-m", "Merge OWU v0.12.0 and preserve native Harmony dispatch", cwd=candidate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, help="New candidate clone path; must not exist")
    parser.add_argument("--build", action="store_true", help="Build the custom image and run full Harmony tests inside it")
    parser.add_argument("--build-native", action="store_true", help="Build this prepared Windows candidate; remaining arguments go to build_native.py")
    args, remaining = parser.parse_known_args()
    if args.build_native:
        if args.build or args.destination:
            parser.error("--build-native operates on this prepared candidate and cannot be combined with --build or --destination")
        subprocess.run([sys.executable, str(Path(__file__).with_name("build_native.py")), *remaining], check=True)
        return
    if remaining:
        parser.error("Unrecognized arguments: " + " ".join(remaining))
    source = Path(__file__).resolve().parents[2]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = (args.destination or source / "tools/gpt-oss-harmony/upgrades" / stamp).resolve()
    if candidate.exists():
        raise RuntimeError("Candidate path already exists; choose a new path to preserve previous work.")
    # Network and Docker checks precede clone, checkout, or commit mutations.
    remote = run("git", "ls-remote", UPSTREAM_URL, f"refs/tags/{UPSTREAM_TAG}", cwd=source, capture=True).decode().split()
    if not remote or remote[0] != UPSTREAM_SHA:
        raise RuntimeError("Upstream tag does not match the verified release commit.")
    if args.build:
        run("docker", "info", "--format", "{{.ServerVersion}}", cwd=source)
    head = run("git", "rev-parse", "HEAD", cwd=source, capture=True).decode().strip()
    patch = run("git", "diff", "--binary", "HEAD", cwd=source, capture=True)
    untracked = run("git", "ls-files", "--others", "--exclude-standard", "-z", cwd=source, capture=True).decode().split("\0")
    # No ignored files, credentials, local env files, databases, or node_modules are copied.
    allowed = {"docs/better-stack-rum.md", "docs/gpt-oss-harmony/upgrading.md", "tools/gpt-oss-harmony/upgrade.py", "tools/gpt-oss-harmony/test_upgrade.py", "tools/gpt-oss-harmony/upgrade.ps1"}
    unexpected = [name for name in untracked if name and name not in allowed]
    if unexpected:
        raise RuntimeError(f"Untracked files need an explicit preservation decision: {unexpected}")
    clone_source(source, candidate)
    run("git", "switch", "-c", "upgrade/owu-v0.12.0-harmony", head, cwd=candidate)
    if patch:
        subprocess.run(["git", "apply", "--binary", "-"], input=patch, cwd=candidate, check=True)
    for name in filter(None, untracked):
        target = candidate / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / name).read_bytes())
    # Clone-local commit identity only; global/source configuration is never changed.
    for key, fallback in (("user.name", "OWU Upgrade"), ("user.email", "owu-upgrade@localhost")):
        identity = subprocess.run(["git", "config", "--get", key], cwd=source, capture_output=True, text=True)
        run("git", "config", key, identity.stdout.strip() or fallback, cwd=candidate)
    run("git", "add", "-A", cwd=candidate)
    if run("git", "diff", "--cached", "--name-only", cwd=candidate, capture=True):
        run("git", "commit", "-m", "Checkpoint current Harmony, monitoring, and sign-out changes", cwd=candidate)
    checkpoint = run("git", "rev-parse", "HEAD", cwd=candidate, capture=True).decode().strip()
    run("git", "remote", "add", "upstream", UPSTREAM_URL, cwd=candidate)
    run("git", "fetch", "upstream", "tag", UPSTREAM_TAG, cwd=candidate)
    if run("git", "rev-parse", UPSTREAM_TAG, cwd=candidate, capture=True).decode().strip() != UPSTREAM_SHA:
        raise RuntimeError("Fetched release identity changed.")
    integrate(candidate, checkpoint)
    if args.build:
        image = f"local/openwebui-gpt-oss-harmony:0.12.0-{stamp}"
        run("docker", "build", "--build-arg", "PUBLIC_BETTER_STACK_RUM_TOKEN", "--build-arg", "PUBLIC_BETTER_STACK_SENTRY_DSN", "-t", image, ".", cwd=candidate)
        test_command = "mkdir -p /tmp/owu-upgrade-tests /tmp/owu-upgrade-static && python -m unittest discover -s /app/backend/tests/unit/integrations -p 'test_gpt_oss_harmony*.py'"
        run("docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", "-e", "DATA_DIR=/tmp/owu-upgrade-tests", "-e", "STATIC_DIR=/tmp/owu-upgrade-static", "-e", "WEBUI_SECRET_KEY=isolated-upgrade-tests-only", "-e", "OFFLINE_MODE=true", "-e", "HF_HUB_OFFLINE=1", image, "-c", test_command, cwd=candidate)
        print(f"Validated candidate image: {image}")
    else:
        print("Source prepared. Runtime/build validation is still required (rerun with --build and a new destination).")
    print(f"Candidate: {candidate}\nPreserved checkpoint: {checkpoint}\nOriginal checkout and running services are unchanged.")


if __name__ == "__main__":
    main()
