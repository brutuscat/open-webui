"""Offline tests of the reviewed merge resolutions and native deferral behavior."""

import ast
import fnmatch
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from upgrade import clone_source, guard_native_tool_deferral, resolve_conflicts


def conflict(ours, theirs):
    return f"<<<<<<< local\n{ours}\n=======\n{theirs}\n>>>>>>> upstream\n"


class UpgradeTests(unittest.TestCase):
    def test_clone_preserves_shallow_linked_worktree_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args, cwd=root):
                return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.STDOUT).strip()
            original, shallow, linked, candidate = [root / name for name in ("original", "shallow", "linked", "candidate")]
            git("init", str(original))
            git("config", "user.name", "Test", cwd=original)
            git("config", "user.email", "test@localhost", cwd=original)
            for value in ("before", "boundary"):
                (original / "file").write_text(value)
                git("add", "file", cwd=original)
                git("commit", "-m", value, cwd=original)
            git("clone", "--depth", "1", original.as_uri(), str(shallow))
            git("worktree", "add", "--detach", str(linked), cwd=shallow)
            clone_source(linked, candidate)
            self.assertEqual(git("rev-parse", "--is-shallow-repository", cwd=candidate), b"true")
            self.assertEqual(git("rev-parse", "HEAD", cwd=candidate), git("rev-parse", "HEAD", cwd=linked))
            git("fsck", "--connectivity-only", cwd=candidate)

    def test_terminal_keeps_workspace_and_upstream_shell_policy(self):
        merged = resolve_conflicts("backend/open_webui/utils/tools.py", conflict("            'terminal_cwd': terminal_cwd,", "            'user_shell_tools': connection_config.get('user_shell_tools', 'auto'),"))
        self.assertIn("'terminal_cwd': terminal_cwd,", merged)
        self.assertIn("'user_shell_tools':", merged)

    def test_build_keeps_monitoring_channel_slim_and_ownership(self):
        ours = 'RUN PUBLIC_BETTER_STACK_RUM_TOKEN="${PUBLIC_BETTER_STACK_RUM_TOKEN}" PUBLIC_BETTER_STACK_SENTRY_DSN="${PUBLIC_BETTER_STACK_SENTRY_DSN}" npm run build'
        theirs = 'ENV APP_BUILD_CHANNEL=${BUILD_CHANNEL}\nRUN npm run build && \\\n    if [ "$USE_SLIM" = "true" ]; then find build -type f -name "*.map" -delete; fi\nRUN chown -R $UID:$GID /app/backend'
        merged = resolve_conflicts("Dockerfile", conflict(ours, theirs))
        for marker in ("PUBLIC_BETTER_STACK_RUM_TOKEN", "PUBLIC_BETTER_STACK_SENTRY_DSN", "APP_BUILD_CHANNEL", "USE_SLIM", "chown"):
            self.assertIn(marker, merged)
        self.assertEqual(merged.count("npm run build"), 1)

    def test_unexpected_conflicts_stop_instead_of_dropping_changes(self):
        with self.assertRaisesRegex(RuntimeError, "Unreviewed conflict"):
            resolve_conflicts("backend/open_webui/utils/tools.py", conflict("private workspace implementation changed", "upstream changed again"))

    def test_deferral_guard_is_idempotent_and_refuses_unknown_code(self):
        original = "        if (config['defer_builtin_tools'] or tool.get('type') != 'builtin')\n"
        guarded = guard_native_tool_deferral(original)
        self.assertEqual(guard_native_tool_deferral(guarded), guarded)
        with self.assertRaises(RuntimeError):
            guard_native_tool_deferral("unknown implementation")

    def test_real_upstream_selector_never_defers_native_dispatchers(self):
        # Set by the preview runner to exercise the fetched, pinned upstream code.
        import os
        source = os.getenv("OWU_UPGRADE_TOOL_SEARCH_SOURCE") or Path(__file__).resolve().parents[2] / "backend/open_webui/utils/tool_search.py"
        if not Path(source).is_file():
            self.skipTest("Set OWU_UPGRADE_TOOL_SEARCH_SOURCE to the pinned tool_search.py")
        tree = ast.parse(guard_native_tool_deferral(Path(source).read_text(encoding="utf-8")))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "select_deferred_tools")
        namespace = {"fnmatch": fnmatch, "JSONCodec": type("Codec", (), {"dumps": staticmethod(json.dumps)})}
        exec(compile(ast.Module(body=[function], type_ignores=[]), source, "exec"), namespace)
        native = ["browser.search", "container.exec", "repo_browser.search", "python", "apply_patch"]
        tools = {name: {"spec": {"description": "large" * 500}, "type": "terminal", "native_harmony_hidden": True} for name in native}
        tools["ordinary_tool"] = {"spec": {"description": "large" * 500}, "type": "terminal"}
        config = {"always_loaded": [], "defer_builtin_tools": True, "defer_threshold": 100}
        self.assertEqual(namespace["select_deferred_tools"](tools, config), ["ordinary_tool"])


if __name__ == "__main__":
    unittest.main()
