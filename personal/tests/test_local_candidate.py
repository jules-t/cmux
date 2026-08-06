from __future__ import annotations

import pathlib
import tempfile
import unittest

from personal.common import ControlError, write_json
from personal.local_candidate import make_record, select_candidate, validate_candidate


SOURCE_A = "a" * 40
SOURCE_B = "b" * 40
TARGET_A = "c" * 40
TARGET_B = "d" * 40


class LocalCandidateTests(unittest.TestCase):
    def write_record(
        self,
        data_root: pathlib.Path,
        *,
        channel: str,
        base_tag: str,
        source_sha: str,
        target_sha: str,
        personal_source_sha: str = SOURCE_A,
        prepared_at: str = "2026-08-06T10:00:00Z",
    ) -> dict:
        run_directory = data_root / "candidates" / "runs" / f"{channel}-{source_sha[:8]}"
        (run_directory / "source" / ".git").mkdir(parents=True)
        (run_directory / "candidate.bundle").write_bytes(b"bundle")
        is_main = channel == "main"
        record = make_record(
            channel=channel,
            source_sha=source_sha,
            personal_source_sha=personal_source_sha,
            target_sha=target_sha,
            candidate_branch=(
                f"candidate/main-{target_sha[:12]}"
                if is_main
                else f"candidate/personal-{base_tag.removeprefix('v')}"
            ),
            base_tag=base_tag,
            upstream_main_sha=target_sha if is_main else None,
            runtime_manifest_asset=(
                "cmuxd-remote-manifest-123.json"
                if is_main
                else "cmuxd-remote-manifest.json"
            ),
            integration_status="resolved_and_reviewed" if is_main else "clean",
            run_directory=run_directory,
            prepared_at=prepared_at,
        )
        write_json(data_root / "candidates" / f"{channel}.json", record)
        return record

    def test_main_identity_requires_the_exact_nightly_target(self) -> None:
        with self.assertRaisesRegex(ControlError, "target differs"):
            validate_candidate(
                {
                    "schema_version": 1,
                    "channel": "main",
                    "source_sha": SOURCE_B,
                    "personal_source_sha": SOURCE_A,
                    "target_sha": TARGET_A,
                    "candidate_branch": "candidate/main-cccccccccccc",
                    "base_tag": "v1.2.3",
                    "upstream_main_sha": TARGET_B,
                    "runtime_manifest_asset": "cmuxd-remote-manifest-123.json",
                    "integration_status": "clean",
                    "prepared_at": "2026-08-06T10:00:00Z",
                }
            )

    def test_auto_selection_prefers_main_at_the_same_official_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = pathlib.Path(temporary)
            self.write_record(
                data_root,
                channel="stable",
                base_tag="v1.2.3",
                source_sha=SOURCE_A,
                target_sha=TARGET_A,
            )
            main = self.write_record(
                data_root,
                channel="main",
                base_tag="v1.2.3",
                source_sha=SOURCE_B,
                target_sha=TARGET_B,
            )
            selected = select_candidate(
                data_root, personal_source_sha=SOURCE_A
            )
            self.assertEqual(selected, main)

    def test_auto_selection_prefers_a_newer_stable_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = pathlib.Path(temporary)
            stable = self.write_record(
                data_root,
                channel="stable",
                base_tag="v1.2.4",
                source_sha=SOURCE_A,
                target_sha=TARGET_A,
            )
            self.write_record(
                data_root,
                channel="main",
                base_tag="v1.2.3",
                source_sha=SOURCE_B,
                target_sha=TARGET_B,
            )
            selected = select_candidate(
                data_root, personal_source_sha=SOURCE_A
            )
            self.assertEqual(selected, stable)

    def test_candidate_from_an_older_personal_source_is_not_selectable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = pathlib.Path(temporary)
            self.write_record(
                data_root,
                channel="stable",
                base_tag="v1.2.3",
                source_sha=SOURCE_A,
                target_sha=TARGET_A,
                personal_source_sha=SOURCE_B,
            )
            with self.assertRaisesRegex(ControlError, "no reviewed stable candidate"):
                select_candidate(
                    data_root,
                    personal_source_sha=SOURCE_A,
                    channel="stable",
                )

    def test_candidate_older_than_the_published_base_is_not_selectable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = pathlib.Path(temporary)
            self.write_record(
                data_root,
                channel="stable",
                base_tag="v1.2.3",
                source_sha=SOURCE_A,
                target_sha=TARGET_A,
            )
            with self.assertRaisesRegex(ControlError, "no reviewed stable candidate"):
                select_candidate(
                    data_root,
                    personal_source_sha=SOURCE_A,
                    channel="stable",
                    minimum_base_tag="v1.2.4",
                )


if __name__ == "__main__":
    unittest.main()
