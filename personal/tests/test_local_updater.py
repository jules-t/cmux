from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from personal.common import ControlError
from personal.local_updater import (
    installation_status,
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


if __name__ == "__main__":
    unittest.main()
