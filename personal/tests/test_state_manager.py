from __future__ import annotations

import json
import pathlib
import unittest

from personal.common import ControlError
from personal.state_manager import (
    claim_publication,
    finalize_publication,
    validate_promotion,
)


SOURCE_A = "a" * 40
SOURCE_B = "b" * 40
SOURCE_C = "c" * 40
NOW = "2026-07-26T12:00:00Z"


def initial_state(current_stable_tag: str = "v0.64.20") -> dict[str, object]:
    return {
        "current_stable_tag": current_stable_tag,
        "last_promoted_personal_tag": None,
        "pending_observation": {
            "tag": None,
            "count": 0,
            "first_seen_at": None,
            "last_seen_at": None,
        },
    }


def published_state(
    personal_tag: str = "personal-v0.64.20-r2",
    source_sha: str = SOURCE_A,
) -> dict[str, object]:
    return {
        "current_stable_tag": "v0.64.20",
        "last_published_base_tag": "v0.64.20",
        "last_promoted_personal_tag": personal_tag,
        "last_promoted_source_sha": source_sha,
        "personal_stable_sha": source_sha,
        "publication_claim": None,
    }


def claim(
    state: dict[str, object],
    *,
    base_tag: str = "v0.64.20",
    personal_tag: str = "personal-v0.64.20-r1",
    source_sha: str = SOURCE_A,
    run_id: str = "123",
) -> None:
    claim_publication(
        state,
        base_tag=base_tag,
        personal_tag=personal_tag,
        source_sha=source_sha,
        run_id=run_id,
        updated_at=NOW,
    )


