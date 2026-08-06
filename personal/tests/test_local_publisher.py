from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from personal.common import ControlError
from personal.local_publisher import publication_run_id, publish_validated_receipt


class LocalPublisherTests(unittest.TestCase):
    def candidate(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "channel": "main",
            "source_sha": "a" * 40,
            "personal_source_sha": "c" * 40,
            "target_sha": "b" * 40,
            "candidate_branch": "candidate/main-bbbbbbbbbbbb",
            "base_tag": "v0.64.23",
            "upstream_main_sha": "b" * 40,
            "runtime_manifest_asset": "cmuxd-remote-manifest-123.json",
            "integration_status": "resolved_and_reviewed",
            "prepared_at": "2026-08-06T10:00:00Z",
        }

    def receipt(self) -> dict[str, object]:
        return {
            "candidate": self.candidate(),
            "source_sha": "a" * 40,
            "base_tag": "v0.64.23",
            "personal_tag": "personal-v0.64.23-r1",
            "upstream_main_sha": "b" * 40,
            "assets_directory": "assets",
            "candidate_bundle": "candidate.bundle",
            "validated_at": "2026-08-06T12:00:00Z",
            "build_origin": "local",
        }

    def test_publishes_locally_then_promotes_and_finalizes_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            events: list[str] = []

            def mutate(*args: object, **kwargs: object) -> dict[str, object]:
                events.append("state")
                return {}

            with (
                mock.patch(
                    "personal.local_publisher.verify_receipt",
                    return_value=self.receipt(),
                ),
                mock.patch(
                    "personal.local_publisher.load_json",
                    return_value={"fork_repository": "owner/repository"},
                ),
                mock.patch(
                    "personal.local_publisher.require_installed_receipt",
                    side_effect=lambda *args, **kwargs: events.append("installed"),
                ),
                mock.patch(
                    "personal.local_publisher.ensure_gh_token",
                    side_effect=lambda: events.append("auth"),
                ),
                mock.patch(
                    "personal.local_publisher.clone_validated_candidate",
                    return_value=root / "candidate",
                ),
                mock.patch(
                    "personal.local_publisher.remote_branch_sha",
                    return_value="c" * 40,
                ),
                mock.patch(
                    "personal.local_publisher.publish_candidate",
                    side_effect=lambda *args, **kwargs: events.append("candidate"),
                ),
                mock.patch(
                    "personal.local_publisher.mutate_remote_state",
                    side_effect=mutate,
                ),
                mock.patch(
                    "personal.local_publisher.ensure_release_reservation",
                    side_effect=lambda *args, **kwargs: events.append("reserve"),
                ),
                mock.patch(
                    "personal.local_publisher.publish_release",
                    side_effect=lambda *args, **kwargs: events.append("publish"),
                ),
                mock.patch(
                    "personal.local_publisher.promote_personal_stable",
                    side_effect=lambda *args, **kwargs: events.append("promote"),
                ),
                mock.patch("personal.local_publisher.run") as command,
                mock.patch("personal.local_publisher.write_json"),
            ):
                command.return_value.stdout = "git@example.invalid:owner/repository.git\n"
                result = publish_validated_receipt(
                    root / "validation-receipt.json",
                    config_path=root / "config.json",
                    repository=root,
                    data_root=root / "data",
                )

            self.assertEqual(result["status"], "published")
            self.assertEqual(
                events,
                [
                    "installed",
                    "auth",
                    "candidate",
                    "state",
                    "reserve",
                    "publish",
                    "promote",
                    "state",
                ],
            )

    def test_uninstalled_receipt_cannot_mutate_github(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            with (
                mock.patch(
                    "personal.local_publisher.verify_receipt",
                    return_value=self.receipt(),
                ),
                mock.patch(
                    "personal.local_publisher.load_json",
                    return_value={"fork_repository": "owner/repository"},
                ),
                mock.patch(
                    "personal.local_publisher.require_installed_receipt",
                    side_effect=ControlError("not installed"),
                ),
                mock.patch("personal.local_publisher.publish_candidate") as publish,
                mock.patch("personal.local_publisher.mutate_remote_state") as mutate,
            ):
                with self.assertRaisesRegex(ControlError, "not installed"):
                    publish_validated_receipt(
                        root / "validation-receipt.json",
                        config_path=root / "config.json",
                        repository=root,
                        data_root=root / "data",
                    )

            publish.assert_not_called()
            mutate.assert_not_called()

    def test_publication_claim_id_is_stable_and_numeric(self) -> None:
        first = publication_run_id("a" * 40)
        self.assertEqual(first, publication_run_id("a" * 40))
        self.assertRegex(first, r"^[1-9][0-9]*$")


if __name__ == "__main__":
    unittest.main()
