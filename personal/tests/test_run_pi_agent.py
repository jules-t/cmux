from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest


class RunPiAgentTests(unittest.TestCase):
    def test_shared_clone_objects_are_mounted_read_only_at_host_path(self) -> None:
        control_root = pathlib.Path(__file__).resolve().parents[2]
        script = control_root / "personal" / "ci" / "run_pi_agent.sh"

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = pathlib.Path(temporary)
            source = temporary_root / "source cache"
            workspace = temporary_root / "workspace"
            prompt = control_root / ".github" / "pi" / "prompts" / "deepseek-smoke.txt"

            subprocess.run(["git", "init", "--quiet", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Test User"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.com"],
                check=True,
            )
            (source / "tracked.txt").write_text("shared object\n")
            subprocess.run(
                ["git", "-C", str(source), "add", "tracked.txt"], check=True
            )
            subprocess.run(
                ["git", "-C", str(source), "commit", "--quiet", "-m", "fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--shared",
                    "--no-checkout",
                    "--quiet",
                    str(source),
                    str(workspace),
                ],
                check=True,
            )

            alternate_file = workspace / ".git" / "objects" / "info" / "alternates"
            alternate_objects = pathlib.Path(alternate_file.read_text().strip()).resolve()
            self.assertTrue(alternate_objects.is_dir())

            fake_bin = temporary_root / "bin"
            fake_bin.mkdir()
            docker_log = temporary_root / "docker-arguments.txt"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$@\" >> \"$CMUX_TEST_DOCKER_ARGUMENTS\"\n"
            )
            fake_docker.chmod(0o755)

            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["DEEPSEEK_API_KEY"] = "test-only-key"
            environment["CMUX_TEST_DOCKER_ARGUMENTS"] = str(docker_log)
            subprocess.run(
                [
                    str(script),
                    "--control",
                    str(control_root),
                    "--workspace-root",
                    str(workspace),
                    "--working-directory",
                    ".",
                    "--prompt",
                    str(prompt),
                    "--profile",
                    "resolver",
                ],
                check=True,
                cwd=control_root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            docker_arguments = docker_log.read_text().splitlines()
            expected_mount = (
                f"type=bind,source={alternate_objects},target={alternate_objects},readonly"
            )
            self.assertIn(expected_mount, docker_arguments)


if __name__ == "__main__":
    unittest.main()
