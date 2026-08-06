from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

from personal.common import (
    ControlError,
    load_json,
    require_release_tag,
    utc_now,
    version_tuple,
    write_json,
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PREPARED_AT_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
IMMUTABLE_RUNTIME_RE = re.compile(
    r"^cmuxd-remote-manifest-[1-9][0-9]+\.json$"
)
CHANNELS = ("stable", "main")
INTEGRATION_STATUSES = ("clean", "resolved_and_reviewed")
CANDIDATE_KEYS = {
    "schema_version",
    "channel",
    "source_sha",
    "personal_source_sha",
    "target_sha",
    "candidate_branch",
    "base_tag",
    "upstream_main_sha",
    "runtime_manifest_asset",
    "integration_status",
    "prepared_at",
}
RECORD_KEYS = {"schema_version", "candidate", "run_directory"}


def require_sha(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ControlError(f"{context} is not a full lowercase commit SHA")
    return value


def require_candidate_branch(value: object, *, channel: str) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise ControlError("candidate branch is invalid")
    expected_prefix = "candidate/main-" if channel == "main" else "candidate/personal-"
    unsafe = (
        not value.startswith(expected_prefix)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)
        or "//" in value
        or ".." in value
        or "@{" in value
        or value.endswith(("/", ".", ".lock"))
        or any(
            part in {"", ".", ".."} or part.endswith(".lock")
            for part in value.split("/")
        )
    )
    if unsafe:
        raise ControlError(f"candidate branch is unsafe for the {channel} channel")
    return value


def validate_candidate(
    value: object, *, expected_channel: str | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlError("local candidate is not an object")
    if set(value) != CANDIDATE_KEYS:
        missing = sorted(CANDIDATE_KEYS - set(value))
        unexpected = sorted(set(value) - CANDIDATE_KEYS)
        raise ControlError(
            f"local candidate fields differ (missing={missing}, unexpected={unexpected})"
        )
    if value.get("schema_version") != 1:
        raise ControlError("local candidate is not schema version 1")
    channel = value.get("channel")
    if channel not in CHANNELS:
        raise ControlError(f"local candidate channel is invalid: {channel!r}")
    if expected_channel is not None and channel != expected_channel:
        raise ControlError(
            f"local candidate channel is {channel!r}, expected {expected_channel!r}"
        )

    source_sha = require_sha(value.get("source_sha"), context="candidate source SHA")
    personal_source_sha = require_sha(
        value.get("personal_source_sha"), context="candidate personal source SHA"
    )
    target_sha = require_sha(value.get("target_sha"), context="candidate target SHA")
    base_tag = require_release_tag(str(value.get("base_tag", "")))
    candidate_branch = require_candidate_branch(
        value.get("candidate_branch"), channel=str(channel)
    )
    integration_status = value.get("integration_status")
    if integration_status not in INTEGRATION_STATUSES:
        raise ControlError(
            f"candidate integration status is invalid: {integration_status!r}"
        )
    prepared_at = value.get("prepared_at")
    if not isinstance(prepared_at, str) or not PREPARED_AT_RE.fullmatch(prepared_at):
        raise ControlError("candidate preparation time is not an exact UTC timestamp")

    upstream_main_sha = value.get("upstream_main_sha")
    runtime_manifest_asset = value.get("runtime_manifest_asset")
    if channel == "main":
        upstream_main_sha = require_sha(
            upstream_main_sha, context="candidate upstream main SHA"
        )
        if target_sha != upstream_main_sha:
            raise ControlError("main candidate target differs from its published nightly SHA")
        if (
            not isinstance(runtime_manifest_asset, str)
            or not IMMUTABLE_RUNTIME_RE.fullmatch(runtime_manifest_asset)
        ):
            raise ControlError(
                "main candidate does not name an immutable nightly runtime manifest"
            )
    else:
        if upstream_main_sha is not None:
            raise ControlError("stable candidate unexpectedly names an upstream main SHA")
        if runtime_manifest_asset != "cmuxd-remote-manifest.json":
            raise ControlError(
                "stable candidate must use the official stable runtime manifest"
            )

    return {
        "schema_version": 1,
        "channel": channel,
        "source_sha": source_sha,
        "personal_source_sha": personal_source_sha,
        "target_sha": target_sha,
        "candidate_branch": candidate_branch,
        "base_tag": base_tag,
        "upstream_main_sha": upstream_main_sha,
        "runtime_manifest_asset": runtime_manifest_asset,
        "integration_status": integration_status,
        "prepared_at": prepared_at,
    }


def make_record(
    *,
    channel: str,
    source_sha: str,
    personal_source_sha: str,
    target_sha: str,
    candidate_branch: str,
    base_tag: str,
    runtime_manifest_asset: str,
    integration_status: str,
    run_directory: pathlib.Path,
    upstream_main_sha: str | None = None,
    prepared_at: str | None = None,
) -> dict[str, Any]:
    candidate = validate_candidate(
        {
            "schema_version": 1,
            "channel": channel,
            "source_sha": source_sha,
            "personal_source_sha": personal_source_sha,
            "target_sha": target_sha,
            "candidate_branch": candidate_branch,
            "base_tag": base_tag,
            "upstream_main_sha": upstream_main_sha,
            "runtime_manifest_asset": runtime_manifest_asset,
            "integration_status": integration_status,
            "prepared_at": prepared_at or utc_now(),
        },
        expected_channel=channel,
    )
    return {
        "schema_version": 1,
        "candidate": candidate,
        "run_directory": str(run_directory.resolve()),
    }


def validate_record(
    value: object,
    *,
    data_root: pathlib.Path | None = None,
    expected_channel: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != RECORD_KEYS:
        raise ControlError("local candidate record fields are invalid")
    if value.get("schema_version") != 1:
        raise ControlError("local candidate record is not schema version 1")
    candidate = validate_candidate(
        value.get("candidate"), expected_channel=expected_channel
    )
    raw_run_directory = value.get("run_directory")
    if not isinstance(raw_run_directory, str):
        raise ControlError("local candidate run directory is invalid")
    run_directory = pathlib.Path(raw_run_directory)
    if not run_directory.is_absolute():
        raise ControlError("local candidate run directory is not absolute")
    if data_root is not None:
        try:
            run_directory.resolve().relative_to(data_root.resolve())
        except ValueError as exc:
            raise ControlError("local candidate run directory escapes the data root") from exc
    if not (run_directory / "source" / ".git").exists():
        raise ControlError("local candidate source checkout is missing")
    if not (run_directory / "candidate.bundle").is_file():
        raise ControlError("local candidate bundle is missing")
    return {
        "schema_version": 1,
        "candidate": candidate,
        "run_directory": str(run_directory.resolve()),
    }


def read_candidates(
    data_root: pathlib.Path, *, personal_source_sha: str
) -> list[dict[str, Any]]:
    personal_source_sha = require_sha(
        personal_source_sha, context="current personal/stable SHA"
    )
    records: list[dict[str, Any]] = []
    for channel in CHANNELS:
        path = data_root / "candidates" / f"{channel}.json"
        if not path.is_file():
            continue
        record = validate_record(
            load_json(path), data_root=data_root, expected_channel=channel
        )
        if record["candidate"]["personal_source_sha"] == personal_source_sha:
            records.append(record)
    return records


def select_candidate(
    data_root: pathlib.Path,
    *,
    personal_source_sha: str,
    channel: str = "auto",
    target_sha: str | None = None,
    minimum_base_tag: str | None = None,
) -> dict[str, Any]:
    if channel not in ("auto", *CHANNELS):
        raise ControlError(f"candidate channel is invalid: {channel!r}")
    if target_sha is not None:
        target_sha = require_sha(target_sha, context="expected candidate target SHA")
    if minimum_base_tag is not None:
        minimum_base_tag = require_release_tag(minimum_base_tag)
    records = read_candidates(data_root, personal_source_sha=personal_source_sha)
    if channel != "auto":
        records = [
            record for record in records if record["candidate"]["channel"] == channel
        ]
    if target_sha is not None:
        records = [
            record
            for record in records
            if record["candidate"]["target_sha"] == target_sha
        ]
    if minimum_base_tag is not None:
        records = [
            record
            for record in records
            if version_tuple(record["candidate"]["base_tag"])
            >= version_tuple(minimum_base_tag)
        ]
    if not records:
        label = "reviewed" if channel == "auto" else f"reviewed {channel}"
        raise ControlError(
            f"no {label} candidate for the current personal/stable source is available"
        )
    return max(
        records,
        key=lambda record: (
            version_tuple(record["candidate"]["base_tag"]),
            record["candidate"]["channel"] == "main",
            record["candidate"]["prepared_at"],
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record and select locally reviewed cmux Personal candidates."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--channel", choices=CHANNELS, required=True)
    create.add_argument("--source-sha", required=True)
    create.add_argument("--personal-source-sha", required=True)
    create.add_argument("--target-sha", required=True)
    create.add_argument("--candidate-branch", required=True)
    create.add_argument("--base-tag", required=True)
    create.add_argument("--upstream-main-sha", default="")
    create.add_argument("--runtime-manifest-asset", required=True)
    create.add_argument(
        "--integration-status", choices=INTEGRATION_STATUSES, required=True
    )
    create.add_argument("--run-directory", required=True)
    create.add_argument("--output", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--data-root", required=True)
    select.add_argument("--personal-source-sha", required=True)
    select.add_argument("--channel", choices=("auto", *CHANNELS), default="auto")
    select.add_argument("--target-sha")
    select.add_argument("--minimum-base-tag")
    select.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--record", required=True)
    verify.add_argument("--data-root")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "create":
        record = make_record(
            channel=args.channel,
            source_sha=args.source_sha,
            personal_source_sha=args.personal_source_sha,
            target_sha=args.target_sha,
            candidate_branch=args.candidate_branch,
            base_tag=args.base_tag,
            upstream_main_sha=args.upstream_main_sha or None,
            runtime_manifest_asset=args.runtime_manifest_asset,
            integration_status=args.integration_status,
            run_directory=pathlib.Path(args.run_directory),
        )
        record = validate_record(record)
        write_json(args.output, record)
    elif args.command == "select":
        record = select_candidate(
            pathlib.Path(args.data_root),
            personal_source_sha=args.personal_source_sha,
            channel=args.channel,
            target_sha=args.target_sha,
            minimum_base_tag=args.minimum_base_tag,
        )
        write_json(args.output, record)
    else:
        record = validate_record(
            load_json(args.record),
            data_root=pathlib.Path(args.data_root) if args.data_root else None,
        )
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, OSError) as exc:
        raise SystemExit(f"local candidate blocked: {exc}") from exc
