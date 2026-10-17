import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


def _load_apply_patch_module():
    repo_root = Path(__file__).resolve().parents[4]
    helper = repo_root / "tools" / "gpt-oss-harmony" / "open-terminal" / "apply_patch.py"
    spec = importlib.util.spec_from_file_location("harmony_apply_patch_helper", helper)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Harmony apply_patch helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.name == "posix", "Harmony patch helper runs in a POSIX container")
class HarmonyApplyPatchTests(unittest.TestCase):
    def test_update_preserves_existing_executable_mode(self):
        helper = _load_apply_patch_module()
        patch = """*** Begin Patch
*** Update File: script.sh
@@
-echo old
+echo new
*** End Patch
"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "script.sh"
            target.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
            target.chmod(0o755)

            helper.apply(helper.parse_patch(patch), root)

            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            self.assertEqual(target.read_text(encoding="utf-8"), "#!/bin/sh\necho new\n")