class StateManagerTests(unittest.TestCase):
    def test_checked_in_state_has_a_source_promotion_lease_baseline(self) -> None:
        path = pathlib.Path(__file__).resolve().parents[1] / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertRegex(state["personal_stable_sha"], r"^[0-9a-f]{40}$")

    def test_allows_the_first_personal_publication(self) -> None:
        validate_promotion(
            initial_state(),
            base_tag="v0.64.20",
            personal_tag="personal-v0.64.20-r1",
            source_sha=SOURCE_A,
        )

    def test_first_publication_cannot_roll_back_personal_stable_base(self) -> None:
        with self.assertRaisesRegex(ControlError, "older than personal/stable base"):
            validate_promotion(
                initial_state("v0.64.21"),
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r1",
                source_sha=SOURCE_A,
            )

    def test_allows_a_newer_revision(self) -> None:
        validate_promotion(
            published_state(),
            base_tag="v0.64.20",
            personal_tag="personal-v0.64.20-r3",
            source_sha=SOURCE_B,
        )

    def test_rejects_an_older_recovery(self) -> None:
        with self.assertRaisesRegex(ControlError, "stale publication"):
            validate_promotion(
                published_state(),
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r1",
                source_sha=SOURCE_B,
            )

    def test_allows_an_exact_idempotent_recovery(self) -> None:
        validate_promotion(
            published_state(),
            base_tag="v0.64.20",
            personal_tag="personal-v0.64.20-r2",
            source_sha=SOURCE_A,
        )

    def test_rejects_same_tag_with_different_source(self) -> None:
        with self.assertRaisesRegex(ControlError, "conflicts"):
            validate_promotion(
                published_state(),
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r2",
                source_sha=SOURCE_B,
            )

    def test_newer_official_base_wins_over_revision_number(self) -> None:
        validate_promotion(
            published_state("personal-v0.64.20-r99"),
            base_tag="v0.64.21",
            personal_tag="personal-v0.64.21-r1",
            source_sha=SOURCE_B,
        )

    def test_durable_claim_blocks_an_older_recovery(self) -> None:
        state = initial_state()
        claim(
            state,
            personal_tag="personal-v0.64.20-r2",
            source_sha=SOURCE_B,
        )
        with self.assertRaisesRegex(ControlError, "publication claim"):
            validate_promotion(
                state,
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r1",
                source_sha=SOURCE_A,
            )

    def test_exact_claim_is_idempotent(self) -> None:
        state = initial_state()
        claim(state)
        original = dict(state["publication_claim"])  # type: ignore[arg-type]
        claim(state)
        self.assertEqual(state["publication_claim"], original)

    def test_exact_claim_cannot_be_reassigned_to_another_build_run(self) -> None:
        state = initial_state()
        claim(state)
        with self.assertRaisesRegex(ControlError, "belongs to workflow run 123"):
            claim(state, run_id="456")

    def test_newer_claim_supersedes_an_unfinished_older_claim(self) -> None:
        state = initial_state()
        claim(state)
        claim(
            state,
            personal_tag="personal-v0.64.20-r2",
            source_sha=SOURCE_B,
            run_id="456",
        )
        self.assertEqual(
            state["publication_claim"],
            {
                "base_tag": "v0.64.20",
                "personal_tag": "personal-v0.64.20-r2",
                "source_sha": SOURCE_B,
                "workflow_run_id": "456",
                "claimed_at": NOW,
            },
        )

    def test_finalize_requires_a_durable_claim_or_exact_published_state(self) -> None:
        with self.assertRaisesRegex(ControlError, "no exact durable claim"):
            finalize_publication(
                initial_state(),
                base_tag="v0.64.20",
                personal_tag="personal-v0.64.20-r1",
                source_sha=SOURCE_A,
                personal_stable_sha=SOURCE_A,
                run_id="123",
                updated_at=NOW,
            )

    def test_finalize_records_an_aligned_source_as_promoted(self) -> None:
        state = initial_state()
        claim(state)
        finalize_publication(
            state,
            base_tag="v0.64.20",
            personal_tag="personal-v0.64.20-r1",
            source_sha=SOURCE_A,
            personal_stable_sha=SOURCE_A,
            run_id="123",
            updated_at=NOW,
        )
        self.assertEqual(state["last_published_base_tag"], "v0.64.20")
        self.assertEqual(state["last_promoted_personal_tag"], "personal-v0.64.20-r1")
        self.assertEqual(state["last_promoted_source_sha"], SOURCE_A)
        self.assertEqual(state["personal_stable_sha"], SOURCE_A)
        self.assertIsNone(state["publication_claim"])
        self.assertEqual(state["attempt"]["status"], "promoted")  # type: ignore[index]

    def test_non_promoting_publish_tracks_branch_separately(self) -> None:
        state = initial_state()
        claim(
            state,
            base_tag="v0.64.21",
            personal_tag="personal-v0.64.21-r1",
            source_sha=SOURCE_B,
        )
        finalize_publication(
            state,
            base_tag="v0.64.21",
            personal_tag="personal-v0.64.21-r1",
            source_sha=SOURCE_B,
            personal_stable_sha=SOURCE_A,
            run_id="123",
            updated_at=NOW,
        )
        self.assertEqual(state["current_stable_tag"], "v0.64.20")
        self.assertEqual(state["last_published_base_tag"], "v0.64.21")
        self.assertEqual(state["last_promoted_source_sha"], SOURCE_B)
        self.assertEqual(state["personal_stable_sha"], SOURCE_A)
        self.assertEqual(
            state["attempt"]["status"],  # type: ignore[index]
            "published_without_source_promotion",
        )

    def test_exact_published_recovery_can_finalize_without_a_claim(self) -> None:
        state = published_state()
        finalize_publication(
            state,
            base_tag="v0.64.20",
            personal_tag="personal-v0.64.20-r2",
            source_sha=SOURCE_A,
            personal_stable_sha=SOURCE_C,
            run_id="123",
            updated_at=NOW,
        )
        self.assertEqual(state["personal_stable_sha"], SOURCE_C)
        self.assertEqual(
            state["attempt"]["status"],  # type: ignore[index]
            "published_without_source_promotion",
        )


if __name__ == "__main__":
    unittest.main()
