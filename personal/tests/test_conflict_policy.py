from __future__ import annotations

import unittest

from personal.conflict_policy import classify_conflicts, validate_agent_outputs


POLICY = {
    "maximum_conflicted_files": 3,
    "maximum_conflict_markers": 10,
    "protected_patterns": [".github/workflows/**", "**/*.entitlements"],
    "manual_review_patterns": ["**/*.xcodeproj/**"],
}


class ConflictPolicyTests(unittest.TestCase):
    def test_simple_source_conflict_is_agent_eligible(self) -> None:
        result = classify_conflicts(
            ["Sources/KeyboardShortcutSettingsFileStore.swift"],
            marker_count=3,
            policy=POLICY,
        )
        self.assertTrue(result["eligible_for_agent"])
        self.assertEqual(result["protected_paths"], [])

    def test_workflow_and_entitlement_conflicts_stop(self) -> None:
        result = classify_conflicts(
            [".github/workflows/release.yml", "cmux.release.entitlements"],
            marker_count=6,
            policy=POLICY,
        )
        self.assertFalse(result["eligible_for_agent"])
        self.assertEqual(len(result["protected_paths"]), 2)

    def test_reviewer_must_confirm_both_functionalities_and_tests(self) -> None:
        resolver = {"decision": "resolved", "confidence": 0.95}
        reviewer = {
            "decision": "approve",
            "confidence": 0.96,
            "base_functionality_preserved": True,
            "personal_functionality_preserved": True,
            "tests_preserved": True,
            "test_weakening_detected": False,
        }
        self.assertTrue(validate_agent_outputs(resolver, reviewer)["approved"])
        reviewer["personal_functionality_preserved"] = False
        self.assertFalse(validate_agent_outputs(resolver, reviewer)["approved"])


if __name__ == "__main__":
    unittest.main()
