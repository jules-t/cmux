from __future__ import annotations

import argparse
import json
import re
from typing import Any

from personal.common import ControlError, TAG_RE, load_json


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_BUILD_RE = re.compile(r"^[1-9][0-9]+$")
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


def nightly_runtime_build(
    manifest: dict[str, Any],
    *,
    repository: str,
    base_tag: str,
) -> str:
    version = stable_version(base_tag)
    prefix = f"{version}-nightly."
    app_version = manifest.get("appVersion")
    if not isinstance(app_version, str) or not app_version.startswith(prefix):
        raise ControlError(
            f"official nightly runtime appVersion is {app_version!r}, "
            f"expected {prefix}<build>"
        )
    runtime_build = app_version.removeprefix(prefix)
    if not RUNTIME_BUILD_RE.fullmatch(runtime_build):
        raise ControlError("official nightly runtime build is not a positive numeric build ID")
    validate_official_runtime_manifest(
        manifest,
        repository=repository,
        base_tag=base_tag,
        channel="nightly",
        runtime_build=runtime_build,
    )
    return runtime_build


def validate_official_runtime_manifest(
    manifest: dict[str, Any],
    *,
    repository: str,
    base_tag: str,
    channel: str = "auto",
    runtime_build: str | None = None,
) -> dict[str, Any]:
    if channel not in {"auto", "stable", "nightly"}:
        raise ControlError(f"unsupported official runtime channel: {channel!r}")
    detected_channel = "nightly" if manifest.get("releaseTag") == "nightly" else "stable"
    if channel != "auto" and detected_channel != channel:
        raise ControlError(
            f"official runtime manifest is for {detected_channel}, expected {channel}"
        )

    version = stable_version(base_tag)
    if detected_channel == "nightly":
        if runtime_build is None:
            prefix = f"{version}-nightly."
            app_version = str(manifest.get("appVersion", ""))
            runtime_build = app_version.removeprefix(prefix)
        if not RUNTIME_BUILD_RE.fullmatch(runtime_build or ""):
            raise ControlError("official nightly runtime build is not a positive numeric build ID")
        release_tag = "nightly"
        app_version = f"{version}-nightly.{runtime_build}"
        asset_suffix = f"-{runtime_build}"
    else:
        if runtime_build is not None:
            raise ControlError("stable runtime manifests cannot specify a nightly build ID")
        release_tag = base_tag
        app_version = version
        asset_suffix = ""

    release_url = f"https://github.com/{repository}/releases/download/{release_tag}"
    expected_root = {
        "schemaVersion": 1,
        "appVersion": app_version,
        "releaseTag": release_tag,
        "releaseURL": release_url,
        "checksumsAssetName": f"cmuxd-remote-checksums{asset_suffix}.txt",
        "checksumsURL": f"{release_url}/cmuxd-remote-checksums{asset_suffix}.txt",
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
        asset_name = f"cmuxd-remote-{platform[0]}-{platform[1]}{asset_suffix}"
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
    parser.add_argument(
        "--channel",
        choices=("auto", "stable", "nightly"),
        default="auto",
    )
    parser.add_argument("--runtime-build")
    parser.add_argument("--print-runtime-build", action="store_true")
    args = parser.parse_args()
    value = load_json(args.manifest)
    manifest = validate_official_runtime_manifest(
        value,
        repository=args.repository,
        base_tag=args.base_tag,
        channel=args.channel,
        runtime_build=args.runtime_build,
    )
    if args.print_runtime_build:
        print(
            nightly_runtime_build(
                value,
                repository=args.repository,
                base_tag=args.base_tag,
            )
        )
    else:
        print(json.dumps(manifest, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"official runtime manifest blocked: {exc}") from exc
