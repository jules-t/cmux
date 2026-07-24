from __future__ import annotations

import argparse
import json
import os
import subprocess

from personal.common import ControlError


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
    args = parser.parse_args()

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
        "number,title",
    )
    issues = json.loads(existing.stdout)
    match = next((issue for issue in issues if issue.get("title") == args.title), None)
    if match:
        gh(
            "issue",
            "comment",
            str(match["number"]),
            "--repo",
            args.repo,
            "--body",
            args.body,
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
        args.body,
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
