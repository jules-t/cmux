from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from personal import release_publisher
from personal.common import ControlError


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
    ) -> None:
        self.release = release
        self.source_sha = source_sha
        self.annotated = annotated
        self.assets: list[dict[str, object]] = []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        if arguments[:3] == ("api", "--paginate", "--slurp"):
            path = arguments[3]
            if path == f"repos/{REPOSITORY}/releases?per_page=100":
                releases = [] if self.release is None else [self.release]
                return completed([releases])
            if path == f"repos/{REPOSITORY}/releases/1/assets?per_page=100":
                return completed([self.assets])
        if arguments[:2] == ("api", f"repos/{REPOSITORY}/git/ref/tags/{PERSONAL_TAG}"):
            object_type = "tag" if self.annotated else "commit"
            object_sha = "c" * 40 if self.annotated else self.source_sha
            return completed({"object": {"type": object_type, "sha": object_sha}})
        if arguments[:2] == ("api", f"repos/{REPOSITORY}/git/tags/{'c' * 40}"):
            return completed({"object": {"type": "commit", "sha": self.source_sha}})
        if arguments[:3] == ("release", "create", PERSONAL_TAG):
            target = arguments[arguments.index("--target") + 1]
            self.source_sha = target
            self.release = {
                "id": 1,
                "tag_name": PERSONAL_TAG,
                "draft": True,
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
    return {"id": 1, "tag_name": PERSONAL_TAG, "draft": True}


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

    def test_reserve_reuses_an_exact_annotated_draft_and_exercises_edit(self) -> None:
        github = GitHubStub(release=draft_release(), annotated=True)

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
        self.assertTrue(
            any(
                call[:2] == ("api", f"repos/{REPOSITORY}/git/tags/{'c' * 40}")
                for call in github.calls
            )
        )

    def test_reserve_rejects_a_draft_whose_tag_points_elsewhere(self) -> None:
        github = GitHubStub(release=draft_release(), source_sha=OTHER_SHA)

        with mock.patch.object(release_publisher, "gh", side_effect=github):
            with self.assertRaisesRegex(ControlError, "points to"):
                release_publisher.reserve_release(
                    repository=REPOSITORY,
                    personal_tag=PERSONAL_TAG,
                    source_sha=SOURCE_SHA,
                    base_tag=BASE_TAG,
                )

        self.assertFalse(any(call[:2] == ("release", "edit") for call in github.calls))

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
        github = GitHubStub(release=release)
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


if __name__ == "__main__":
    unittest.main()
