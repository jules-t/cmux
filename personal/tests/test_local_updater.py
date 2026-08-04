from __future__ import annotations

import fcntl
import json
import os
import pathlib
import plistlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from personal.common import ControlError, utc_now, write_json
from personal.local_updater import (
    installation_status,
    main,
    read_staged,
    release_key,
    select_release,
    should_discover,
    stage_app,
    staged_bundle_path,
    validate_install_destination,
)


class LocalUpdaterTests(unittest.TestCase):
    def _write_config(
        self,
        root: pathlib.Path,
        *,
        archive_name: str = "cmux-personal-macos-arm64.zip",
        manifest_name: str = "cmux-personal-manifest.json",
    ) -> pathlib.Path:
        config_path = root / "config.json"
        write_json(
            config_path,
            {
                "fork_repository": "jules-t/cmux",
                "upstream_repository": "manaflow-ai/cmux",
                "app_name": "cmux Personal",
                "bundle_identifier": "com.cmuxterm.app.staging.personal",
                "artifact_name": archive_name,
                "manifest_name": manifest_name,
                "install_path": "~/Applications/cmux Personal.app",
                "allowed_attestation_workflows": [
                    "jules-t/cmux/.github/workflows/personal-build.yml"
                ],
            },
        )
        return config_path

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

    def test_install_uses_configured_app_path_after_downloading_assets(self) -> None:
        digest = "d" * 64
        tag = "personal-v0.64.20-r1"
        archive_name = "cmux-personal-macos-arm64.zip"
        manifest_name = "cmux-personal-manifest.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path = self._write_config(
                root,
                archive_name=archive_name,
                manifest_name=manifest_name,
            )
            release = {
                "tag_name": tag,
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": name,
                        "browser_download_url": f"https://example.invalid/{name}",
                    }
                    for name in (archive_name, manifest_name, f"{archive_name}.sha256")
                ],
            }

            def fake_download(_url: str, destination: pathlib.Path) -> None:
                if destination.name == manifest_name:
                    destination.write_text(
                        json.dumps(
                            {
                                "personal_tag": tag,
                                "repository": "jules-t/cmux",
                                "archive_name": archive_name,
                                "archive_sha256": digest,
                                "source_sha": "a" * 40,
                                "base_tag": "v0.64.20",
                            }
                        ),
                        encoding="utf-8",
                    )
                elif destination.name.endswith(".sha256"):
                    destination.write_text(
                        f"{digest}  {archive_name}\n",
                        encoding="utf-8",
                    )
                else:
                    destination.write_bytes(b"archive")

            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path), "--tag", tag],
                ),
                mock.patch("personal.local_updater.request_json", return_value=[release]),
                mock.patch("personal.local_updater.download", side_effect=fake_download),
                mock.patch("personal.local_updater.sha256", return_value=digest),
                mock.patch("personal.local_updater.verify_attestation"),
                mock.patch("personal.local_updater.validate_zip_paths"),
                mock.patch("personal.local_updater.subprocess.run"),
                mock.patch("personal.local_updater.inspect_bundle"),
                mock.patch("personal.local_updater.stage_app") as stage_app,
                mock.patch("personal.local_updater.install_staged_app") as install_staged_app,
                mock.patch("personal.local_updater.notify"),
            ):
                self.assertEqual(main(), 0)

            destination = root / "Applications" / "cmux Personal.app"
            self.assertEqual(
                install_staged_app.call_args.kwargs["destination"],
                destination,
            )
            self.assertEqual(
                stage_app.call_args.kwargs["staged_app"],
                staged_bundle_path(destination),
            )

    def _stage(self, root: pathlib.Path, tag: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        config_path = self._write_config(root)
        destination = root / "Applications" / "cmux Personal.app"
        staged_app = staged_bundle_path(destination)
        staged_app.mkdir(parents=True)
        (staged_app / "marker").write_text(tag, encoding="utf-8")
        write_json(
            root / ".local/share/cmux-personal/staged.json",
            {
                "schema_version": 1,
                "personal_tag": tag,
                "source_sha": "b" * 40,
                "base_tag": "v0.64.20",
                "staged_at": "2026-08-04T00:00:00Z",
                "staged_path": str(staged_app),
            },
        )
        return config_path, destination, staged_app

    def test_staged_update_installs_without_network_once_the_app_closes(self) -> None:
        tag = "personal-v0.64.22-r2"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path, destination, staged_app = self._stage(root, tag)
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path), "--scheduled"],
                ),
                mock.patch("personal.local_updater.request_json") as request_json,
                mock.patch("personal.local_updater.inspect_bundle"),
                mock.patch("personal.local_updater.is_running", return_value=False),
                mock.patch("personal.local_updater.notify"),
            ):
                self.assertEqual(main(), 0)

            request_json.assert_not_called()
            self.assertEqual((destination / "marker").read_text(encoding="utf-8"), tag)
            self.assertFalse(staged_app.exists())
            self.assertFalse((root / ".local/share/cmux-personal/staged.json").exists())
            state = json.loads(
                (root / ".local/share/cmux-personal/current.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["personal_tag"], tag)

    def test_staged_update_is_kept_while_the_app_is_open(self) -> None:
        tag = "personal-v0.64.22-r2"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path, destination, staged_app = self._stage(root, tag)
            release = {"tag_name": tag, "draft": False, "prerelease": False, "assets": []}
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path), "--scheduled"],
                ),
                mock.patch("personal.local_updater.request_json", return_value=[release]),
                mock.patch("personal.local_updater.inspect_bundle"),
                mock.patch("personal.local_updater.is_running", return_value=True),
                mock.patch("personal.local_updater.notify"),
            ):
                self.assertEqual(main(), 0)

            self.assertTrue(staged_app.is_dir())
            self.assertFalse(destination.exists())

    def test_scheduled_tick_skips_github_until_the_discovery_interval_elapses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path, _, staged_app = self._stage(root, "personal-v0.64.22-r2")
            shutil.rmtree(staged_app)
            write_json(
                root / ".local/share/cmux-personal/check.json",
                {"schema_version": 1, "last_checked_at": utc_now()},
            )
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path), "--scheduled"],
                ),
                mock.patch("personal.local_updater.request_json") as request_json,
                mock.patch("personal.local_updater.is_running", return_value=True),
            ):
                self.assertEqual(main(), 0)

            request_json.assert_not_called()

    def test_manual_runs_always_contact_github(self) -> None:
        tag = "personal-v0.64.22-r2"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path, destination, _ = self._stage(root, tag)
            write_json(
                root / ".local/share/cmux-personal/check.json",
                {"schema_version": 1, "last_checked_at": utc_now()},
            )
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path)],
                ),
                mock.patch(
                    "personal.local_updater.request_json",
                    return_value=[
                        {
                            "tag_name": tag,
                            "draft": False,
                            "prerelease": False,
                            "assets": [],
                        }
                    ],
                ) as request_json,
                mock.patch("personal.local_updater.inspect_bundle"),
                mock.patch("personal.local_updater.is_running", return_value=False),
                mock.patch("personal.local_updater.notify"),
            ):
                self.assertEqual(main(), 0)

            request_json.assert_called_once()
            self.assertEqual((destination / "marker").read_text(encoding="utf-8"), tag)

    def test_scheduled_tick_does_not_replace_a_newer_installed_release(self) -> None:
        staged_tag = "personal-v0.64.22-r1"
        installed_tag = "personal-v0.64.22-r2"
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path, destination, staged_app = self._stage(root, staged_tag)
            contents = destination / "Contents"
            contents.mkdir(parents=True)
            (destination / "marker").write_text(installed_tag, encoding="utf-8")
            with (contents / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.cmuxterm.app.staging.personal",
                        "CMUXPersonalReleaseTag": installed_tag,
                    },
                    handle,
                )
            write_json(
                root / ".local/share/cmux-personal/check.json",
                {"schema_version": 1, "last_checked_at": utc_now()},
            )

            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path), "--scheduled"],
                ),
                mock.patch("personal.local_updater.request_json") as request_json,
                mock.patch("personal.local_updater.inspect_bundle"),
                mock.patch("personal.local_updater.is_running", return_value=False),
                mock.patch("personal.local_updater.notify"),
            ):
                self.assertEqual(main(), 0)

            request_json.assert_not_called()
            self.assertEqual(
                (destination / "marker").read_text(encoding="utf-8"),
                installed_tag,
            )
            self.assertTrue(staged_app.is_dir())

    def test_staged_bundle_is_ignored_when_its_metadata_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _, destination, staged_app = self._stage(root, "personal-v0.64.22-r2")
            staged_state_path = root / ".local/share/cmux-personal/staged.json"
            self.assertTrue(read_staged(staged_state_path, staged_app))

            write_json(
                staged_state_path,
                {
                    "schema_version": 1,
                    "personal_tag": "personal-v0.64.22-r2",
                    "source_sha": "b" * 40,
                    "base_tag": "v0.64.20",
                    "staged_at": "2026-08-04T00:00:00Z",
                    "staged_path": str(destination.parent / "somewhere-else.app"),
                },
            )
            self.assertFalse(read_staged(staged_state_path, staged_app))

    def test_failed_replacement_keeps_the_existing_staged_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _, _, staged_app = self._stage(root, "personal-v0.64.22-r1")
            staged_state_path = root / ".local/share/cmux-personal/staged.json"
            previous_state = staged_state_path.read_bytes()
            extracted_app = root / "extracted/cmux Personal.app"
            extracted_app.mkdir(parents=True)

            with mock.patch(
                "personal.local_updater.subprocess.run",
                side_effect=OSError("ditto failed"),
            ):
                with self.assertRaisesRegex(OSError, "ditto failed"):
                    stage_app(
                        extracted_app,
                        staged_app=staged_app,
                        staged_state_path=staged_state_path,
                        tag="personal-v0.64.22-r2",
                        manifest={
                            "source_sha": "c" * 40,
                            "base_tag": "v0.64.20",
                        },
                    )

            self.assertEqual(
                (staged_app / "marker").read_text(encoding="utf-8"),
                "personal-v0.64.22-r1",
            )
            self.assertEqual(staged_state_path.read_bytes(), previous_state)

    def test_failed_staged_swap_restores_the_existing_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            _, _, staged_app = self._stage(root, "personal-v0.64.22-r1")
            staged_state_path = root / ".local/share/cmux-personal/staged.json"
            previous_state = staged_state_path.read_bytes()
            extracted_app = root / "extracted/cmux Personal.app"
            extracted_app.mkdir(parents=True)
            (extracted_app / "marker").write_text("personal-v0.64.22-r2", encoding="utf-8")
            real_replace = os.replace

            def fake_run(command: list[str], **_kwargs: object) -> None:
                if command[0] == "/usr/bin/ditto":
                    shutil.copytree(command[1], command[2])

            def fail_final_swap(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
                if ".building-" in pathlib.Path(source).name:
                    raise OSError("swap failed")
                real_replace(source, destination)

            with (
                mock.patch("personal.local_updater.subprocess.run", side_effect=fake_run),
                mock.patch("personal.local_updater.os.replace", side_effect=fail_final_swap),
            ):
                with self.assertRaisesRegex(OSError, "swap failed"):
                    stage_app(
                        extracted_app,
                        staged_app=staged_app,
                        staged_state_path=staged_state_path,
                        tag="personal-v0.64.22-r2",
                        manifest={"source_sha": "c" * 40, "base_tag": "v0.64.20"},
                    )

            self.assertEqual(
                (staged_app / "marker").read_text(encoding="utf-8"),
                "personal-v0.64.22-r1",
            )
            self.assertEqual(staged_state_path.read_bytes(), previous_state)

    def test_discovery_is_due_again_once_the_interval_elapses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            check_path = pathlib.Path(temporary) / "check.json"
            self.assertTrue(should_discover(check_path, interval_seconds=60))

            write_json(check_path, {"schema_version": 1, "last_checked_at": utc_now()})
            self.assertFalse(should_discover(check_path, interval_seconds=60))
            self.assertTrue(should_discover(check_path, interval_seconds=0))

            write_json(check_path, {"schema_version": 1, "last_checked_at": "not a timestamp"})
            self.assertTrue(should_discover(check_path, interval_seconds=60))
            write_json(
                check_path,
                {"schema_version": 1, "last_checked_at": "2026-08-04T00:00:00"},
            )
            self.assertTrue(should_discover(check_path, interval_seconds=60))

    def test_lock_is_released_after_failure_with_retained_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config_path = self._write_config(root)
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["local_updater.py", "--config", str(config_path)],
                ),
                mock.patch(
                    "personal.local_updater.request_json",
                    side_effect=ControlError("simulated failure"),
                ),
            ):
                try:
                    main()
                except ControlError as exc:
                    retained_exception = exc
                else:
                    self.fail("expected the simulated updater failure")

            lock_path = root / ".local/share/cmux-personal/updater.lock"
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            self.assertEqual(str(retained_exception), "simulated failure")


if __name__ == "__main__":
    unittest.main()
