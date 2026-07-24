from __future__ import annotations

import unittest

from personal.release_naming import next_personal_tag


class ReleaseNamingTests(unittest.TestCase):
    def test_allocates_first_revision(self) -> None:
        self.assertEqual(next_personal_tag([], "v0.64.20"), ("personal-v0.64.20-r1", 1))

    def test_allocates_after_highest_matching_revision(self) -> None:
        tags = [
            "personal-v0.64.20-r1",
            "personal-v0.64.19-r8",
            "personal-v0.64.20-r3",
        ]
        self.assertEqual(
            next_personal_tag(tags, "v0.64.20"),
            ("personal-v0.64.20-r4", 4),
        )


if __name__ == "__main__":
    unittest.main()
