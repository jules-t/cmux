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
            self.assertTrue(os.access(home / ".local/bin/cmux-personal-sync", os.X_OK))
            self.assertTrue(os.access(home / ".local/bin/cmux-personal-build", os.X_OK))
            self.assertTrue(os.access(home / ".local/bin/cmux-personal-publish", os.X_OK))
            self.assertTrue(
                (home / ".local/share/cmux-personal/personal/local_updater.py").is_file()
            )
            self.assertTrue(
                (
                    home
                    / ".local/share/cmux-personal/personal/official_runtime_manifest.py"
                ).is_file()
            )
            monitor_control = home / ".local/share/cmux-personal/monitor-control"
            self.assertTrue((monitor_control / ".git").is_dir())
            self.assertTrue(
                (monitor_control / "personal/ci/local_sync.sh").is_file()
            )
            self.assertTrue(
                (monitor_control / ".github/pi/prompts/resolve-cmux-conflicts.txt").is_file()
            )
            self.assertFalse((monitor_control / "personal/pi/node_modules").exists())
            launch_agent = (
                home / "Library/LaunchAgents/com.jules.cmux-personal-updater.plist"
            )
            with launch_agent.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["Label"], "com.jules.cmux-personal-updater")
            self.assertEqual(plist["StartInterval"], 120)
            self.assertIn("-m", plist["ProgramArguments"])
            self.assertIn("personal.local_updater", plist["ProgramArguments"])
            self.assertIn("--scheduled", plist["ProgramArguments"])
            self.assertIn("/opt/homebrew/bin", plist["EnvironmentVariables"]["PATH"])
            monitor_agent = (
                home / "Library/LaunchAgents/com.jules.cmux-personal-monitor.plist"
            )
            with monitor_agent.open("rb") as handle:
                monitor = plistlib.load(handle)
            self.assertEqual(monitor["Label"], "com.jules.cmux-personal-monitor")
            self.assertEqual(monitor["StartInterval"], 6 * 60 * 60)
            self.assertIn("local_sync.sh", " ".join(monitor["ProgramArguments"]))
            self.assertIn("--scheduled", monitor["ProgramArguments"])
            self.assertEqual(monitor["WorkingDirectory"], str(monitor_control))
            self.assertIn(
                str(monitor_control / "personal/ci/local_sync.sh"),
                monitor["ProgramArguments"],
            )


if __name__ == "__main__":
    unittest.main()
