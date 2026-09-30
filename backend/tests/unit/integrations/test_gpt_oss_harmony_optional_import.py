import os
import subprocess
import sys
import unittest
from pathlib import Path


class HarmonyOptionalImportTests(unittest.TestCase):
    def test_standard_tools_import_does_not_load_optional_browser_adapter(self):
        repo_root = Path(__file__).resolve().parents[4]
        env = os.environ.copy()
        backend = str(repo_root / "backend")
        env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
        script = """
import sys
import open_webui.utils.tools
if "open_webui.integrations.gpt_oss_harmony.dispatch" in sys.modules:
    raise SystemExit("optional Harmony browser adapter was imported eagerly")
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
