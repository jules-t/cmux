from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from personal import release_naming
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

    def test_unpublished_draft_also_occupies_a_revision(self) -> None:
        self.assertEqual(
            next_personal_tag(
                ["personal-v0.64.20-r2"],
                "v0.64.20",
                existing_release_tags=["personal-v0.64.20-r3"],
            ),
            ("personal-v0.64.20-r4", 4),
        )

    def test_local_build_attempt_also_occupies_a_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builds_root = pathlib.Path(temporary)
            metadata = builds_root / "failed-build" / "metadata"
            metadata.mkdir(parents=True)
            (metadata / "release-name.json").write_text(
                json.dumps(
                    {
                        "personal_tag": "personal-v0.64.20-r3",
                        "revision": 3,
                        "base_tag": "v0.64.20",
                    }
                ),
                encoding="utf-8",
            )

            local_tags = release_naming.local_personal_tags(builds_root)

        self.assertEqual(
            next_personal_tag(
                ["personal-v0.64.20-r2"],
                "v0.64.20",
                existing_local_tags=local_tags,
            ),
            ("personal-v0.64.20-r4", 4),
        )


if __name__ == "__main__":
    unittest.main()
