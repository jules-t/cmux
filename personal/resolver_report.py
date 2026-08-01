from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any


EXPECTED_KEYS = {"decision", "confidence", "summary", "files"}


def invalid(message: str) -> None:
    raise SystemExit(f"invalid resolver report: {message}")


def validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != EXPECTED_KEYS:
        invalid("expected exactly decision, confidence, summary, and files")
    if value["decision"] not in {"resolved", "stop"}:
        invalid("decision must be resolved or stop")
    if type(value["confidence"]) not in {int, float} or not 0 <= value["confidence"] <= 1:
        invalid("confidence must be a number from 0 through 1")
    if not isinstance(value["summary"], str) or not value["summary"]:
        invalid("summary must be a non-empty string")
    if not isinstance(value["files"], list):
        invalid("files must be an array")
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "resolution"}:
            invalid("each files entry must contain exactly path and resolution")
        if not all(
            isinstance(item[key], str) and item[key]
            for key in ("path", "resolution")
        ):
            invalid("each files entry must use non-empty strings")
    return value


def read_report(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"resolver did not produce a valid JSON report: {exc}") from exc
    return validate(value)


def collect(source: pathlib.Path, destination: pathlib.Path) -> None:
    try:
        value = read_report(source)
    finally:
        source.unlink(missing_ok=True)
    destination.write_text(
        json.dumps(value, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def require_resolved(report: pathlib.Path) -> None:
    value = read_report(report)
    if value["decision"] != "resolved":
        raise SystemExit(f"resolver did not resolve the candidate: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate conflict resolver reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser(
        "collect",
        help="Validate, normalize, and remove the resolver's workspace report.",
    )
    collect_parser.add_argument("--source", type=pathlib.Path, required=True)
    collect_parser.add_argument("--destination", type=pathlib.Path, required=True)

    resolved_parser = subparsers.add_parser(
        "require-resolved",
        help="Fail unless a validated report records a resolved decision.",
    )
    resolved_parser.add_argument("--report", type=pathlib.Path, required=True)

    args = parser.parse_args()
    if args.command == "collect":
        collect(args.source, args.destination)
    else:
        require_resolved(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
