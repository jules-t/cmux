from __future__ import annotations

import fcntl
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from personal.common import ControlError
from personal.local_updater import (
    installation_status,
    main,
    release_key,
    select_release,
    validate_install_destination,
)


class LocalUpdaterTests(unittest.TestCase):
    def test_selects_highest_personal_version_and_revision(self) -> None:
        releases = [
            {"tag_name": "v0.99.0", "draft": False, "prerelease": False},
            {"tag_name": "personal-v0.64.20-r2", "draft": False, "prerelease": False},
            {"tag_name": "personal-v0.64.21-r1", "draft": False, "prerelease": False},
            {"tag_name": "personal-v0.64.21-r2", "draft": True, "prerelease": False},
        ]
        selected = select_release(releases)
        self.assertEqual(selected["tag_name"], "personal-v0.64.21-r1")

    def test_selects_requested_published_tag(self) -> None:
        releases = [
            {"tag_name": "personal-v0.64.20-r1", "draft": False, "prerelease": False},
            {"tag_name": "personal-v0.64.20-r2", "draft": False, "prerelease": False},
        ]
        selected = select_release(releases, "personal-v0.64.20-r1")
        self.assertEqual(selected["tag_name"], "personal-v0.64.20-r1")

    def test_release_key_is_semantic_not_lexical(self) -> None:
        self.assertGreater(
            release_key("personal-v0.64.20-r10"),
            release_key("personal-v0.64.20-r9"),
        )

    def test_install_destination_cannot_target_the_official_app(self) -> None:
        self.assertEqual(
            validate_install_destination("~/Applications/cmux Personal.app").name,
            "cmux Personal.app",
        )
        with self.assertRaises(ControlError):
            validate_install_destination("/Applications/cmux.app")

    def test_state_file_alone_cannot_make_an_absent_app_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, state_tag, bundle_tag, detail = installation_status(
                destination=pathlib.Path(temporary) / "cmux Personal.app",
                state={
                    "personal_tag": "personal-v0.64.20-r1",
                    "source_sha": "a" * 40,
                    "base_tag": "v0.64.20",
                },
                expected_bundle_identifier="com.cmuxterm.app.staging.personal",
                expected_personal_tag="personal-v0.64.20-r1",
                expected_upstream_repository="manaflow-ai/cmux",
            )
        self.assertFalse(current)
        self.assertEqual(state_tag, "personal-v0.64.20-r1")
        self.assertIsNone(bundle_tag)
        self.assertIn("missing", detail)

    @mock.patch("personal.local_updater.inspect_bundle")
    @mock.patch("personal.local_updater.existing_bundle_tag")
    def test_current_app_requires_live_bundle_verification(
        self,
        existing_bundle_tag: mock.Mock,
        inspect_bundle: mock.Mock,
    ) -> None:
        existing_bundle_tag.return_value = "personal-v0.64.20-r1"
        current, _, _, detail = installation_status(
            destination=pathlib.Path("/tmp/cmux Personal.app"),
            state={
                "personal_tag": "personal-v0.64.20-r1",
                "source_sha": "a" * 40,
                "base_tag": "v0.64.20",
            },
            expected_bundle_identifier="com.cmuxterm.app.staging.personal",
            expected_personal_tag="personal-v0.64.20-r1",
            expected_upstream_repository="manaflow-ai/cmux",
        )
        self.assertTrue(current)
        self.assertEqual(detail, "the installed app is verified")
        inspect_bundle.assert_called_once()

    @mock.patch("personal.local_updater.inspect_bundle")
    @mock.patch("personal.local_updater.existing_bundle_tag")
    def test_invalid_current_app_is_reinstalled(
        self,
        existing_bundle_tag: mock.Mock,
        inspect_bundle: mock.Mock,
    ) -> None:
        existing_bundle_tag.return_value = "personal-v0.64.20-r1"
        inspect_bundle.side_effect = ControlError("invalid signature")
        current, _, _, detail = installation_status(
            destination=pathlib.Path("/tmp/cmux Personal.app"),
            state={
                "personal_tag": "personal-v0.64.20-r1",
                "source_sha": "a" * 40,
                "base_tag": "v0.64.20",
            },
            expected_bundle_identifier="com.cmuxterm.app.staging.personal",
            expected_personal_tag="personal-v0.64.20-r1",
            expected_upstream_repository="manaflow-ai/cmux",
        )
        self.assertFalse(current)
        self.assertIn("invalid signature", detail)

    def test_install_uses_configured_app_path_after_downloading_assets(self) -> None:
        digest = "d" * 64
        tag = "personal-v0.64.20-r1"
        archive_name = "cmux-personal-macos-arm64.zip"
        manifest_name = "cmux-personal-manifest.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "fork_repository": "jules-t/cmux",
                        "upstream_repository": "manaflow-ai/cmux",
                        "app_name": "cmux Personal",
                        "bundle_identifier": "com.cmuxterm.app.staging.personal",
                        "artifact_name": archive_name,
                        "manifest_name": manifest_name,
                        "install_path": "~/Applications/cmux Personal.app",
                        "allowed_attestation_workflows": [
                            "jules-t/cmux/.github/workflows/personal-build.yml"
                        ],
                    }
                ),
                encoding="utf-8",
            )
            release = {
                "tag_name": tag,
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": name,
                        "browser_download_url": f"https://example.invalid/{name}",
                    }
                    for name in (archive_name, manifest_name, f"{archive_name}.sha256")
                ],
            }

            def fake_download(_url: str, destination: pathlib.Path) -> None:
                if destination.name == manifest_name:
                    destination.write_text(
                        json.dumps(
                            {
                                "personal_tag": tag,
                                "repository": "jules-t/cmux",
                                "archive_name": archive_name,
                                "archive_sha256": digest,
                                "source_sha": "a" * 40,
                                "base_tag": "v0.64.20",
                            }
                        ),
                        encoding="utf-8",
                    )
                elif destination.name.endswith(".sha256"):
                    destination.write_text(
                        f"{digest}  {archive_name}\n",
                        encoding="utf-8",
                    )
                else:
                    destination.write_bytes(b"archive")

            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path), "--tag", tag],
                ),
                mock.patch("personal.local_updater.request_json", return_value=[release]),
                mock.patch("personal.local_updater.download", side_effect=fake_download),
                mock.patch("personal.local_updater.sha256", return_value=digest),
                mock.patch("personal.local_updater.verify_attestation"),
                mock.patch("personal.local_updater.validate_zip_paths"),
                mock.patch("personal.local_updater.subprocess.run"),
                mock.patch("personal.local_updater.inspect_bundle"),
                mock.patch("personal.local_updater.install_app") as install_app,
                mock.patch("personal.local_updater.notify"),
            ):
                self.assertEqual(main(), 0)

            self.assertEqual(
                install_app.call_args.kwargs["destination"],
                root / "Applications" / "cmux Personal.app",
            )

    def test_lock_is_released_after_failure_with_retained_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"fork_repository": "jules-t/cmux"}),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path)],
                ),
                mock.patch(
                    "personal.local_updater.request_json",
                    side_effect=ControlError("simulated failure"),
                ),
            ):
                try:
                    main()
                except ControlError as exc:
                    retained_exception = exc
                else:
                    self.fail("expected the simulated updater failure")

            lock_path = root / ".local/share/cmux-personal/updater.lock"
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            self.assertEqual(str(retained_exception), "simulated failure")


if __name__ == "__main__":
    unittest.main()
