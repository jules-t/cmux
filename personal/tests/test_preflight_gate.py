from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "preflight_gate.py"


def run_gate(
    *,
    observe: str = "success",
    ready: str = "true",
    prepare: str = "success",
    status: str = "clean",
    resolve: str = "skipped",
    review: str = "skipped",
    approved: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--observe-result",
            observe,
            "--ready",
            ready,
            "--prepare-result",
            prepare,
            "--candidate-status",
            status,
            "--resolve-result",
            resolve,
            "--review-result",
            review,
            "--approved",
            approved,
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class PreflightGateTests(unittest.TestCase):
    def test_accepts_an_observation_with_no_pending_update(self) -> None:
        result = run_gate(
            ready="false",
            prepare="skipped",
            status="",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already current", result.stdout)

    def test_accepts_a_clean_deterministically_verified_candidate(self) -> None:
        result = run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean candidate", result.stdout)

    def test_accepts_a_resolved_and_independently_approved_conflict(self) -> None:
        result = run_gate(
            status="conflict",
            resolve="success",
            review="success",
            approved="true",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("resolved candidate", result.stdout)

    def test_rejects_a_conflict_when_resolution_was_skipped(self) -> None:
        result = run_gate(
            status="conflict",
            resolve="skipped",
            review="skipped",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resolver and review gates", result.stderr)

    def test_rejects_a_failed_observation(self) -> None:
        result = run_gate(
            observe="failure",
            ready="",
            prepare="skipped",
            status="",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("observation failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
