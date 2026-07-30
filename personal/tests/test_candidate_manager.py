from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

from personal.common import ControlError
from personal.candidate_manager import (
    create_bundle,
    make_baseline,
    publish_candidate,
    rebase_candidate,
    verify_candidate,
)


POLICY = {
    "maximum_conflicted_files": 12,
    "maximum_conflict_markers": 80,
    "protected_patterns": [".github/workflows/**"],
    "manual_review_patterns": [],
    "test_patterns": ["**/*Tests.swift", "**/Tests/**"],
}


def command(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def write(path: pathlib.Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class CandidateManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.temporary.name)
        command(self.repo, "init", "-b", "main")
        command(self.repo, "config", "user.name", "Test")
        command(self.repo, "config", "user.email", "test@example.com")
        write(self.repo / "feature.txt", "base\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "base")
        command(self.repo, "tag", "v1.0.0")
        command(self.repo, "switch", "-c", "personal/stable")
        write(self.repo / "personal.txt", "personal\n")
        write(self.repo / "Tests" / "PersonalTests.swift", "test\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "personal feature")
        command(self.repo, "switch", "main")
        write(self.repo / "upstream.txt", "upstream\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "upstream release")
        command(self.repo, "tag", "v1.0.1")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_clean_rebase_preserves_personal_commit_and_tests(self) -> None:
        baseline = make_baseline(
            self.repo,
            source_ref="personal/stable",
            current_base_ref="v1.0.0",
            target_ref="v1.0.1",
            candidate_branch="candidate/personal-v1.0.1",
            policy=POLICY,
        )
        result = rebase_candidate(self.repo, baseline, POLICY, leave_conflicts=False)
        self.assertEqual(result["status"], "clean")
        verification = verify_candidate(self.repo, baseline=baseline, policy=POLICY)
        self.assertTrue(verification["verified"])
        self.assertTrue((self.repo / "Tests" / "PersonalTests.swift").exists())
        bundle = self.repo / ".test-candidate.bundle"
        create_bundle(self.repo, bundle)
        clone = self.repo / ".candidate-clone"
        subprocess.run(
            ["git", "clone", str(bundle), str(clone)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(command(clone, "rev-parse", "HEAD"), verification["candidate_commit"])
        remote = self.repo / ".published.git"
        command(self.repo, "init", "--bare", str(remote))
        publish_candidate(
            clone,
            remote_url=str(remote),
            candidate_branch="candidate/personal-v1.0.1",
            expected_commit=verification["candidate_commit"],
        )
        self.assertEqual(
            command(remote, "rev-parse", "refs/heads/candidate/personal-v1.0.1"),
            verification["candidate_commit"],
        )

    def test_conflicting_source_change_is_reported(self) -> None:
        command(self.repo, "switch", "personal/stable")
        write(self.repo / "feature.txt", "personal version\n")
        command(self.repo, "add", "feature.txt")
        command(self.repo, "commit", "-m", "personal edit")
        command(self.repo, "switch", "main")
        write(self.repo / "feature.txt", "upstream version\n")
        command(self.repo, "add", "feature.txt")
        command(self.repo, "commit", "-m", "upstream edit")
        command(self.repo, "tag", "-f", "v1.0.1")
        baseline = make_baseline(
            self.repo,
            source_ref="personal/stable",
            current_base_ref="v1.0.0",
            target_ref="v1.0.1",
            candidate_branch="candidate/personal-v1.0.1",
            policy=POLICY,
        )
        result = rebase_candidate(self.repo, baseline, POLICY, leave_conflicts=False)
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["eligible_for_agent"])
        self.assertEqual(result["conflicted_paths"], ["feature.txt"])

    def test_clean_rebase_preserves_personal_merge_topology(self) -> None:
        command(self.repo, "switch", "personal/stable")
        personal_root = command(self.repo, "rev-parse", "HEAD")

        command(self.repo, "switch", "-c", "personal-line-numbers", personal_root)
        write(self.repo / "line-numbers.txt", "line numbers\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "personal line numbers")

        command(self.repo, "switch", "-c", "personal-indent-guides", personal_root)
        write(self.repo / "indent-guides.txt", "indent guides\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "personal indent guides")

        command(self.repo, "switch", "personal/stable")
        command(
            self.repo,
            "merge",
            "--no-ff",
            "personal-line-numbers",
            "-m",
            "merge personal line numbers",
        )
        command(
            self.repo,
            "merge",
            "--no-ff",
            "personal-indent-guides",
            "-m",
            "merge personal indent guides",
        )

        baseline = make_baseline(
            self.repo,
            source_ref="personal/stable",
            current_base_ref="v1.0.0",
            target_ref="v1.0.1",
            candidate_branch="candidate/personal-v1.0.1",
            policy=POLICY,
        )
        result = rebase_candidate(self.repo, baseline, POLICY, leave_conflicts=False)
        self.assertEqual(result["status"], "clean")
        verification = verify_candidate(self.repo, baseline=baseline, policy=POLICY)
        self.assertTrue(verification["verified"])
        self.assertEqual(
            command(
                self.repo,
                "rev-list",
                "--count",
                "--merges",
                f"{baseline['target_commit']}..HEAD",
            ),
            "2",
        )
        self.assertTrue((self.repo / "line-numbers.txt").exists())
        self.assertTrue((self.repo / "indent-guides.txt").exists())

    def test_rejects_a_target_that_rewrites_the_recorded_upstream_base(self) -> None:
        command(self.repo, "switch", "--orphan", "rewritten")
        write(self.repo / "unrelated.txt", "rewritten history\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "rewritten upstream")
        with self.assertRaisesRegex(ControlError, "rewritten history"):
            make_baseline(
                self.repo,
                source_ref="personal/stable",
                current_base_ref="v1.0.0",
                target_ref="rewritten",
                candidate_branch="candidate/personal-v1.0.1",
                policy=POLICY,
            )

    def test_a_rebase_failure_without_unmerged_paths_fails_closed(self) -> None:
        baseline = make_baseline(
            self.repo,
            source_ref="personal/stable",
            current_base_ref="v1.0.0",
            target_ref="v1.0.1",
            candidate_branch="candidate/personal-v1.0.1",
            policy=POLICY,
        )
        hook = self.repo / ".git" / "hooks" / "pre-rebase"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        result = rebase_candidate(self.repo, baseline, POLICY, leave_conflicts=False)
        self.assertEqual(result["status"], "conflict")
        self.assertFalse(result["eligible_for_agent"])
        self.assertIn(
            "git reported a conflict without any unmerged paths",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
