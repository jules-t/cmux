from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any


def invalid(kind: str, message: str) -> None:
    raise SystemExit(f"invalid {kind} agent output: {message}")


def load_object(kind: str, path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{kind} agent did not produce valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        invalid(kind, "expected a JSON object")
    return value


def validate_reviewer(value: dict[str, Any]) -> None:
    expected = {
        "decision",
        "confidence",
        "base_functionality_preserved",
        "personal_functionality_preserved",
        "tests_preserved",
        "test_weakening_detected",
        "summary",
    }
    if set(value) != expected:
        invalid("reviewer", "unexpected or missing fields")
    if value["decision"] not in {"approve", "stop"}:
        invalid("reviewer", "decision must be approve or stop")
    if type(value["confidence"]) not in {int, float} or not 0 <= value["confidence"] <= 1:
        invalid("reviewer", "confidence must be a number from 0 through 1")
    for field in (
        "base_functionality_preserved",
        "personal_functionality_preserved",
        "tests_preserved",
        "test_weakening_detected",
    ):
        if type(value[field]) is not bool:
            invalid("reviewer", f"{field} must be a boolean")
    if not isinstance(value["summary"], str) or not value["summary"]:
        invalid("reviewer", "summary must be a non-empty string")


def validate_smoke(value: dict[str, Any]) -> None:
    if set(value) != {"decision", "model", "upstream_repository"}:
        invalid("smoke", "unexpected or missing fields")
    if value["decision"] != "pass":
        invalid("smoke", "decision must be pass")
    if value["model"] != "deepseek-v4-flash":
        invalid("smoke", "model must be deepseek-v4-flash")
    if value["upstream_repository"] != "manaflow-ai/cmux":
        invalid("smoke", "upstream repository did not match")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate structured Pi agent output.")
    parser.add_argument("kind", choices=("reviewer", "smoke"))
    parser.add_argument("path", type=pathlib.Path)
    args = parser.parse_args()

    value = load_object(args.kind, args.path)
    if args.kind == "reviewer":
        validate_reviewer(value)
    else:
        validate_smoke(value)
    args.path.write_text(
        json.dumps(value, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
