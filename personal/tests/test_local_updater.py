from __future__ import annotations

import unittest

from personal.common import ControlError
from personal.local_updater import release_key, select_release, validate_install_destination


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


if __name__ == "__main__":
    unittest.main()
