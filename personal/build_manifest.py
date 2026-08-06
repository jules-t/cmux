from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re

from personal.common import ControlError, load_json, utc_now, write_json


SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NIGHTLY_RUNTIME_ASSET_RE = re.compile(
    r"^cmuxd-remote-manifest-[1-9][0-9]+\.json$"
)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the cmux Personal release manifest.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--base-tag", required=True)
    parser.add_argument("--personal-tag", required=True)
    parser.add_argument("--upstream-main-sha", default="")
    parser.add_argument("--runtime-manifest-asset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checksum-output", required=True)
    args = parser.parse_args()

    config = load_json(args.config)
    archive = pathlib.Path(args.archive)
    if not archive.is_file():
        raise ControlError(f"archive not found: {archive}")
    digest = sha256(archive)
    if not SOURCE_SHA_RE.fullmatch(args.source_sha):
        raise ControlError("source SHA is not a full lowercase commit SHA")
    if args.upstream_main_sha and not SOURCE_SHA_RE.fullmatch(args.upstream_main_sha):
        raise ControlError("upstream main SHA is not a full lowercase commit SHA")
    if args.upstream_main_sha:
        if not NIGHTLY_RUNTIME_ASSET_RE.fullmatch(args.runtime_manifest_asset):
            raise ControlError("main build runtime manifest asset is not immutable")
    elif args.runtime_manifest_asset != "cmuxd-remote-manifest.json":
        raise ControlError("stable build runtime manifest asset is not canonical")
    manifest = {
        "schema_version": 1,
        "build_origin": (
            os.environ.get("CMUX_PERSONAL_BUILD_ORIGIN")
            or ("github_actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local")
        ),
        "repository": config["fork_repository"],
        "upstream_repository": config["upstream_repository"],
        "app_name": config["app_name"],
        "bundle_identifier": config["bundle_identifier"],
        "architecture": config["architecture"],
        "archive_name": archive.name,
        "archive_sha256": digest,
        "archive_size": archive.stat().st_size,
        "source_sha": args.source_sha,
        "base_tag": args.base_tag,
        "personal_tag": args.personal_tag,
        "runtime_manifest_asset": args.runtime_manifest_asset,
        "workflow": os.environ.get("GITHUB_WORKFLOW_REF"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "built_at": utc_now(),
        "builder_os": platform.platform(),
    }
    if args.upstream_main_sha:
        manifest["upstream_main_sha"] = args.upstream_main_sha
    write_json(args.output, manifest)
    checksum = pathlib.Path(args.checksum_output)
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"manifest generation blocked: {exc}") from exc
