from __future__ import annotations

import argparse
import hashlib
import pathlib

from personal.common import ControlError, load_json


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_assets(
    directory: pathlib.Path,
    *,
    config: dict,
    source_sha: str,
    base_tag: str,
    personal_tag: str,
) -> dict:
    archive_name = str(config["artifact_name"])
    manifest_name = str(config["manifest_name"])
    archive = directory / archive_name
    manifest_path = directory / manifest_name
    checksum_path = directory / f"{archive_name}.sha256"
    for path in (archive, manifest_path, checksum_path):
        if not path.is_file():
            raise ControlError(f"release asset is missing: {path.name}")
    manifest = load_json(manifest_path)
    expected = {
        "repository": config["fork_repository"],
        "bundle_identifier": config["bundle_identifier"],
        "architecture": config["architecture"],
        "archive_name": archive_name,
        "source_sha": source_sha,
        "base_tag": base_tag,
        "personal_tag": personal_tag,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ControlError(
                f"manifest {key} is {manifest.get(key)!r}, expected {value!r}"
            )
    digest = sha256(archive)
    if manifest.get("archive_sha256") != digest:
        raise ControlError("archive digest does not match the manifest")
    if manifest.get("archive_size") != archive.stat().st_size:
        raise ControlError("archive size does not match the manifest")
    checksum_fields = checksum_path.read_text(encoding="utf-8").strip().split()
    if checksum_fields != [digest, archive_name]:
        raise ControlError("checksum file does not match the archive")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify assets before provenance attestation.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--base-tag", required=True)
    parser.add_argument("--personal-tag", required=True)
    args = parser.parse_args()
    verify_assets(
        pathlib.Path(args.directory),
        config=load_json(args.config),
        source_sha=args.source_sha,
        base_tag=args.base_tag,
        personal_tag=args.personal_tag,
    )
    print("release assets: verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, OSError, KeyError) as exc:
        raise SystemExit(f"release asset verification blocked: {exc}") from exc
