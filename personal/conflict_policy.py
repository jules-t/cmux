from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
from typing import Any, Iterable

from personal.common import ControlError, append_github_output, load_json, write_json


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    normalized = pathlib.PurePosixPath(path).as_posix()
    for pattern in patterns:
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(normalized, pattern[3:]):
            return True
    return False


def classify_conflicts(
    paths: list[str],
    *,
    marker_count: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    unique_paths = sorted(set(paths))
    protected = [
        path for path in unique_paths if path_matches(path, policy.get("protected_patterns", []))
    ]
    manual_review = [
        path for path in unique_paths if path_matches(path, policy.get("manual_review_patterns", []))
    ]
    maximum_files = int(policy.get("maximum_conflicted_files", 12))
    maximum_markers = int(policy.get("maximum_conflict_markers", 80))
    reasons: list[str] = []
    if protected:
        reasons.append(f"protected paths are conflicted: {', '.join(protected)}")
    if len(unique_paths) > maximum_files:
        reasons.append(f"{len(unique_paths)} conflicted files exceeds the limit of {maximum_files}")
    if marker_count > maximum_markers:
        reasons.append(f"{marker_count} conflict markers exceeds the limit of {maximum_markers}")
    if not unique_paths:
        reasons.append("git reported a conflict without any unmerged paths")
    return {
        "eligible_for_agent": not reasons,
        "conflicted_paths": unique_paths,
        "protected_paths": protected,
        "manual_review_paths": manual_review,
        "marker_count": marker_count,
        "reasons": reasons,
        "requires_independent_review": bool(unique_paths),
    }


def validate_agent_outputs(
    resolver: dict[str, Any],
    reviewer: dict[str, Any],
    *,
    minimum_resolver_confidence: float = 0.80,
    minimum_reviewer_confidence: float = 0.90,
) -> dict[str, Any]:
    reasons: list[str] = []
    if resolver.get("decision") != "resolved":
        reasons.append(f"resolver decision is {resolver.get('decision')!r}")
    if float(resolver.get("confidence", 0)) < minimum_resolver_confidence:
        reasons.append("resolver confidence is below the automatic threshold")
    if reviewer.get("decision") != "approve":
        reasons.append(f"reviewer decision is {reviewer.get('decision')!r}")
    if float(reviewer.get("confidence", 0)) < minimum_reviewer_confidence:
        reasons.append("reviewer confidence is below the automatic threshold")
    if reviewer.get("base_functionality_preserved") is not True:
        reasons.append("reviewer did not confirm base cmux functionality")
    if reviewer.get("personal_functionality_preserved") is not True:
        reasons.append("reviewer did not confirm personal functionality")
    if reviewer.get("tests_preserved") is not True:
        reasons.append("reviewer did not confirm test preservation")
    if reviewer.get("test_weakening_detected") is not False:
        reasons.append("reviewer detected or could not exclude weakened tests")
    return {"approved": not reasons, "reasons": reasons}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply cmux personal conflict policy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--policy", required=True)
    classify_parser.add_argument("--paths-file", required=True)
    classify_parser.add_argument("--marker-count", type=int, required=True)
    classify_parser.add_argument("--output", required=True)

    review_parser = subparsers.add_parser("review-gate")
    review_parser.add_argument("--resolver", required=True)
    review_parser.add_argument("--reviewer", required=True)
    review_parser.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "classify":
        paths = [
            line.strip()
            for line in pathlib.Path(args.paths_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        result = classify_conflicts(
            paths,
            marker_count=args.marker_count,
            policy=load_json(args.policy),
        )
        write_json(args.output, result)
        append_github_output(result)
        print(json.dumps(result, indent=2))
        return 0

    result = validate_agent_outputs(load_json(args.resolver), load_json(args.reviewer))
    write_json(args.output, result)
    append_github_output(result)
    print(json.dumps(result, indent=2))
    if not result["approved"]:
        raise ControlError("agent review gate stopped promotion: " + "; ".join(result["reasons"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(str(exc)) from exc
