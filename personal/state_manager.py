from __future__ import annotations

import argparse

from personal.common import ControlError, load_json, require_release_tag, utc_now, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Update persisted cmux Personal release state.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mark = subparsers.add_parser("mark-attempt")
    mark.add_argument("--state", required=True)
    mark.add_argument("--tag", required=True)
    mark.add_argument("--status", required=True)
    mark.add_argument("--run-id")

    promote = subparsers.add_parser("promote")
    promote.add_argument("--state", required=True)
    promote.add_argument("--base-tag", required=True)
    promote.add_argument("--personal-tag", required=True)
    promote.add_argument("--source-sha", required=True)
    promote.add_argument("--run-id")

    args = parser.parse_args()
    state = load_json(args.state)
    now = utc_now()
    if args.command == "mark-attempt":
        tag = require_release_tag(args.tag)
        state["attempt"] = {
            "tag": tag,
            "status": args.status,
            "workflow_run_id": args.run_id,
            "updated_at": now,
        }
    else:
        base_tag = require_release_tag(args.base_tag)
        state["current_stable_tag"] = base_tag
        state["last_promoted_personal_tag"] = args.personal_tag
        state["last_promoted_source_sha"] = args.source_sha
        state["pending_observation"] = {
            "tag": None,
            "count": 0,
            "first_seen_at": None,
            "last_seen_at": None,
        }
        state["attempt"] = {
            "tag": base_tag,
            "status": "promoted",
            "workflow_run_id": args.run_id,
            "updated_at": now,
        }
    write_json(args.state, state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"state update blocked: {exc}") from exc
