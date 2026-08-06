from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from personal.local_installer import install_validated_receipt
from personal.local_updater import AppRunningError


class LocalInstallerTests(unittest.TestCase):
    def receipt(self) -> dict[str, object]:
        return {
            "source_sha": "a" * 40,
            "base_tag": "v0.64.23",
            "personal_tag": "personal-v0.64.23-r1",
            "upstream_main_sha": "b" * 40,
            "assets_directory": "assets",
        }

    def config(self) -> dict[str, object]:
        return {
            "artifact_name": "cmux-personal-macos-arm64.zip",
            "app_name": "cmux Personal",
            "bundle_identifier": "com.cmuxterm.app.staging.personal",
            "install_path": "~/Applications/cmux Personal.app",
            "upstream_repository": "manaflow-ai/cmux",
        }

    def test_verifies_stages_and_transactionally_installs_the_exact_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            receipt_path = root / "validation-receipt.json"
            config_path = root / "config.json"
            data_root = root / "data"
            destination = root / "Applications" / "cmux Personal.app"
            events: list[str] = []

            with (
                mock.patch(
                    "personal.local_installer.verify_receipt",
                    return_value=self.receipt(),
                ),
                mock.patch(
                    "personal.local_installer.load_json", return_value=self.config()
                ),
                mock.patch(
                    "personal.local_installer.verify_assets",
                    side_effect=lambda *args, **kwargs: events.append("verify")
                    or {
                        "source_sha": "a" * 40,
                        "base_tag": "v0.64.23",
                        "upstream_main_sha": "b" * 40,
                    },
                ),
                mock.patch("personal.local_installer.validate_zip_paths"),
                mock.patch(
                    "personal.local_installer.validate_install_destination",
                    return_value=destination,
                ),
                mock.patch("personal.local_installer.subprocess.run"),
                mock.patch(
                    "personal.local_installer.inspect_bundle",
                    side_effect=lambda *args, **kwargs: events.append("inspect"),
                ),
                mock.patch(
                    "personal.local_installer.stage_app",
                    side_effect=lambda *args, **kwargs: events.append("stage"),
                ),
                mock.patch(
                    "personal.local_installer.install_staged_app",
                    side_effect=lambda *args, **kwargs: events.append("install"),
                ),
                mock.patch(
                    "personal.local_installer.record_installation",
                    side_effect=lambda *args, **kwargs: events.append("record"),
                ),
            ):
                result = install_validated_receipt(
                    receipt_path,
                    config_path=config_path,
                    data_root=data_root,
                )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(
                events, ["verify", "inspect", "stage", "install", "record"]
            )

    def test_leaves_a_verified_build_staged_when_the_app_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            destination = root / "Applications" / "cmux Personal.app"
            with (
                mock.patch(
                    "personal.local_installer.verify_receipt",
                    return_value=self.receipt(),
                ),
                mock.patch(
                    "personal.local_installer.load_json", return_value=self.config()
                ),
                mock.patch(
                    "personal.local_installer.verify_assets", return_value=self.receipt()
                ),
                mock.patch("personal.local_installer.validate_zip_paths"),
                mock.patch(
                    "personal.local_installer.validate_install_destination",
                    return_value=destination,
                ),
                mock.patch("personal.local_installer.subprocess.run"),
                mock.patch("personal.local_installer.inspect_bundle"),
                mock.patch("personal.local_installer.stage_app"),
                mock.patch(
                    "personal.local_installer.install_staged_app",
                    side_effect=AppRunningError("running"),
                ),
                mock.patch(
                    "personal.local_installer.record_installation"
                ) as record_installation,
            ):
                result = install_validated_receipt(
                    root / "validation-receipt.json",
                    config_path=root / "config.json",
                    data_root=root / "data",
                )

            self.assertEqual(result["status"], "staged")
            record_installation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
