from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import tempfile

from personal.common import ControlError, load_json
from personal.local_updater import (
    AppRunningError,
    inspect_bundle,
    install_staged_app,
    record_installation,
    stage_app,
    staged_bundle_path,
    validate_install_destination,
    validate_zip_paths,
)
from personal.local_validation import verify_receipt
from personal.verify_release_assets import verify_assets


def install_validated_receipt(
    receipt_path: pathlib.Path,
    *,
    config_path: pathlib.Path,
    data_root: pathlib.Path,
) -> dict[str, str]:
    receipt = verify_receipt(
        argparse.Namespace(receipt=str(receipt_path), config=str(config_path))
    )
    config = load_json(config_path)
    root = receipt_path.resolve().parent
    assets = root / str(receipt["assets_directory"])
    manifest = verify_assets(
        assets,
        config=config,
        source_sha=str(receipt["source_sha"]),
        base_tag=str(receipt["base_tag"]),
        personal_tag=str(receipt["personal_tag"]),
        upstream_main_sha=(
            str(receipt["upstream_main_sha"])
            if receipt.get("upstream_main_sha")
            else None
        ),
    )
    archive = assets / str(config["artifact_name"])
    validate_zip_paths(archive)
    destination = validate_install_destination(str(config["install_path"]))
    staged_app = staged_bundle_path(destination)
    staged_state_path = data_root / "staged.json"
    state_path = data_root / "current.json"
    data_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cmux-personal-local-install-") as temporary:
        extracted = pathlib.Path(temporary) / "extracted"
        extracted.mkdir()
        subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)],
            check=True,
        )
        app = extracted / f"{config['app_name']}.app"
        inspect_bundle(
            app,
            expected_bundle_identifier=str(config["bundle_identifier"]),
            expected_source_sha=str(receipt["source_sha"]),
            expected_base_tag=str(receipt["base_tag"]),
            expected_personal_tag=str(receipt["personal_tag"]),
            expected_upstream_repository=str(config["upstream_repository"]),
            expected_upstream_main_sha=(
                str(receipt["upstream_main_sha"])
                if receipt.get("upstream_main_sha")
                else None
            ),
        )
        stage_app(
            app,
            staged_app=staged_app,
            staged_state_path=staged_state_path,
            tag=str(receipt["personal_tag"]),
            manifest=manifest,
        )

    try:
        install_staged_app(
            staged_app,
            destination=destination,
            bundle_identifier=str(config["bundle_identifier"]),
            data_root=data_root,
        )
    except AppRunningError:
        return {
            "status": "staged",
            "personal_tag": str(receipt["personal_tag"]),
            "install_path": str(destination),
        }

    record_installation(
        str(receipt["personal_tag"]),
        manifest,
        state_path=state_path,
        staged_state_path=staged_state_path,
        install_path=destination,
    )
    return {
        "status": "installed",
        "personal_tag": str(receipt["personal_tag"]),
        "install_path": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install an exact locally built and validated cmux Personal app."
    )
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--data-root",
        default="~/.local/share/cmux-personal",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    result = install_validated_receipt(
        pathlib.Path(args.receipt),
        config_path=pathlib.Path(args.config),
        data_root=pathlib.Path(args.data_root).expanduser(),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, KeyError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"local installation blocked: {exc}") from exc
