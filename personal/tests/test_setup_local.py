from __future__ import annotations

import os
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest


class SetupLocalTests(unittest.TestCase):
    def test_writes_only_the_expected_user_scoped_files(self) -> None:
        control_root = pathlib.Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            environment["HOME"] = temporary
            environment.pop("PYTHONPATH", None)
            subprocess.run(
                [
                    sys.executable,
                    str(control_root / "personal" / "setup_local.py"),
                    "--control-root",
                    str(control_root),
                    "--config",
                    str(control_root / "personal" / "config.json"),
                    "--no-load",
                ],
                check=True,
                cwd=temporary,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            home = pathlib.Path(temporary)
            self.assertTrue(os.access(home / ".local/bin/cmux-personal", os.X_OK))
            self.assertTrue(os.access(home / ".local/bin/cmux-personal-update", os.X_OK))
            self.assertTrue(
                (home / ".local/share/cmux-personal/personal/local_updater.py").is_file()
            )
            self.assertTrue(
                (
                    home
                    / ".local/share/cmux-personal/personal/official_runtime_manifest.py"
                ).is_file()
            )
            launch_agent = (
                home / "Library/LaunchAgents/com.jules.cmux-personal-updater.plist"
            )
            with launch_agent.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["Label"], "com.jules.cmux-personal-updater")
            self.assertEqual(plist["StartInterval"], 21600)
            self.assertIn("-m", plist["ProgramArguments"])
            self.assertIn("personal.local_updater", plist["ProgramArguments"])
            self.assertIn("/opt/homebrew/bin", plist["EnvironmentVariables"]["PATH"])


if __name__ == "__main__":
    unittest.main()
