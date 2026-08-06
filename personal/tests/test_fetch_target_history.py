from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest


def git(repository: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class FetchTargetHistoryTests(unittest.TestCase):
    def test_fetches_only_the_checked_out_commit_history(self) -> None:
        script = (
            pathlib.Path(__file__).resolve().parents[1]
            / "ci"
            / "fetch_target_history.sh"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            remote = root / "remote.git"
            checkout = root / "checkout"

            git(root, "init", "--initial-branch=personal/stable", str(source))
            git(source, "config", "user.name", "Test User")
            git(source, "config", "user.email", "test@example.com")
            (source / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(source, "add", "tracked.txt")
            git(source, "commit", "-m", "base")
            base_blob = git(source, "rev-parse", "HEAD:tracked.txt")
            git(source, "tag", "source-tag")
            (source / "tracked.txt").write_text("target\n", encoding="utf-8")
            git(source, "commit", "-am", "target")
            target_sha = git(source, "rev-parse", "HEAD")

            git(source, "switch", "-c", "unrelated")
            (source / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
            git(source, "add", "unrelated.txt")
            git(source, "commit", "-m", "unrelated")
            unrelated_sha = git(source, "rev-parse", "HEAD")

            git(root, "clone", "--bare", str(source), str(remote))
            git(remote, "config", "uploadpack.allowFilter", "true")
            git(
                root,
                "clone",
                "--branch",
                "personal/stable",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-tags",
                remote.as_uri(),
                str(checkout),
            )

            self.assertEqual(git(checkout, "rev-parse", "--is-shallow-repository"), "true")
            subprocess.run([str(script), str(checkout)], check=True)
            subprocess.run([str(script), str(checkout)], check=True)

            self.assertEqual(git(checkout, "rev-parse", "HEAD"), target_sha)
            self.assertEqual(git(checkout, "rev-parse", "--is-shallow-repository"), "false")
            self.assertEqual(git(checkout, "rev-list", "--count", "HEAD"), "2")
            self.assertEqual(git(checkout, "tag", "--list"), "")
            historical_blob = subprocess.run(
                ["git", "-C", str(checkout), "cat-file", "-e", base_blob],
                env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(historical_blob.returncode, 0)
            self.assertEqual(
                git(checkout, "branch", "--remotes", "--list", "origin/unrelated"),
                "",
            )
            unrelated_object = subprocess.run(
                ["git", "-C", str(checkout), "cat-file", "-e", unrelated_sha],
                env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(unrelated_object.returncode, 0)


if __name__ == "__main__":
    unittest.main()
