from __future__ import annotations

import calendar
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from personal import local_updater, release_publisher
from personal.common import ControlError
from personal.tests.test_official_runtime_manifest import nightly_manifest_fixture
from personal.tests.test_verify_release_assets import (
    CONFIG as RELEASE_ASSET_CONFIG,
    write_app_archive,
)


REPOSITORY = "owner/repository"
BASE_TAG = "v0.64.20"
PERSONAL_TAG = "personal-v0.64.20-r1"
SOURCE_SHA = "a" * 40
OTHER_SHA = "b" * 40


def completed(value: object | None = None) -> subprocess.CompletedProcess[str]:
    stdout = "" if value is None else json.dumps(value)
    return subprocess.CompletedProcess([], 0, stdout, "")


class GitHubStub:
    def __init__(
        self,
        *,
        release: dict[str, object] | None = None,
        source_sha: str = SOURCE_SHA,
        annotated: bool = False,
        visibility_delay: int = 0,
        tag_exists: bool | None = None,
    ) -> None:
        self.release = release
        self.source_sha = source_sha
        self.annotated = annotated
        self.visibility_delay = visibility_delay
        self.tag_exists = tag_exists
        self.assets: list[dict[str, object]] = []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        if arguments[:3] == ("api", "--paginate", "--slurp"):
            path = arguments[3]
            if path == f"repos/{REPOSITORY}/releases?per_page=100":
                if self.release is not None and self.visibility_delay > 0:
                    self.visibility_delay -= 1
                    releases = []
                else:
                    releases = [] if self.release is None else [self.release]
                return completed([releases])
            if path == f"repos/{REPOSITORY}/releases/1/assets?per_page=100":
                return completed([self.assets])
        if arguments[:2] == (
            "api",
            f"repos/{REPOSITORY}/git/matching-refs/tags/{PERSONAL_TAG}",
        ):
            tag_exists = self.tag_exists
            if tag_exists is None:
                tag_exists = self.release is not None and not bool(
                    self.release.get("draft")
                )
            if not tag_exists:
                return completed([])
            object_type = "tag" if self.annotated else "commit"
            object_sha = "c" * 40 if self.annotated else self.source_sha
            return completed(
                [
                    {
                        "ref": f"refs/tags/{PERSONAL_TAG}",
                        "object": {"type": object_type, "sha": object_sha},
                    }
                ]
            )
        if arguments[:2] == ("api", f"repos/{REPOSITORY}/git/tags/{'c' * 40}"):
            return completed({"object": {"type": "commit", "sha": self.source_sha}})
        if arguments[:3] == ("release", "create", PERSONAL_TAG):
            if self.release is not None:
                raise ControlError("release already exists")
            target = arguments[arguments.index("--target") + 1]
            self.source_sha = target
            self.release = {
                "id": 1,
                "tag_name": PERSONAL_TAG,
                "draft": True,
                "target_commitish": target,
            }
            self.assets = []
            return completed()
        if arguments[:3] == ("release", "edit", PERSONAL_TAG):
            if "--draft=false" in arguments:
                assert self.release is not None
                self.release["draft"] = False
            return completed()
        if arguments[:3] == ("release", "upload", PERSONAL_TAG):
            repo_index = arguments.index("--repo")
            paths = [pathlib.Path(value) for value in arguments[3:repo_index]]
            self.assets = [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
                }
                for path in paths
            ]
            return completed()
        raise AssertionError(f"unexpected gh call: {arguments!r}")


def draft_release() -> dict[str, object]:
    return {
        "id": 1,
        "tag_name": PERSONAL_TAG,
        "draft": True,
        "target_commitish": SOURCE_SHA,
    }


class ReleasePublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.token = mock.patch.dict(os.environ, {"GH_TOKEN": "test-token"}, clear=False)
        self.token.start()

    def tearDown(self) -> None:
        self.token.stop()

    def test_reserve_creates_an_exact_empty_draft(self) -> None:
        github = GitHubStub()

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            release = release_publisher.reserve_release(
                repository=REPOSITORY,
                personal_tag=PERSONAL_TAG,
                source_sha=SOURCE_SHA,
                base_tag=BASE_TAG,
                run_url="https://github.example/runs/1",
            )

        self.assertTrue(release["draft"])
        create = next(call for call in github.calls if call[:2] == ("release", "create"))
        self.assertIn("--draft", create)
        self.assertEqual(create[create.index("--target") + 1], SOURCE_SHA)
        self.assertFalse(any(call[:2] == ("release", "upload") for call in github.calls))

    def test_reserve_reuses_an_exact_draft_and_exercises_edit(self) -> None:
        github = GitHubStub(release=draft_release())

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            release_publisher.reserve_release(
                repository=REPOSITORY,
                personal_tag=PERSONAL_TAG,
                source_sha=SOURCE_SHA,
                base_tag=BASE_TAG,
            )

        edit = next(call for call in github.calls if call[:2] == ("release", "edit"))
        self.assertIn("--title", edit)
        self.assertIn("--notes", edit)
        self.assertIn("--draft", edit)
        self.assertFalse(
            any("/git/ref/tags/" in " ".join(call) for call in github.calls)
        )

    def test_reserve_rejects_a_draft_whose_target_points_elsewhere(self) -> None:
        release = draft_release()
        release["target_commitish"] = OTHER_SHA
        github = GitHubStub(release=release)

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            with self.assertRaisesRegex(ControlError, "targets"):
                release_publisher.reserve_release(
                    repository=REPOSITORY,
                    personal_tag=PERSONAL_TAG,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                )

        self.assertFalse(any(call[:2] == ("release", "edit") for call in github.calls))

    def test_reserve_rejects_a_draft_with_a_conflicting_existing_tag(self) -> None:
        github = GitHubStub(
            release=draft_release(),
            source_sha=OTHER_SHA,
            tag_exists=True,
        )

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            with self.assertRaisesRegex(ControlError, "points to"):
                release_publisher.reserve_release(
                    repository=REPOSITORY,
                    personal_tag=PERSONAL_TAG,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                )

        self.assertFalse(any(call[:2] == ("release", "edit") for call in github.calls))

    def test_reserve_retries_new_draft_visibility(self) -> None:
        github = GitHubStub(visibility_delay=2)

        with (
            mock.patch.object(release_publisher, "gh", side_effect=github),
            mock.patch.object(release_publisher.time, "sleep") as sleep,
        ):
            release = release_publisher.reserve_release(
                repository=REPOSITORY,
                personal_tag=PERSONAL_TAG,
                source_sha=SOURCE_SHA,
                base_tag=BASE_TAG,
            )

        self.assertTrue(release["draft"])
        self.assertEqual(sleep.call_count, 1)

    def test_reserve_recovers_when_an_existing_draft_is_initially_hidden(self) -> None:
        github = GitHubStub(
            release=draft_release(),
            visibility_delay=2,
        )

        with (
            mock.patch.object(release_publisher, "gh", side_effect=github),
            mock.patch.object(release_publisher.time, "sleep") as sleep,
        ):
            release = release_publisher.reserve_release(
                repository=REPOSITORY,
                personal_tag=PERSONAL_TAG,
                source_sha=SOURCE_SHA,
                base_tag=BASE_TAG,
            )

        self.assertTrue(release["draft"])
        self.assertEqual(sleep.call_count, 0)
        self.assertTrue(
            any(call[:2] == ("release", "create") for call in github.calls)
        )
        self.assertTrue(
            any(call[:2] == ("release", "edit") for call in github.calls)
        )

    def test_reserve_rejects_an_existing_draft_with_assets(self) -> None:
        github = GitHubStub(release=draft_release())
        github.assets = [{"name": "stale.zip", "size": 1, "digest": "sha256:bad"}]

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            with self.assertRaisesRegex(ControlError, "contains assets"):
                release_publisher.reserve_release(
                    repository=REPOSITORY,
                    personal_tag=PERSONAL_TAG,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                )

        self.assertFalse(any(call[:2] == ("release", "edit") for call in github.calls))

    def test_ensure_reservation_creates_a_missing_exact_draft(self) -> None:
        github = GitHubStub()

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            release = release_publisher.ensure_release_reservation(
                repository=REPOSITORY,
                personal_tag=PERSONAL_TAG,
                source_sha=SOURCE_SHA,
                base_tag=BASE_TAG,
                run_url="https://github.example/runs/1",
            )

        self.assertTrue(release["draft"])
        create = next(call for call in github.calls if call[:2] == ("release", "create"))
        self.assertEqual(create[create.index("--target") + 1], SOURCE_SHA)

    def test_ensure_reservation_preserves_an_exact_partial_draft(self) -> None:
        github = GitHubStub(release=draft_release())
        github.assets = [{"name": "partial.zip", "size": 1, "digest": "sha256:bad"}]

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            release = release_publisher.ensure_release_reservation(
                repository=REPOSITORY,
                personal_tag=PERSONAL_TAG,
                source_sha=SOURCE_SHA,
                base_tag=BASE_TAG,
            )

        self.assertTrue(release["draft"])
        self.assertFalse(
            any(call[:2] in {("release", "create"), ("release", "edit")} for call in github.calls)
        )

    def make_assets(
        self,
        directory: pathlib.Path,
    ) -> tuple[dict[str, str], dict[str, tuple[int, str]]]:
        config = {"artifact_name": "app.zip", "manifest_name": "manifest.json"}
        contents = {
            "app.zip": b"archive",
            "manifest.json": b'{"verified": true}\n',
            "app.zip.sha256": b"checksum\n",
        }
        expected: dict[str, tuple[int, str]] = {}
        for name, payload in contents.items():
            path = directory / name
            path.write_bytes(payload)
            expected[name] = (len(payload), hashlib.sha256(payload).hexdigest())
        return config, expected

    def make_main_assets(
        self,
        directory: pathlib.Path,
        *,
        upstream_main_sha: str,
    ) -> dict[str, str]:
        config = dict(RELEASE_ASSET_CONFIG)
        archive = directory / config["artifact_name"]
        write_app_archive(
            archive,
            source_sha=SOURCE_SHA,
            upstream_main_sha=upstream_main_sha,
            runtime_manifest=nightly_manifest_fixture(),
        )
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = {
            "repository": config["fork_repository"],
            "upstream_repository": config["upstream_repository"],
            "bundle_identifier": config["bundle_identifier"],
            "architecture": config["architecture"],
            "archive_name": archive.name,
            "archive_sha256": digest,
            "archive_size": archive.stat().st_size,
            "source_sha": SOURCE_SHA,
            "base_tag": BASE_TAG,
            "personal_tag": PERSONAL_TAG,
            "upstream_main_sha": upstream_main_sha,
        }
        (directory / config["manifest_name"]).write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        (directory / f"{archive.name}.sha256").write_text(
            f"{digest}  {archive.name}\n",
            encoding="utf-8",
        )
        return config

    def test_publish_accepts_main_based_assets_with_exact_upstream_identity(self) -> None:
        github = GitHubStub(release=draft_release())
        upstream_main_sha = "d" * 40
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config = self.make_main_assets(
                directory,
                upstream_main_sha=upstream_main_sha,
            )
            with mock.patch.object(release_publisher, "gh", side_effect=github):
                result = release_publisher.publish_release(
                    repository=REPOSITORY,
                    directory=directory,
                    config=config,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                    personal_tag=PERSONAL_TAG,
                    upstream_main_sha=upstream_main_sha,
                )

        self.assertFalse(result["draft"])

    def test_publish_uploads_a_draft_then_verifies_and_publishes_it(self) -> None:
        github = GitHubStub(release=draft_release())
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config, _ = self.make_assets(directory)
            with (
                mock.patch.object(release_publisher, "gh", side_effect=github),
                mock.patch.object(release_publisher, "verify_assets") as verify,
            ):
                result = release_publisher.publish_release(
                    repository=REPOSITORY,
                    directory=directory,
                    config=config,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                    personal_tag=PERSONAL_TAG,
                )

        verify.assert_called_once()
        self.assertFalse(result["draft"])
        upload = next(call for call in github.calls if call[:2] == ("release", "upload"))
        self.assertIn("--clobber", upload)
        published = next(
            call
            for call in github.calls
            if call[:2] == ("release", "edit") and "--draft=false" in call
        )
        self.assertLess(github.calls.index(upload), github.calls.index(published))

    def test_publish_is_idempotent_only_when_published_assets_match(self) -> None:
        release = {"id": 1, "tag_name": PERSONAL_TAG, "draft": False}
        github = GitHubStub(release=release, annotated=True)
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config, expected = self.make_assets(directory)
            github.assets = [
                {"name": name, "size": size, "digest": f"sha256:{digest}"}
                for name, (size, digest) in expected.items()
            ]
            with (
                mock.patch.object(release_publisher, "gh", side_effect=github),
                mock.patch.object(release_publisher, "verify_assets"),
            ):
                result = release_publisher.publish_release(
                    repository=REPOSITORY,
                    directory=directory,
                    config=config,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                    personal_tag=PERSONAL_TAG,
                )

        self.assertFalse(result["draft"])
        self.assertFalse(
            any(call[:2] in {("release", "upload"), ("release", "edit")} for call in github.calls)
        )
        self.assertTrue(
            any(
                call[:2] == ("api", f"repos/{REPOSITORY}/git/tags/{'c' * 40}")
                for call in github.calls
            )
        )

    def test_publish_rejects_a_remote_asset_mismatch(self) -> None:
        release = {"id": 1, "tag_name": PERSONAL_TAG, "draft": False}
        github = GitHubStub(release=release)
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config, expected = self.make_assets(directory)
            github.assets = [
                {
                    "name": name,
                    "size": size + (1 if name == "app.zip" else 0),
                    "digest": f"sha256:{digest}",
                }
                for name, (size, digest) in expected.items()
            ]
            with (
                mock.patch.object(release_publisher, "gh", side_effect=github),
                mock.patch.object(release_publisher, "verify_assets"),
            ):
                with self.assertRaisesRegex(ControlError, "has size"):
                    release_publisher.publish_release(
                        repository=REPOSITORY,
                        directory=directory,
                        config=config,
                        source_sha=SOURCE_SHA,
                        base_tag=BASE_TAG,
                        personal_tag=PERSONAL_TAG,
                    )

        self.assertFalse(
            any(call[:2] in {("release", "upload"), ("release", "edit")} for call in github.calls)
        )

    def test_publish_rejects_a_missing_remote_digest(self) -> None:
        release = {"id": 1, "tag_name": PERSONAL_TAG, "draft": False}
        github = GitHubStub(release=release)
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config, expected = self.make_assets(directory)
            github.assets = [
                {"name": name, "size": size, "digest": None}
                for name, (size, _digest) in expected.items()
            ]
            with (
                mock.patch.object(release_publisher, "gh", side_effect=github),
                mock.patch.object(release_publisher, "verify_assets"),
            ):
                with self.assertRaisesRegex(ControlError, "has digest None"):
                    release_publisher.publish_release(
                        repository=REPOSITORY,
                        directory=directory,
                        config=config,
                        source_sha=SOURCE_SHA,
                        base_tag=BASE_TAG,
                        personal_tag=PERSONAL_TAG,
                    )

    def test_publish_requires_the_preflight_reservation(self) -> None:
        github = GitHubStub()
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config, _ = self.make_assets(directory)
            with (
                mock.patch.object(release_publisher, "gh", side_effect=github),
                mock.patch.object(release_publisher, "verify_assets"),
            ):
                with self.assertRaisesRegex(ControlError, "run reserve first"):
                    release_publisher.publish_release(
                        repository=REPOSITORY,
                        directory=directory,
                        config=config,
                        source_sha=SOURCE_SHA,
                        base_tag=BASE_TAG,
                        personal_tag=PERSONAL_TAG,
                    )


