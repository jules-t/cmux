from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from personal import agent_output


def reviewer_output() -> dict[str, object]:
    return {
        "decision": "approve",
        "confidence": 0.95,
        "base_functionality_preserved": True,
        "personal_functionality_preserved": True,
        "tests_preserved": True,
        "test_weakening_detected": False,
        "summary": "Both behavior sets remain intact.",
    }


class AgentOutputTests(unittest.TestCase):
    def test_accepts_exact_reviewer_output(self) -> None:
        agent_output.validate_reviewer(reviewer_output())

    def test_rejects_extra_reviewer_fields(self) -> None:
        value = reviewer_output()
        value["extra"] = True

        with self.assertRaisesRegex(SystemExit, "unexpected or missing fields"):
            agent_output.validate_reviewer(value)

    def test_rejects_boolean_reviewer_confidence(self) -> None:
        value = reviewer_output()
        value["confidence"] = True

        with self.assertRaisesRegex(SystemExit, "confidence must be a number"):
            agent_output.validate_reviewer(value)

    def test_accepts_exact_smoke_output(self) -> None:
        agent_output.validate_smoke(
            {
                "decision": "pass",
                "model": "deepseek-v4-flash",
                "upstream_repository": "manaflow-ai/cmux",
            }
        )

    def test_main_canonicalizes_valid_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "review.json"
            value = reviewer_output()
            output.write_text(json.dumps(value, indent=2), encoding="utf-8")

            with mock.patch(
                "sys.argv",
                ["agent_output.py", "reviewer", str(output)],
            ):
                self.assertEqual(agent_output.main(), 0)

            self.assertEqual(
                output.read_text(encoding="utf-8"),
                json.dumps(value, separators=(",", ":")) + "\n",
            )


if __name__ == "__main__":
    unittest.main()
