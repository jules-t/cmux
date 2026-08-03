from __future__ import annotations

import argparse
import json
import subprocess

from personal.common import ControlError


MARKER_PREFIX = "<!-- cmux-personal-status-key:"
MARKER_SUFFIX = "-->"


def status_marker(dedupe_key: str) -> str:
    return f"{MARKER_PREFIX} {dedupe_key} {MARKER_SUFFIX}"


def marked_body(body: str, dedupe_key: str | None) -> str:
    if not dedupe_key:
        return body
    return f"{body}\n\n{status_marker(dedupe_key)}"


def gh(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["gh", *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise ControlError(result.stderr.strip() or result.stdout.strip())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a cmux Personal status issue.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--assignee")
    parser.add_argument(
        "--dedupe-key",
        help="Suppress repeat notifications while the blocked condition is unchanged.",
    )
    args = parser.parse_args()

    body = marked_body(args.body, args.dedupe_key)
    existing = gh(
        "issue",
        "list",
        "--repo",
        args.repo,
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title,body",
    )
    issues = json.loads(existing.stdout)
    match = next((issue for issue in issues if issue.get("title") == args.title), None)
    if match:
        already_reported = bool(args.dedupe_key) and status_marker(
            args.dedupe_key
        ) in (match.get("body") or "")
        edited = gh(
            "issue",
            "edit",
            str(match["number"]),
            "--repo",
            args.repo,
            "--body",
            body,
            check=False,
        )
        if edited.returncode != 0:
            detail = edited.stderr.strip() or edited.stdout.strip()
            print(f"warning: could not refresh issue body: {detail}")
        if already_reported:
            print(f"refreshed issue #{match['number']} without a repeat notification")
            return 0
        gh(
            "issue",
            "comment",
            str(match["number"]),
            "--repo",
            args.repo,
            "--body",
            body,
        )
        print(f"updated issue #{match['number']}")
        return 0

    command = [
        "issue",
        "create",
        "--repo",
        args.repo,
        "--title",
        args.title,
        "--body",
        body,
    ]
    if args.assignee:
        command.extend(["--assignee", args.assignee])
    result = gh(*command)
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, json.JSONDecodeError) as exc:
        raise SystemExit(f"status issue failed: {exc}") from exc
