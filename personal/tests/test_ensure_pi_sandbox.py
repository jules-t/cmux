from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest


class EnsurePiSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control_root = pathlib.Path(__file__).resolve().parents[2]
        self.script = self.control_root / "personal/ci/ensure_pi_sandbox.sh"

    def test_scheduled_run_starts_docker_desktop_and_waits_until_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_app = root / "Docker.app"
            docker_app.mkdir()
            ready = root / "docker-ready"
            opened = root / "docker-opened"

            self._write_executable(
                fake_bin / "docker",
                """#!/bin/bash
if [[ "${1:-}" == "info" ]]; then
  [[ -f "$FAKE_DOCKER_READY" ]]
  exit
fi
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
  exit 0
fi
exit 1
""",
            )
            self._write_executable(
                fake_bin / "uname",
                """#!/bin/bash
echo Darwin
""",
            )
            self._write_executable(
                fake_bin / "open",
                """#!/bin/bash
touch "$FAKE_DOCKER_OPENED" "$FAKE_DOCKER_READY"
""",
            )
            environment = self._environment(fake_bin, docker_app, ready, opened)

            result = subprocess.run(
                [str(self.script), "--control-root", str(self.control_root), "--scheduled"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(opened.is_file())

    def test_interactive_run_does_not_start_docker_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_app = root / "Docker.app"
            docker_app.mkdir()
            ready = root / "docker-ready"
            opened = root / "docker-opened"

            self._write_executable(
                fake_bin / "docker",
                """#!/bin/bash
if [[ "${1:-}" == "info" ]]; then
  exit 1
fi
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
  exit 0
fi
exit 1
""",
            )
            self._write_executable(
                fake_bin / "uname",
                """#!/bin/bash
echo Darwin
""",
            )
            self._write_executable(
                fake_bin / "open",
                """#!/bin/bash
touch "$FAKE_DOCKER_OPENED" "$FAKE_DOCKER_READY"
""",
            )
            environment = self._environment(fake_bin, docker_app, ready, opened)

            result = subprocess.run(
                [str(self.script), "--control-root", str(self.control_root)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is not running", result.stderr)
            self.assertFalse(opened.exists())

    @staticmethod
    def _write_executable(path: pathlib.Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def _environment(
        fake_bin: pathlib.Path,
        docker_app: pathlib.Path,
        ready: pathlib.Path,
        opened: pathlib.Path,
    ) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
        environment["FAKE_DOCKER_READY"] = str(ready)
        environment["FAKE_DOCKER_OPENED"] = str(opened)
        environment["CMUX_PERSONAL_DOCKER_APP"] = str(docker_app)
        environment["CMUX_PERSONAL_OPEN_COMMAND"] = str(fake_bin / "open")
        environment["CMUX_PERSONAL_DOCKER_START_ATTEMPTS"] = "2"
        environment["CMUX_PERSONAL_DOCKER_START_DELAY_SECONDS"] = "0"
        return environment


if __name__ == "__main__":
    unittest.main()