class PublicationRehearsalTests(unittest.TestCase):
    def deleting_stub(self, deleted: list[str]):
        def stub(*arguments: str) -> subprocess.CompletedProcess[str]:
            if arguments[:2] == ("release", "delete"):
                deleted.append(arguments[2])
            return completed()

        return stub

    def test_rehearsal_tags_come_only_from_numeric_run_ids(self) -> None:
        self.assertEqual(
            release_publisher.rehearsal_tag("30806253387"),
            "personal-rehearsal-30806253387",
        )
        for invalid in ("", "abc", "1 2", "1; rm -rf /", "../escape"):
            with self.subTest(run_id=invalid), self.assertRaises(ControlError):
                release_publisher.rehearsal_tag(invalid)

    def test_rehearsal_releases_are_invisible_to_the_local_updater(self) -> None:
        tag = release_publisher.rehearsal_tag("30806253387")
        releases = [
            {"tag_name": tag, "draft": False, "prerelease": False},
            {"tag_name": PERSONAL_TAG, "draft": False, "prerelease": False},
        ]
        self.assertEqual(
            local_updater.select_release(releases)["tag_name"], PERSONAL_TAG
        )
        with self.assertRaises(ControlError):
            local_updater.select_release(
                [{"tag_name": tag, "draft": False, "prerelease": False}]
            )

    def test_rehearsal_cleanup_refuses_to_delete_a_real_release(self) -> None:
        for tag in (PERSONAL_TAG, BASE_TAG, "personal-v0.64.22-r1"):
            with self.subTest(tag=tag), self.assertRaises(ControlError):
                release_publisher.delete_rehearsal_release(REPOSITORY, tag)

    def test_sweep_removes_only_expired_foreign_rehearsals(self) -> None:
        now = calendar.timegm(
            time.strptime("2026-08-03T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ")
        )
        releases = [
            {"tag_name": PERSONAL_TAG, "created_at": "2026-07-01T00:00:00Z"},
            {"tag_name": "personal-rehearsal-1", "created_at": "2026-08-03T00:00:00Z"},
            {"tag_name": "personal-rehearsal-2", "created_at": "2026-08-03T11:00:00Z"},
            {"tag_name": "personal-rehearsal-9", "created_at": "2026-08-02T00:00:00Z"},
        ]
        deleted: list[str] = []
        with mock.patch.object(
            release_publisher, "list_releases", return_value=releases
        ), mock.patch.object(
            release_publisher,
            "gh_allowing_failure",
            side_effect=self.deleting_stub(deleted),
        ):
            swept = release_publisher.sweep_stale_rehearsals(
                REPOSITORY, keep_tag="personal-rehearsal-9", now=now
            )

        self.assertEqual(swept, ["personal-rehearsal-1"])
        self.assertEqual(deleted, ["personal-rehearsal-1"])

    def test_rehearsal_deletes_its_scratch_release_after_a_failure(self) -> None:
        deleted: list[str] = []
        with mock.patch.dict(os.environ, {"GH_TOKEN": "token"}), mock.patch.object(
            release_publisher, "list_releases", return_value=[]
        ), mock.patch.object(
            release_publisher, "gh", side_effect=ControlError("release create failed")
        ), mock.patch.object(
            release_publisher,
            "gh_allowing_failure",
            side_effect=self.deleting_stub(deleted),
        ):
            with self.assertRaises(ControlError):
                release_publisher.rehearse_publication(
                    repository=REPOSITORY,
                    config=RELEASE_ASSET_CONFIG,
                    target_sha=SOURCE_SHA,
                    run_id="42",
                )

        self.assertEqual(
            deleted, ["personal-rehearsal-42", "personal-rehearsal-42"]
        )

    def test_rehearsal_verifies_uploaded_assets_before_and_after_exposure(self) -> None:
        deleted: list[str] = []
        verified: list[dict[str, object]] = []
        draft = {"id": 1, "tag_name": "personal-rehearsal-42", "draft": True}
        public = {"id": 1, "tag_name": "personal-rehearsal-42", "draft": False}

        with mock.patch.dict(os.environ, {"GH_TOKEN": "token"}), mock.patch.object(
            release_publisher, "list_releases", return_value=[]
        ), mock.patch.object(
            release_publisher, "gh", side_effect=lambda *a: completed()
        ), mock.patch.object(
            release_publisher,
            "gh_allowing_failure",
            side_effect=self.deleting_stub(deleted),
        ), mock.patch.object(
            release_publisher, "wait_for_release", side_effect=[draft, public]
        ), mock.patch.object(
            release_publisher, "find_release", return_value=draft
        ), mock.patch.object(
            release_publisher,
            "verify_remote_assets",
            side_effect=lambda repository, release, expected: verified.append(expected),
        ):
            result = release_publisher.rehearse_publication(
                repository=REPOSITORY,
                config=RELEASE_ASSET_CONFIG,
                target_sha=SOURCE_SHA,
                run_id="42",
            )

        self.assertEqual(result["tag"], "personal-rehearsal-42")
        self.assertEqual(len(verified), 2)
        self.assertEqual(
            sorted(verified[0]),
            [
                "cmux-personal-macos-arm64.zip",
                "cmux-personal-macos-arm64.zip.sha256",
                "cmux-personal-manifest.json",
            ],
        )
        self.assertEqual(deleted, ["personal-rehearsal-42", "personal-rehearsal-42"])


if __name__ == "__main__":
    unittest.main()
