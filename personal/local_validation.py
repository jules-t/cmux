from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

from personal.common import ControlError, load_json, utc_now, write_json
from personal.local_candidate import validate_candidate, validate_record
from personal.verify_release_assets import verify_assets


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha(value: object, label: str) -> str:
    rendered = str(value)
    if not SHA_RE.fullmatch(rendered):
        raise ControlError(f"{label} is not a full lowercase commit SHA")
    return rendered


def verify_bundle(bundle: pathlib.Path, source_sha: str) -> str:
    if not bundle.is_file():
        raise ControlError(f"candidate bundle not found: {bundle}")
    result = subprocess.run(
        ["git", "bundle", "list-heads", str(bundle)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ControlError(
            "candidate bundle is invalid: "
            + (result.stderr.strip() or result.stdout.strip())
        )
    heads = [line.split()[0] for line in result.stdout.splitlines() if line.split()]
    if source_sha not in heads:
        raise ControlError("candidate bundle does not contain the validated source commit")
    return sha256(bundle)


def create_receipt(args: argparse.Namespace) -> dict:
    source_sha = require_sha(args.source_sha, "source SHA")
    candidate_record = validate_record(load_json(args.candidate))
    candidate = candidate_record["candidate"]
    if candidate["source_sha"] != source_sha:
        raise ControlError("validated source SHA does not match the local candidate")
    if candidate["base_tag"] != args.base_tag:
        raise ControlError("validated base tag does not match the local candidate")
    upstream_main_sha = candidate["upstream_main_sha"]

    root = pathlib.Path(args.root).resolve()
    assets = pathlib.Path(args.assets).resolve()
    bundle = pathlib.Path(args.candidate_bundle).resolve()
    try:
        assets_relative = assets.relative_to(root)
        bundle_relative = bundle.relative_to(root)
    except ValueError as exc:
        raise ControlError("validated assets and candidate bundle must be inside the run root") from exc

    config = load_json(args.config)
    manifest = verify_assets(
        assets,
        config=config,
        source_sha=source_sha,
        base_tag=args.base_tag,
        personal_tag=args.personal_tag,
        upstream_main_sha=upstream_main_sha,
    )
    if manifest.get("build_origin") != "local":
        raise ControlError("local validation requires a locally built release manifest")
    if manifest.get("runtime_manifest_asset") != candidate["runtime_manifest_asset"]:
        raise ControlError(
            "built runtime manifest does not match the reviewed local candidate"
        )
    archive = assets / str(config["artifact_name"])
    receipt = {
        "schema_version": 1,
        "status": "validated",
        "validated_at": utc_now(),
        "candidate": candidate,
        "source_sha": source_sha,
        "base_tag": args.base_tag,
        "upstream_main_sha": upstream_main_sha,
        "runtime_manifest_asset": candidate["runtime_manifest_asset"],
        "personal_tag": args.personal_tag,
        "candidate_branch": candidate["candidate_branch"],
        "candidate_bundle": str(bundle_relative),
        "candidate_bundle_sha256": verify_bundle(bundle, source_sha),
        "assets_directory": str(assets_relative),
        "archive_name": archive.name,
        "archive_sha256": sha256(archive),
        "build_manifest_sha256": sha256(assets / str(config["manifest_name"])),
        "builder_os": manifest.get("builder_os"),
        "build_origin": "local",
    }
    write_json(args.output, receipt)
    return receipt


def verify_receipt(args: argparse.Namespace) -> dict:
    receipt_path = pathlib.Path(args.receipt).resolve()
    receipt = load_json(receipt_path)
    if receipt.get("schema_version") != 1 or receipt.get("status") != "validated":
        raise ControlError("local validation receipt is not a completed schema-v1 receipt")
    if receipt.get("build_origin") != "local":
        raise ControlError("validation receipt is not for a local build")
    candidate = validate_candidate(receipt.get("candidate"))
    source_sha = require_sha(receipt.get("source_sha"), "receipt source SHA")
    if candidate["source_sha"] != source_sha:
        raise ControlError("receipt source SHA differs from its local candidate")
    if candidate["base_tag"] != receipt.get("base_tag"):
        raise ControlError("receipt base tag differs from its local candidate")
    if candidate["candidate_branch"] != receipt.get("candidate_branch"):
        raise ControlError("receipt branch differs from its local candidate")
    upstream_main_sha = candidate["upstream_main_sha"]
    if upstream_main_sha != receipt.get("upstream_main_sha"):
        raise ControlError("receipt upstream main SHA differs from its local candidate")
    root = receipt_path.parent
    bundle = root / str(receipt.get("candidate_bundle", ""))
    if verify_bundle(bundle, source_sha) != receipt.get("candidate_bundle_sha256"):
        raise ControlError("candidate bundle changed after local validation")
    assets = root / str(receipt.get("assets_directory", ""))
    config = load_json(args.config)
    verify_assets(
        assets,
        config=config,
        source_sha=source_sha,
        base_tag=str(receipt.get("base_tag", "")),
        personal_tag=str(receipt.get("personal_tag", "")),
        upstream_main_sha=upstream_main_sha,
    )
    archive = assets / str(receipt.get("archive_name", ""))
    if sha256(archive) != receipt.get("archive_sha256"):
        raise ControlError("validated archive changed after local validation")
    manifest = assets / str(config["manifest_name"])
    if sha256(manifest) != receipt.get("build_manifest_sha256"):
        raise ControlError("build manifest changed after local validation")
    if candidate["runtime_manifest_asset"] != receipt.get("runtime_manifest_asset"):
        raise ControlError("receipt runtime manifest differs from its local candidate")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or verify a local cmux Personal build-validation receipt."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--root", required=True)
    create.add_argument("--assets", required=True)
    create.add_argument("--candidate-bundle", required=True)
    create.add_argument("--candidate", required=True)
    create.add_argument("--config", required=True)
    create.add_argument("--source-sha", required=True)
    create.add_argument("--base-tag", required=True)
    create.add_argument("--personal-tag", required=True)
    create.add_argument("--output", required=True)
    create.set_defaults(handler=create_receipt)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--config", required=True)
    verify.set_defaults(handler=verify_receipt)

    args = parser.parse_args()
    value = args.handler(args)
    print(json.dumps(value, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, KeyError, OSError) as exc:
        raise SystemExit(f"local validation blocked: {exc}") from exc
