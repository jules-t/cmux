from __future__ import annotations

import copy
import unittest

from personal.common import ControlError
from personal.official_runtime_manifest import (
    nightly_runtime_build,
    validate_official_runtime_manifest,
)


def manifest_fixture() -> dict:
    tag = "v0.64.20"
    root = f"https://github.com/manaflow-ai/cmux/releases/download/{tag}"
    entries = []
    for go_os, go_arch in (
        ("darwin", "arm64"),
        ("darwin", "amd64"),
        ("linux", "arm64"),
        ("linux", "amd64"),
    ):
        asset = f"cmuxd-remote-{go_os}-{go_arch}"
        entries.append(
            {
                "goOS": go_os,
                "goArch": go_arch,
                "assetName": asset,
                "downloadURL": f"{root}/{asset}",
                "sha256": "a" * 64,
            }
        )
    return {
        "schemaVersion": 1,
        "appVersion": "0.64.20",
        "releaseTag": tag,
        "releaseURL": root,
        "checksumsAssetName": "cmuxd-remote-checksums.txt",
        "checksumsURL": f"{root}/cmuxd-remote-checksums.txt",
        "entries": entries,
    }


def nightly_manifest_fixture(runtime_build: str = "3053117246701") -> dict:
    root = "https://github.com/manaflow-ai/cmux/releases/download/nightly"
    entries = []
    for go_os, go_arch in (
        ("darwin", "arm64"),
        ("darwin", "amd64"),
        ("linux", "arm64"),
        ("linux", "amd64"),
    ):
        asset = f"cmuxd-remote-{go_os}-{go_arch}-{runtime_build}"
        entries.append(
            {
                "goOS": go_os,
                "goArch": go_arch,
                "assetName": asset,
                "downloadURL": f"{root}/{asset}",
                "sha256": "b" * 64,
            }
        )
    return {
        "schemaVersion": 1,
        "appVersion": f"0.64.20-nightly.{runtime_build}",
        "releaseTag": "nightly",
        "releaseURL": root,
        "checksumsAssetName": f"cmuxd-remote-checksums-{runtime_build}.txt",
        "checksumsURL": f"{root}/cmuxd-remote-checksums-{runtime_build}.txt",
        "entries": entries,
    }


class OfficialRuntimeManifestTests(unittest.TestCase):
    def test_accepts_exact_official_release_shape(self) -> None:
        value = manifest_fixture()
        self.assertIs(
            validate_official_runtime_manifest(
                value,
                repository="manaflow-ai/cmux",
                base_tag="v0.64.20",
            ),
            value,
        )

    def test_rejects_cross_release_payload_urls(self) -> None:
        value = copy.deepcopy(manifest_fixture())
        value["entries"][0]["downloadURL"] = value["entries"][0][
            "downloadURL"
        ].replace("v0.64.20", "v0.64.19")
        with self.assertRaises(ControlError):
            validate_official_runtime_manifest(
                value,
                repository="manaflow-ai/cmux",
                base_tag="v0.64.20",
            )

    def test_requires_every_supported_remote_platform(self) -> None:
        value = manifest_fixture()
        value["entries"].pop()
        with self.assertRaises(ControlError):
            validate_official_runtime_manifest(
                value,
                repository="manaflow-ai/cmux",
                base_tag="v0.64.20",
            )

    def test_accepts_immutable_nightly_runtime_shape(self) -> None:
        value = nightly_manifest_fixture()
        self.assertEqual(
            nightly_runtime_build(
                value,
                repository="manaflow-ai/cmux",
                base_tag="v0.64.20",
            ),
            "3053117246701",
        )
        self.assertIs(
            validate_official_runtime_manifest(
                value,
                repository="manaflow-ai/cmux",
                base_tag="v0.64.20",
                channel="nightly",
                runtime_build="3053117246701",
            ),
            value,
        )

    def test_rejects_nightly_asset_from_another_build(self) -> None:
        value = nightly_manifest_fixture()
        value["entries"][0]["assetName"] = "cmuxd-remote-darwin-arm64-999"
        with self.assertRaises(ControlError):
            validate_official_runtime_manifest(
                value,
                repository="manaflow-ai/cmux",
                base_tag="v0.64.20",
                channel="nightly",
                runtime_build="3053117246701",
            )


if __name__ == "__main__":
    unittest.main()
