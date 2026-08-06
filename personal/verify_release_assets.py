from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import plistlib
import re
import zipfile

from personal.common import ControlError, load_json
from personal.official_runtime_manifest import validate_official_runtime_manifest


SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NIGHTLY_RUNTIME_ASSET_RE = re.compile(
    r"^cmuxd-remote-manifest-([1-9][0-9]+)\.json$"
)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_arm64_macho(archive: zipfile.ZipFile, member: str) -> None:
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise ControlError(f"app archive is missing executable: {member}") from exc
    mode = info.external_attr >> 16
    if mode and mode & 0o111 == 0:
        raise ControlError(f"app archive member is not executable: {member}")
    with archive.open(info) as handle:
        header = handle.read(8)
    if len(header) != 8:
        raise ControlError(f"app archive executable is truncated: {member}")
    magic = int.from_bytes(header[:4], "little")
    cpu_type = int.from_bytes(header[4:8], "little")
    if magic != 0xFEEDFACF or cpu_type != 0x0100000C:
        raise ControlError(f"app archive executable is not a thin arm64 Mach-O: {member}")


def verify_archive_bundle(
    archive_path: pathlib.Path,
    *,
    config: dict,
    source_sha: str,
    base_tag: str,
    personal_tag: str,
    upstream_main_sha: str | None = None,
    runtime_manifest_asset: str | None = None,
) -> None:
    app_root = f"{config['app_name']}.app/Contents"
    plist_member = f"{app_root}/Info.plist"
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                candidate = pathlib.PurePosixPath(info.filename)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise ControlError(f"app archive contains an unsafe path: {info.filename}")
            try:
                plist_payload = archive.read(plist_member)
            except KeyError as exc:
                raise ControlError("app archive has no Info.plist") from exc
            try:
                plist = plistlib.loads(plist_payload)
            except plistlib.InvalidFileException as exc:
                raise ControlError(f"app archive has an invalid Info.plist: {exc}") from exc
            expected_plist = {
                "CFBundleIdentifier": config["bundle_identifier"],
                "CMUXPersonalSourceSHA": source_sha,
                "CMUXPersonalBaseTag": base_tag,
                "CMUXPersonalReleaseTag": personal_tag,
            }
            if upstream_main_sha:
                if not SOURCE_SHA_RE.fullmatch(upstream_main_sha):
                    raise ControlError("upstream main SHA is not a full lowercase commit SHA")
                expected_plist["CMUXPersonalUpstreamMainSHA"] = upstream_main_sha
            elif "CMUXPersonalUpstreamMainSHA" in plist:
                raise ControlError(
                    "stable app archive unexpectedly declares an upstream main SHA"
                )
            for key, expected in expected_plist.items():
                if plist.get(key) != expected:
                    raise ControlError(
                        f"app archive {key} is {plist.get(key)!r}, expected {expected!r}"
                    )
            if "SUFeedURL" in plist or "SUPublicEDKey" in plist:
                raise ControlError("app archive still contains the official Sparkle update identity")
            raw_runtime_manifest = plist.get("CMUXRemoteDaemonManifestJSON")
            if not isinstance(raw_runtime_manifest, str):
                raise ControlError("app archive has no embedded remote runtime manifest")
            try:
                runtime_manifest = json.loads(raw_runtime_manifest)
            except json.JSONDecodeError as exc:
                raise ControlError(
                    f"app archive has an invalid remote runtime manifest: {exc}"
                ) from exc
            if not isinstance(runtime_manifest, dict):
                raise ControlError("app archive remote runtime manifest is not an object")
            runtime_build = None
            if upstream_main_sha and runtime_manifest_asset:
                match = NIGHTLY_RUNTIME_ASSET_RE.fullmatch(runtime_manifest_asset)
                if not match:
                    raise ControlError(
                        "main app archive does not name an immutable runtime manifest"
                    )
                runtime_build = match.group(1)
            validate_official_runtime_manifest(
                runtime_manifest,
                repository=str(config["upstream_repository"]),
                base_tag=base_tag,
                channel="nightly" if upstream_main_sha else "stable",
                runtime_build=runtime_build,
            )

            binaries = [
                f"{app_root}/MacOS/cmux",
                f"{app_root}/Resources/bin/cmux",
                f"{app_root}/Resources/bin/ghostty",
                f"{app_root}/Resources/bin/cmux-diff-sidecar",
            ]
            for member in binaries:
                require_arm64_macho(archive, member)
            helper = archive.read(f"{app_root}/Resources/bin/ghostty")
            if b"ghostty CLI helper stub" in helper:
                raise ControlError("app archive contains the placeholder Ghostty CLI helper")
    except zipfile.BadZipFile as exc:
        raise ControlError(f"app archive is not a valid ZIP: {exc}") from exc


