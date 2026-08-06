from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from personal.common import ControlError
from personal.local_validation import create_receipt, verify_receipt


def git(repository: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class LocalValidationTests(unittest.TestCase):
    def test_receipt_detects_changes_after_a_successful_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            repository = root / "source"
            assets = root / "assets"
            repository.mkdir()
            assets.mkdir()
            git(root, "init", "--initial-branch=candidate/local", str(repository))
            git(repository, "config", "user.name", "Test User")
            git(repository, "config", "user.email", "test@example.com")
            (repository / "source.txt").write_text("candidate\n", encoding="utf-8")
            git(repository, "add", "source.txt")
            git(repository, "commit", "-m", "candidate")
            source_sha = git(repository, "rev-parse", "HEAD")
            bundle = root / "candidate.bundle"
            git(repository, "bundle", "create", str(bundle), "HEAD")

            archive = assets / "cmux-personal-macos-arm64.zip"
            manifest = assets / "cmux-personal-manifest.json"
            checksum = assets / "cmux-personal-macos-arm64.zip.sha256"
            archive.write_bytes(b"validated archive")
            manifest.write_text("{}\n", encoding="utf-8")
            checksum.write_text("placeholder\n", encoding="utf-8")
            config = root / "config.json"
            config.write_text(
                '{"artifact_name":"cmux-personal-macos-arm64.zip",'
                '"manifest_name":"cmux-personal-manifest.json"}\n',
                encoding="utf-8",
            )
            receipt = root / "validation-receipt.json"
            candidate_record = root / "local-candidate.json"
            candidate_record.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "candidate": {
                            "schema_version": 1,
                            "channel": "main",
                            "source_sha": source_sha,
                            "personal_source_sha": "b" * 40,
                            "target_sha": "a" * 40,
                            "candidate_branch": "candidate/main-aaaaaaaaaaaa",
                            "base_tag": "v1.2.3",
                            "upstream_main_sha": "a" * 40,
                            "runtime_manifest_asset": "cmuxd-remote-manifest-123.json",
                            "integration_status": "resolved_and_reviewed",
                            "prepared_at": "2026-08-06T10:00:00Z",
                        },
                        "run_directory": str(root),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            arguments = argparse.Namespace(
                root=str(root),
                assets=str(assets),
                candidate_bundle=str(bundle),
                candidate=str(candidate_record),
                config=str(config),
                source_sha=source_sha,
                base_tag="v1.2.3",
                personal_tag="personal-v1.2.3-r1",
                output=str(receipt),
            )
            verified_manifest = {
                "builder_os": "test-macOS",
                "build_origin": "local",
                "runtime_manifest_asset": "cmuxd-remote-manifest-123.json",
            }
            with mock.patch(
                "personal.local_validation.verify_assets",
                return_value=verified_manifest,
            ):
                created = create_receipt(arguments)
            self.assertEqual(created["source_sha"], source_sha)

            verify_arguments = argparse.Namespace(
                receipt=str(receipt), config=str(config)
            )
            with mock.patch("personal.local_validation.verify_assets"):
                self.assertEqual(verify_receipt(verify_arguments), created)

            archive.write_bytes(b"changed after validation")
            with mock.patch("personal.local_validation.verify_assets"):
                with self.assertRaisesRegex(
                    ControlError, "validated archive changed"
                ):
                    verify_receipt(verify_arguments)

    def test_stable_candidate_receipt_has_no_upstream_main_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            repository = root / "source"
            assets = root / "assets"
            repository.mkdir()
            assets.mkdir()
            git(root, "init", "--initial-branch=candidate/local", str(repository))
            git(repository, "config", "user.name", "Test User")
            git(repository, "config", "user.email", "test@example.com")
            (repository / "source.txt").write_text("stable\n", encoding="utf-8")
            git(repository, "add", "source.txt")
            git(repository, "commit", "-m", "stable candidate")
            source_sha = git(repository, "rev-parse", "HEAD")
            bundle = root / "candidate.bundle"
            git(repository, "bundle", "create", str(bundle), "HEAD")
            (assets / "cmux-personal-macos-arm64.zip").write_bytes(b"archive")
            (assets / "cmux-personal-manifest.json").write_text(
                "{}\n", encoding="utf-8"
            )
            config = root / "config.json"
            config.write_text(
                '{"artifact_name":"cmux-personal-macos-arm64.zip",'
                '"manifest_name":"cmux-personal-manifest.json"}\n',
                encoding="utf-8",
            )
            candidate_record = root / "candidate.json"
            candidate_record.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "candidate": {
                            "schema_version": 1,
                            "channel": "stable",
                            "source_sha": source_sha,
                            "personal_source_sha": "b" * 40,
                            "target_sha": "c" * 40,
                            "candidate_branch": "candidate/personal-v1.2.3",
                            "base_tag": "v1.2.3",
                            "upstream_main_sha": None,
                            "runtime_manifest_asset": "cmuxd-remote-manifest.json",
                            "integration_status": "clean",
                            "prepared_at": "2026-08-06T10:00:00Z",
                        },
                        "run_directory": str(root),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            arguments = argparse.Namespace(
                root=str(root),
                assets=str(assets),
                candidate_bundle=str(bundle),
                candidate=str(candidate_record),
                config=str(config),
                source_sha=source_sha,
                base_tag="v1.2.3",
                personal_tag="personal-v1.2.3-r1",
                output=str(root / "receipt.json"),
            )
            with mock.patch(
                "personal.local_validation.verify_assets",
                return_value={
                    "builder_os": "test-macOS",
                    "build_origin": "local",
                    "runtime_manifest_asset": "cmuxd-remote-manifest.json",
                },
            ) as verify:
                receipt = create_receipt(arguments)

            self.assertIsNone(receipt["upstream_main_sha"])
            self.assertIsNone(verify.call_args.kwargs["upstream_main_sha"])


if __name__ == "__main__":
    unittest.main()
