from __future__ import annotations

import argparse
import re
from typing import Any

from personal.common import (
    PERSONAL_TAG_RE,
    ControlError,
    load_json,
    require_release_tag,
    utc_now,
    version_tuple,
    write_json,
)


SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")


def personal_tag_key(personal_tag: str, base_tag: str) -> tuple[int, int, int, int]:
    base_tag = require_release_tag(base_tag)
    match = PERSONAL_TAG_RE.fullmatch(personal_tag)
    if not match or not personal_tag.startswith(f"personal-{base_tag}-r"):
        raise ControlError(
            f"personal release tag {personal_tag!r} does not belong to {base_tag!r}"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def require_source_sha(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not SOURCE_SHA_RE.fullmatch(value):
        raise ControlError(f"{context} is not a full lowercase commit SHA")
    return value


def require_run_id(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
        raise ControlError(f"{context} is not a positive workflow run ID")
    return value


def published_identity(
    state: dict[str, Any],
) -> tuple[str, str, str] | None:
    personal_tag = state.get("last_promoted_personal_tag")
    if personal_tag is None:
        return None
    if not isinstance(personal_tag, str):
        raise ControlError("persisted personal release tag is invalid")
    base_tag = state.get("last_published_base_tag", state.get("current_stable_tag"))
    if not isinstance(base_tag, str):
        raise ControlError("persisted published base tag is invalid")
    base_tag = require_release_tag(base_tag)
    source_sha = require_source_sha(
        state.get("last_promoted_source_sha"),
        context="persisted published source SHA",
    )
    personal_tag_key(personal_tag, base_tag)
    return base_tag, personal_tag, source_sha


def claimed_identity(
    state: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    claim = state.get("publication_claim")
    if claim is None:
        return None
    if not isinstance(claim, dict):
        raise ControlError("persisted publication claim is invalid")
    base_tag = claim.get("base_tag")
    personal_tag = claim.get("personal_tag")
    if not isinstance(base_tag, str) or not isinstance(personal_tag, str):
        raise ControlError("persisted publication claim has invalid tags")
    base_tag = require_release_tag(base_tag)
    source_sha = require_source_sha(
        claim.get("source_sha"),
        context="persisted publication claim source SHA",
    )
    personal_tag_key(personal_tag, base_tag)
    run_id = require_run_id(
        claim.get("workflow_run_id"),
        context="persisted publication claim workflow run ID",
    )
    return base_tag, personal_tag, source_sha, run_id


def identity_is_exact(
    identity: tuple[str, str, str] | tuple[str, str, str, str] | None,
    *,
    base_tag: str,
    personal_tag: str,
    source_sha: str,
) -> bool:
    return identity is not None and identity[:3] == (
        base_tag,
        personal_tag,
        source_sha,
    )


def compare_identity(
    *,
    label: str,
    requested_key: tuple[int, int, int, int],
    base_tag: str,
    personal_tag: str,
    source_sha: str,
    existing: tuple[str, str, str],
) -> None:
    existing_base, existing_tag, existing_source = existing
    existing_key = personal_tag_key(existing_tag, existing_base)
    if requested_key < existing_key:
        raise ControlError(
            f"stale publication {personal_tag} would replace newer {label} {existing_tag}"
        )
    if requested_key == existing_key and (
        existing_base != base_tag
        or existing_tag != personal_tag
        or existing_source != source_sha
    ):
        raise ControlError(
            f"publication identity for {personal_tag} conflicts with persisted {label}"
        )


def validate_promotion(
    state: dict[str, Any],
    *,
    base_tag: str,
    personal_tag: str,
    source_sha: str,
) -> None:
    base_tag = require_release_tag(base_tag)
    requested_key = personal_tag_key(personal_tag, base_tag)
    source_sha = require_source_sha(source_sha, context="requested personal source SHA")
    current_stable = state.get("current_stable_tag")
    if not isinstance(current_stable, str):
        raise ControlError("persisted stable release tag is invalid")
    current_stable = require_release_tag(current_stable)
    if version_tuple(base_tag) < version_tuple(current_stable):
        raise ControlError(
            f"publication base {base_tag} is older than personal/stable base {current_stable}"
        )

    published = published_identity(state)
    if published is not None:
        compare_identity(
            label="published release",
            requested_key=requested_key,
            base_tag=base_tag,
            personal_tag=personal_tag,
            source_sha=source_sha,
            existing=published,
        )
    claim = claimed_identity(state)
    if claim is not None:
        compare_identity(
            label="publication claim",
            requested_key=requested_key,
            base_tag=base_tag,
            personal_tag=personal_tag,
            source_sha=source_sha,
            existing=claim[:3],
        )


def claim_publication(
    state: dict[str, Any],
    *,
    base_tag: str,
    personal_tag: str,
    source_sha: str,
    run_id: str | None,
    updated_at: str,
) -> None:
    run_id = require_run_id(run_id, context="publication claim workflow run ID")
    validate_promotion(
        state,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    )
    published = published_identity(state)
    claim = claimed_identity(state)
    if identity_is_exact(
        published,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    ) and claim is None:
        return
    if identity_is_exact(
        claim,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    ):
        if claim is not None and claim[3] != run_id:
            raise ControlError(
                f"publication claim for {personal_tag} belongs to workflow run {claim[3]}"
            )
        return
    state["publication_claim"] = {
        "base_tag": base_tag,
        "personal_tag": personal_tag,
        "source_sha": source_sha,
        "workflow_run_id": run_id,
        "claimed_at": updated_at,
    }


def release_publication_claim(
    state: dict[str, Any],
    *,
    base_tag: str,
    personal_tag: str,
    source_sha: str,
    run_id: str | None,
) -> bool:
    run_id = require_run_id(run_id, context="publication workflow run ID")
    claim = claimed_identity(state)
    if claim is None:
        return False
    if not identity_is_exact(
        claim,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    ):
        raise ControlError(
            f"publication claim {claim[1]} does not belong to {personal_tag}"
        )
    if claim[3] != run_id:
        raise ControlError(
            f"publication claim for {personal_tag} belongs to workflow run {claim[3]}"
        )
    state["publication_claim"] = None
    return True


def finalize_publication(
    state: dict[str, Any],
    *,
    base_tag: str,
    personal_tag: str,
    source_sha: str,
    personal_stable_sha: str,
    run_id: str | None,
    updated_at: str,
    upstream_main_sha: str | None = None,
) -> None:
    run_id = require_run_id(run_id, context="publication workflow run ID")
    validate_promotion(
        state,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    )
    personal_stable_sha = require_source_sha(
        personal_stable_sha,
        context="observed personal/stable SHA",
    )
    if upstream_main_sha:
        upstream_main_sha = require_source_sha(
            upstream_main_sha,
            context="promoted upstream main SHA",
        )
    claim = claimed_identity(state)
    published = published_identity(state)
    if (
        identity_is_exact(
            claim,
            base_tag=base_tag,
            personal_tag=personal_tag,
            source_sha=source_sha,
        )
        and claim is not None
        and claim[3] != run_id
    ):
        raise ControlError(
            f"publication claim for {personal_tag} belongs to workflow run {claim[3]}"
        )
    if not identity_is_exact(
        claim,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    ) and not identity_is_exact(
        published,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
    ):
        raise ControlError(
            f"publication {personal_tag} has no exact durable claim"
        )
    if (
        claim is None
        and identity_is_exact(
            published,
            base_tag=base_tag,
            personal_tag=personal_tag,
            source_sha=source_sha,
        )
        and state.get("personal_stable_sha") == personal_stable_sha
        and state.get("current_upstream_main_sha") == upstream_main_sha
    ):
        return

    state["last_published_base_tag"] = base_tag
    state["last_promoted_personal_tag"] = personal_tag
    state["last_promoted_source_sha"] = source_sha
    state["personal_stable_sha"] = personal_stable_sha
    state["publication_claim"] = None
    source_is_current = personal_stable_sha == source_sha
    if source_is_current:
        state["current_stable_tag"] = base_tag
        if upstream_main_sha:
            state["current_upstream_main_sha"] = upstream_main_sha
        else:
            state.pop("current_upstream_main_sha", None)
        state["pending_observation"] = {
            "tag": None,
            "count": 0,
            "first_seen_at": None,
            "last_seen_at": None,
        }
    state["attempt"] = {
        "tag": base_tag,
        "status": "promoted" if source_is_current else "published_without_source_promotion",
        "workflow_run_id": run_id,
        "updated_at": updated_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update persisted cmux Personal release state.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mark = subparsers.add_parser("mark-attempt")
    mark.add_argument("--state", required=True)
    mark.add_argument("--tag", required=True)
    mark.add_argument("--status", required=True)
    mark.add_argument("--run-id")

    check = subparsers.add_parser("check-promotion")
    check.add_argument("--state", required=True)
    check.add_argument("--base-tag", required=True)
    check.add_argument("--personal-tag", required=True)
    check.add_argument("--source-sha", required=True)

    claim = subparsers.add_parser("claim-publication")
    claim.add_argument("--state", required=True)
    claim.add_argument("--base-tag", required=True)
    claim.add_argument("--personal-tag", required=True)
    claim.add_argument("--source-sha", required=True)
    claim.add_argument("--run-id", required=True)

    unclaim = subparsers.add_parser(
        "release-claim",
        help="release this run's publication claim after a failure before exposure",
    )
    unclaim.add_argument("--state", required=True)
    unclaim.add_argument("--base-tag", required=True)
    unclaim.add_argument("--personal-tag", required=True)
    unclaim.add_argument("--source-sha", required=True)
    unclaim.add_argument("--run-id", required=True)

    finalize = subparsers.add_parser("finalize-publication")
    finalize.add_argument("--state", required=True)
    finalize.add_argument("--base-tag", required=True)
    finalize.add_argument("--personal-tag", required=True)
    finalize.add_argument("--source-sha", required=True)
    finalize.add_argument("--personal-stable-sha", required=True)
    finalize.add_argument("--upstream-main-sha", default="")
    finalize.add_argument("--run-id", required=True)

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
    elif args.command == "check-promotion":
        validate_promotion(
            state,
            base_tag=args.base_tag,
            personal_tag=args.personal_tag,
            source_sha=args.source_sha,
        )
        print(f"publication policy: {args.personal_tag} is monotonic")
        return 0
    elif args.command == "claim-publication":
        claim_publication(
            state,
            base_tag=args.base_tag,
            personal_tag=args.personal_tag,
            source_sha=args.source_sha,
            run_id=args.run_id,
            updated_at=now,
        )
    elif args.command == "release-claim":
        released = release_publication_claim(
            state,
            base_tag=args.base_tag,
            personal_tag=args.personal_tag,
            source_sha=args.source_sha,
            run_id=args.run_id,
        )
        print(
            f"publication claim for {args.personal_tag} released"
            if released
            else f"no publication claim was held for {args.personal_tag}"
        )
    else:
        finalize_publication(
            state,
            base_tag=args.base_tag,
            personal_tag=args.personal_tag,
            source_sha=args.source_sha,
            personal_stable_sha=args.personal_stable_sha,
            run_id=args.run_id,
            updated_at=now,
            upstream_main_sha=args.upstream_main_sha or None,
        )
    write_json(args.state, state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"state update blocked: {exc}") from exc