def verify_assets(
    directory: pathlib.Path,
    *,
    config: dict,
    source_sha: str,
    base_tag: str,
    personal_tag: str,
    upstream_main_sha: str | None = None,
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
        "upstream_repository": config["upstream_repository"],
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
    build_origin = manifest.get("build_origin")
    if build_origin not in {None, "local", "github_actions"}:
        raise ControlError(f"release manifest build origin is invalid: {build_origin!r}")
    if build_origin == "local" and any(
        manifest.get(field) is not None
        for field in ("workflow", "workflow_run_id", "workflow_run_attempt")
    ):
        raise ControlError(
            "local release manifest unexpectedly declares a GitHub build run"
        )
    if upstream_main_sha:
        if not SOURCE_SHA_RE.fullmatch(upstream_main_sha):
            raise ControlError("upstream main SHA is not a full lowercase commit SHA")
        if manifest.get("upstream_main_sha") != upstream_main_sha:
            raise ControlError(
                "release manifest upstream main SHA does not match the requested source base"
            )
    elif manifest.get("upstream_main_sha") is not None:
        raise ControlError("stable release manifest unexpectedly declares an upstream main SHA")
    runtime_manifest_asset = manifest.get("runtime_manifest_asset")
    if runtime_manifest_asset is not None:
        if upstream_main_sha:
            if not isinstance(runtime_manifest_asset, str) or not NIGHTLY_RUNTIME_ASSET_RE.fullmatch(
                runtime_manifest_asset
            ):
                raise ControlError(
                    "release manifest does not name an immutable nightly runtime"
                )
        elif runtime_manifest_asset != "cmuxd-remote-manifest.json":
            raise ControlError(
                "stable release manifest does not name the canonical runtime"
            )
    digest = sha256(archive)
    if manifest.get("archive_sha256") != digest:
        raise ControlError("archive digest does not match the manifest")
    if manifest.get("archive_size") != archive.stat().st_size:
        raise ControlError("archive size does not match the manifest")
    checksum_fields = checksum_path.read_text(encoding="utf-8").strip().split()
    if checksum_fields != [digest, archive_name]:
        raise ControlError("checksum file does not match the archive")
    verify_archive_bundle(
        archive,
        config=config,
        source_sha=source_sha,
        base_tag=base_tag,
        personal_tag=personal_tag,
        upstream_main_sha=upstream_main_sha,
        runtime_manifest_asset=(
            str(runtime_manifest_asset) if runtime_manifest_asset is not None else None
        ),
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify assets before provenance attestation.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--base-tag", required=True)
    parser.add_argument("--personal-tag", required=True)
    parser.add_argument("--upstream-main-sha", default="")
    args = parser.parse_args()
    verify_assets(
        pathlib.Path(args.directory),
        config=load_json(args.config),
        source_sha=args.source_sha,
        base_tag=args.base_tag,
        personal_tag=args.personal_tag,
        upstream_main_sha=args.upstream_main_sha or None,
    )
    print("release assets: verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, OSError, KeyError) as exc:
        raise SystemExit(f"release asset verification blocked: {exc}") from exc
