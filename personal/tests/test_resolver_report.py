from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from personal import resolver_report


def valid_report(*, decision: str = "resolved") -> dict[str, object]:
    return {
        "decision": decision,
        "confidence": 0.9,
        "summary": "Kept the personal behavior and incorporated upstream changes.",
        "files": [
            {
                "path": "Sources/Example.swift",
                "resolution": "Combined both compatible changes.",
            }
        ],
    }


class ResolverReportTests(unittest.TestCase):
    def test_collect_validates_normalizes_and_removes_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source.json"
            destination = root / "destination.json"
            value = valid_report()
            source.write_text(json.dumps(value, indent=2), encoding="utf-8")

            resolver_report.collect(source, destination)

            self.assertFalse(source.exists())
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                json.dumps(value, separators=(",", ":")) + "\n",
            )

    def test_collect_removes_an_invalid_source_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source.json"
            destination = root / "destination.json"
            source.write_text("not JSON", encoding="utf-8")

            with self.assertRaisesRegex(
                SystemExit,
                "resolver did not produce a valid JSON report",
            ):
                resolver_report.collect(source, destination)

            self.assertFalse(source.exists())
            self.assertFalse(destination.exists())

    def test_validate_rejects_unexpected_fields(self) -> None:
        value = valid_report()
        value["unexpected"] = True

        with self.assertRaisesRegex(SystemExit, "expected exactly"):
            resolver_report.validate(value)

    def test_validate_rejects_boolean_confidence(self) -> None:
        value = valid_report()
        value["confidence"] = True

        with self.assertRaisesRegex(SystemExit, "confidence must be a number"):
            resolver_report.validate(value)

    def test_require_resolved_accepts_resolved_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = pathlib.Path(temporary) / "report.json"
            report.write_text(json.dumps(valid_report()), encoding="utf-8")

            resolver_report.require_resolved(report)

    def test_require_resolved_rejects_stop_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = pathlib.Path(temporary) / "report.json"
            report.write_text(
                json.dumps(valid_report(decision="stop")),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "did not resolve the candidate"):
                resolver_report.require_resolved(report)


if __name__ == "__main__":
    unittest.main()
