from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest

from personal.common import ControlError
from personal.verify_release_assets import verify_assets


CONFIG = {
    "artifact_name": "cmux-personal-macos-arm64.zip",
    "manifest_name": "cmux-personal-manifest.json",
    "fork_repository": "jules-t/cmux",
    "bundle_identifier": "com.cmuxterm.app.staging.personal",
    "architecture": "arm64",
}


class VerifyReleaseAssetsTests(unittest.TestCase):
    def test_verifies_cross_asset_identity_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / CONFIG["artifact_name"]
            archive.write_bytes(b"archive")
            digest = hashlib.sha256(b"archive").hexdigest()
            manifest = {
                "repository": "jules-t/cmux",
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

    def test_rejects_source_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / CONFIG["artifact_name"]
            archive.write_bytes(b"archive")
            digest = hashlib.sha256(b"archive").hexdigest()
            manifest = {
                "repository": "jules-t/cmux",
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
