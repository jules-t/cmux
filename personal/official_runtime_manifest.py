from __future__ import annotations

import argparse
import json
import re
from typing import Any

from personal.common import ControlError, TAG_RE, load_json


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_PLATFORMS = {
    ("darwin", "arm64"),
    ("darwin", "amd64"),
    ("linux", "arm64"),
    ("linux", "amd64"),
}


def stable_version(base_tag: str) -> str:
    match = TAG_RE.fullmatch(base_tag)
    if not match:
        raise ControlError(f"invalid official stable tag: {base_tag!r}")
    return ".".join(match.groups()[:3])


def validate_official_runtime_manifest(
    manifest: dict[str, Any],
    *,
    repository: str,
    base_tag: str,
) -> dict[str, Any]:
    version = stable_version(base_tag)
    release_url = f"https://github.com/{repository}/releases/download/{base_tag}"
    expected_root = {
        "schemaVersion": 1,
        "appVersion": version,
        "releaseTag": base_tag,
        "releaseURL": release_url,
        "checksumsAssetName": "cmuxd-remote-checksums.txt",
        "checksumsURL": f"{release_url}/cmuxd-remote-checksums.txt",
    }
    for key, expected in expected_root.items():
        if manifest.get(key) != expected:
            raise ControlError(
                f"official runtime manifest {key} is {manifest.get(key)!r}, "
                f"expected {expected!r}"
            )

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ControlError("official runtime manifest entries must be a list")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ControlError("official runtime manifest contains a non-object entry")
        platform = (str(entry.get("goOS", "")), str(entry.get("goArch", "")))
        if platform not in REQUIRED_PLATFORMS:
            raise ControlError(f"official runtime manifest has an unexpected platform: {platform}")
        if platform in seen:
            raise ControlError(f"official runtime manifest repeats platform: {platform}")
        seen.add(platform)
        asset_name = f"cmuxd-remote-{platform[0]}-{platform[1]}"
        if entry.get("assetName") != asset_name:
            raise ControlError(f"official runtime manifest has an invalid asset for {platform}")
        if entry.get("downloadURL") != f"{release_url}/{asset_name}":
            raise ControlError(f"official runtime manifest has an invalid URL for {platform}")
        if not SHA256_RE.fullmatch(str(entry.get("sha256", ""))):
            raise ControlError(f"official runtime manifest has an invalid digest for {platform}")
    if seen != REQUIRED_PLATFORMS:
        missing = sorted(REQUIRED_PLATFORMS - seen)
        raise ControlError(f"official runtime manifest is missing platforms: {missing}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the official cmux runtime payload manifest."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-tag", required=True)
    args = parser.parse_args()
    manifest = validate_official_runtime_manifest(
        load_json(args.manifest),
        repository=args.repository,
        base_tag=args.base_tag,
    )
    print(json.dumps(manifest, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"official runtime manifest blocked: {exc}") from exc
