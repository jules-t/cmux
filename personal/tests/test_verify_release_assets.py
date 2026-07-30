from __future__ import annotations

import hashlib
import json
import pathlib
import plistlib
import tempfile
import unittest
import zipfile

from personal.common import ControlError
from personal.tests.test_official_runtime_manifest import (
    manifest_fixture,
    nightly_manifest_fixture,
)
from personal.verify_release_assets import verify_assets


CONFIG = {
    "artifact_name": "cmux-personal-macos-arm64.zip",
    "manifest_name": "cmux-personal-manifest.json",
    "fork_repository": "jules-t/cmux",
    "upstream_repository": "manaflow-ai/cmux",
    "app_name": "cmux Personal",
    "bundle_identifier": "com.cmuxterm.app.staging.personal",
    "architecture": "arm64",
}


def write_executable(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = 0o100755 << 16
    archive.writestr(info, payload)


def write_app_archive(
    path: pathlib.Path,
    *,
    source_sha: str,
    upstream_main_sha: str | None = None,
    runtime_manifest: dict | None = None,
) -> None:
    app_root = "cmux Personal.app/Contents"
    plist = {
        "CFBundleIdentifier": CONFIG["bundle_identifier"],
        "CMUXPersonalSourceSHA": source_sha,
        "CMUXPersonalBaseTag": "v0.64.20",
        "CMUXPersonalReleaseTag": "personal-v0.64.20-r1",
        "CMUXRemoteDaemonManifestJSON": json.dumps(runtime_manifest or manifest_fixture()),
    }
    if upstream_main_sha:
        plist["CMUXPersonalUpstreamMainSHA"] = upstream_main_sha
    arm64_header = (
        (0xFEEDFACF).to_bytes(4, "little")
        + (0x0100000C).to_bytes(4, "little")
        + b"\0" * 32
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{app_root}/Info.plist", plistlib.dumps(plist))
        for relative in (
            "MacOS/cmux",
            "Resources/bin/cmux",
            "Resources/bin/ghostty",
            "Resources/bin/cmux-diff-sidecar",
        ):
            write_executable(archive, f"{app_root}/{relative}", arm64_header)


class VerifyReleaseAssetsTests(unittest.TestCase):
    def test_verifies_cross_asset_identity_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / CONFIG["artifact_name"]
            write_app_archive(archive, source_sha="abc")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = {
                "repository": "jules-t/cmux",
                "upstream_repository": "manaflow-ai/cmux",
                "bundle_identifier": "com.cmuxterm.app.staging.personal",
                "architecture": "arm64",
                "archive_name": archive.name,
                "archive_sha256": digest,
                "archive_size": archive.stat().st_size,
                "source_sha": "abc",
                "base_tag": "v0.64.20",
                "personal_tag": "personal-v0.64.20-r1",
            }
            (root / CONFIG["manifest_name"]).write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (root / f"{archive.name}.sha256").write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )
            verified = verify_assets(
                root,
                config=CONFIG,
                source_sha="abc",
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r1",
            )
            self.assertEqual(verified["archive_sha256"], digest)

    def test_verifies_a_main_based_archive_and_nightly_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / CONFIG["artifact_name"]
            upstream_sha = "a" * 40
            write_app_archive(
                archive,
                source_sha="candidate",
                upstream_main_sha=upstream_sha,
                runtime_manifest=nightly_manifest_fixture(),
            )
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = {
                "repository": CONFIG["fork_repository"],
                "upstream_repository": CONFIG["upstream_repository"],
                "bundle_identifier": CONFIG["bundle_identifier"],
                "architecture": CONFIG["architecture"],
                "archive_name": archive.name,
                "archive_sha256": digest,
                "archive_size": archive.stat().st_size,
                "source_sha": "candidate",
                "base_tag": "v0.64.20",
                "personal_tag": "personal-v0.64.20-r1",
                "upstream_main_sha": upstream_sha,
            }
            (root / CONFIG["manifest_name"]).write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (root / f"{archive.name}.sha256").write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )
            verified = verify_assets(
                root,
                config=CONFIG,
                source_sha="candidate",
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r1",
                upstream_main_sha=upstream_sha,
            )
            self.assertEqual(verified["upstream_main_sha"], upstream_sha)

    def test_rejects_source_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / CONFIG["artifact_name"]
            write_app_archive(archive, source_sha="wrong")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = {
                "repository": "jules-t/cmux",
                "upstream_repository": "manaflow-ai/cmux",
                "bundle_identifier": "com.cmuxterm.app.staging.personal",
                "architecture": "arm64",
                "archive_name": archive.name,
                "archive_sha256": digest,
                "archive_size": archive.stat().st_size,
                "source_sha": "wrong",
                "base_tag": "v0.64.20",
                "personal_tag": "personal-v0.64.20-r1",
            }
            (root / CONFIG["manifest_name"]).write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (root / f"{archive.name}.sha256").write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ControlError):
                verify_assets(
                    root,
                    config=CONFIG,
                    source_sha="abc",
                    base_tag="v0.64.20",
                    personal_tag="personal-v0.64.20-r1",
                )


if __name__ == "__main__":
    unittest.main()
