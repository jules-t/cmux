from __future__ import annotations

import copy
import pathlib
import unittest

from personal.release_monitor import PublishedRelease, observe, parse_appcast, parse_github_release


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def seeded_state() -> dict:
    return {
        "schema_version": 1,
        "current_stable_tag": "v0.64.20",
        "pending_observation": {
            "tag": None,
            "count": 0,
            "first_seen_at": None,
            "last_seen_at": None,
        },
        "attempt": {"tag": None, "status": None, "workflow_run_id": None, "updated_at": None},
    }


class ReleaseMonitorTests(unittest.TestCase):
    def test_parses_matching_official_sources(self) -> None:
        appcast = parse_appcast((FIXTURES / "appcast-v0.64.21.xml").read_bytes())
        github = parse_github_release((FIXTURES / "github-v0.64.21.json").read_bytes())
        self.assertEqual(appcast.tag, "v0.64.21")
        self.assertEqual(appcast.build, "101")
        self.assertEqual(github.tag, appcast.tag)

    def test_requires_two_consecutive_observations(self) -> None:
        state = seeded_state()
        published = PublishedRelease("v0.64.21", "101", None)
        state, first = observe(
            state,
            appcast=published,
            github=published,
            observations_required=2,
            checked_at="2026-07-24T10:00:00Z",
        )
        self.assertEqual(first["status"], "observing")
        self.assertFalse(first["ready"])

        state, second = observe(
            state,
            appcast=published,
            github=published,
            observations_required=2,
            checked_at="2026-07-24T16:00:00Z",
        )
        self.assertEqual(second["status"], "ready")
        self.assertTrue(second["ready"])
        self.assertEqual(state["attempt"]["tag"], "v0.64.21")

    def test_suppresses_repeated_automatic_attempts(self) -> None:
        state = seeded_state()
        state["pending_observation"] = {
            "tag": "v0.64.21",
            "count": 2,
            "first_seen_at": "2026-07-24T10:00:00Z",
            "last_seen_at": "2026-07-24T16:00:00Z",
        }
        state["attempt"] = {
            "tag": "v0.64.21",
            "status": "blocked",
            "workflow_run_id": "7",
            "updated_at": "2026-07-24T16:01:00Z",
        }
        published = PublishedRelease("v0.64.21", "101", None)
        state, result = observe(
            state,
            appcast=published,
            github=published,
            observations_required=2,
            checked_at="2026-07-24T22:00:00Z",
        )
        self.assertEqual(result["status"], "already_attempted")
        self.assertFalse(result["ready"])
        self.assertEqual(state["pending_observation"]["count"], 2)
        prior = copy.deepcopy(state)
        state, _ = observe(
            state,
            appcast=published,
            github=published,
            observations_required=2,
            checked_at="2026-07-25T04:00:00Z",
        )
        self.assertEqual(state, prior)

    def test_blocks_mismatched_sources(self) -> None:
        state = seeded_state()
        _, result = observe(
            state,
            appcast=PublishedRelease("v0.64.21", "101", None),
            github=PublishedRelease("v0.64.20", None, None),
            observations_required=2,
            checked_at="2026-07-24T10:00:00Z",
        )
        self.assertEqual(result["status"], "source_mismatch")
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
