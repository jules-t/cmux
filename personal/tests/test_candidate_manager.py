from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

from personal.candidate_manager import create_bundle, make_baseline, rebase_candidate, verify_candidate


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


if __name__ == "__main__":
    unittest.main()
