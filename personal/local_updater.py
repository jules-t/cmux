from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import plistlib
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Any

from personal.common import ControlError, PERSONAL_TAG_RE, load_json, utc_now, write_json
from personal.official_runtime_manifest import validate_official_runtime_manifest


class AppRunningError(ControlError):
    pass


def request_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "cmux-personal-local-updater/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ControlError(f"could not read {url}: {exc}") from exc


def release_key(tag: str) -> tuple[int, int, int, int]:
    match = PERSONAL_TAG_RE.fullmatch(tag)
    if not match:
        raise ControlError(f"invalid personal release tag: {tag!r}")
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def select_release(releases: list[dict[str, Any]], requested_tag: str | None = None) -> dict[str, Any]:
    candidates = [
        release
        for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and not release.get("prerelease")
        and PERSONAL_TAG_RE.fullmatch(str(release.get("tag_name", "")))
    ]
    if requested_tag:
        for release in candidates:
            if release.get("tag_name") == requested_tag:
                return release
        raise ControlError(f"personal release not found: {requested_tag}")
    if not candidates:
        raise ControlError("no published cmux Personal release exists")
    return max(candidates, key=lambda release: release_key(str(release["tag_name"])))


def release_assets(release: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for asset in release.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result[name] = url
    return result


def download(url: str, destination: pathlib.Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cmux-personal-local-updater/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ControlError(f"could not download {url}: {exc}") from exc


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_zip_paths(path: pathlib.Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                candidate = pathlib.PurePosixPath(name)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise ControlError(f"archive contains an unsafe path: {name}")
    except zipfile.BadZipFile as exc:
        raise ControlError(f"invalid ZIP archive: {exc}") from exc


def verify_attestation(
    archive: pathlib.Path,
    *,
    repository: str,
    allowed_workflows: list[str],
) -> None:
    if not shutil.which("gh"):
        raise ControlError("GitHub CLI is required to verify build provenance")
    failures: list[str] = []
    for workflow in allowed_workflows:
        result = subprocess.run(
            [
                "gh",
                "attestation",
                "verify",
                str(archive),
                "--repo",
                repository,
                "--signer-workflow",
                workflow,
                "--deny-self-hosted-runners",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode == 0:
            return
        failures.append(result.stderr.strip() or result.stdout.strip())
    raise ControlError("build provenance verification failed: " + " | ".join(failures))


def inspect_bundle(
    app: pathlib.Path,
    *,
    expected_bundle_identifier: str,
    expected_source_sha: str,
    expected_base_tag: str,
    expected_personal_tag: str,
    expected_upstream_repository: str,
    expected_upstream_main_sha: str | None = None,
) -> None:
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise ControlError("downloaded app has no Info.plist")
    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ControlError(f"downloaded app has an invalid Info.plist: {exc}") from exc
    checks = {
        "CFBundleIdentifier": expected_bundle_identifier,
        "CMUXPersonalSourceSHA": expected_source_sha,
        "CMUXPersonalBaseTag": expected_base_tag,
        "CMUXPersonalReleaseTag": expected_personal_tag,
    }
    for key, expected in checks.items():
        if plist.get(key) != expected:
            raise ControlError(
                f"downloaded app has unexpected {key}: {plist.get(key)!r} (expected {expected!r})"
            )
    if expected_upstream_main_sha:
        if plist.get("CMUXPersonalUpstreamMainSHA") != expected_upstream_main_sha:
            raise ControlError("downloaded app has an unexpected upstream main base")
    elif "CMUXPersonalUpstreamMainSHA" in plist:
        raise ControlError("stable downloaded app unexpectedly declares an upstream main base")
    raw_runtime_manifest = plist.get("CMUXRemoteDaemonManifestJSON")
    if not isinstance(raw_runtime_manifest, str):
        raise ControlError("downloaded app has no embedded remote runtime manifest")
    try:
        runtime_manifest = json.loads(raw_runtime_manifest)
    except json.JSONDecodeError as exc:
        raise ControlError(f"downloaded app has an invalid remote runtime manifest: {exc}") from exc
    if not isinstance(runtime_manifest, dict):
        raise ControlError("downloaded app remote runtime manifest is not an object")
    validate_official_runtime_manifest(
        runtime_manifest,
        repository=expected_upstream_repository,
        base_tag=expected_base_tag,
        channel="nightly" if expected_upstream_main_sha else "stable",
    )
    binary = app / "Contents" / "MacOS" / "cmux"
    cli = app / "Contents" / "Resources" / "bin" / "cmux"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ControlError("downloaded app main binary is missing or non-executable")
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ControlError("downloaded app CLI is missing or non-executable")

    signature = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if signature.returncode != 0:
        raise ControlError("downloaded app code signature is invalid: " + signature.stderr.strip())
    architecture = subprocess.run(
        ["/usr/bin/lipo", str(binary), "-verify_arch", "arm64"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if architecture.returncode != 0:
        raise ControlError("downloaded app is not arm64: " + architecture.stderr.strip())


def is_running(app_path: pathlib.Path) -> bool:
    executable = app_path / "Contents" / "MacOS" / "cmux"
    result = subprocess.run(
        ["/usr/bin/pgrep", "-f", re.escape(str(executable))],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def validate_install_destination(configured_path: str) -> pathlib.Path:
    destination = pathlib.Path(configured_path).expanduser()
    expected = pathlib.Path.home() / "Applications" / "cmux Personal.app"
    if destination != expected:
        raise ControlError(
            f"refusing unexpected install destination {destination}; expected exactly {expected}"
        )
    if destination.is_symlink():
        raise ControlError(f"refusing symlinked install destination: {destination}")
    return destination


def existing_bundle_tag(app: pathlib.Path, expected_bundle_identifier: str) -> str | None:
    if not app.exists():
        return None
    if not app.is_dir() or app.is_symlink():
        raise ControlError(f"existing install is not a regular app directory: {app}")
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise ControlError(f"existing install has no Info.plist: {app}")
    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ControlError(f"existing install has an invalid Info.plist: {exc}") from exc
    if plist.get("CFBundleIdentifier") != expected_bundle_identifier:
        raise ControlError(
            "refusing to replace an app that is not the managed cmux Personal bundle"
        )
    tag = plist.get("CMUXPersonalReleaseTag")
    return tag if isinstance(tag, str) and PERSONAL_TAG_RE.fullmatch(tag) else None


def notify(title: str, message: str) -> None:
    escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            f'display notification "{escaped_message}" with title "{escaped_title}"',
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prune_backups(backups: pathlib.Path, keep: int = 2) -> None:
    entries = sorted(
        [entry for entry in backups.iterdir() if entry.name.endswith(".app")],
        key=lambda entry: entry.stat().st_mtime,
        reverse=True,
    )
    for entry in entries[keep:]:
        shutil.rmtree(entry)


def install_app(
    extracted_app: pathlib.Path,
    *,
    destination: pathlib.Path,
    bundle_identifier: str,
    data_root: pathlib.Path,
) -> None:
    old_tag = existing_bundle_tag(destination, bundle_identifier)
    if is_running(destination):
        raise AppRunningError(
            "cmux Personal is running; close it and the updater will retry without killing it"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    backups = data_root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.installing-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    subprocess.run(["/usr/bin/ditto", str(extracted_app), str(staging)], check=True)
    subprocess.run(
        ["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(staging)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if is_running(destination):
        shutil.rmtree(staging)
        raise AppRunningError(
            "cmux Personal started while its update was being prepared; close it and retry"
        )

    backup: pathlib.Path | None = None
    try:
        if destination.exists():
            stamp = utc_now().replace(":", "").replace("-", "")
            backup_tag = old_tag or "unknown-personal-version"
            backup = backups / f"{stamp}-{backup_tag}.app"
            os.replace(destination, backup)
        os.replace(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if backup and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    prune_backups(backups)


def install_state(state_path: pathlib.Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {}
    try:
        value = load_json(state_path)
    except (ControlError, OSError, json.JSONDecodeError):
        return {}
    return value


def installation_status(
    *,
    destination: pathlib.Path,
    state: dict[str, Any],
    expected_bundle_identifier: str,
    expected_personal_tag: str,
    expected_upstream_repository: str,
) -> tuple[bool, str | None, str | None, str]:
    state_tag = state.get("personal_tag")
    normalized_state_tag = state_tag if isinstance(state_tag, str) else None
    bundle_tag = existing_bundle_tag(destination, expected_bundle_identifier)
    if normalized_state_tag != expected_personal_tag:
        return (
            False,
            normalized_state_tag,
            bundle_tag,
            "the updater state does not match the latest release",
        )
    if bundle_tag != expected_personal_tag:
        return (
            False,
            normalized_state_tag,
            bundle_tag,
            "the installed app is missing or does not match the updater state",
        )
    source_sha = state.get("source_sha")
    if not isinstance(source_sha, str) or not source_sha:
        return (
            False,
            normalized_state_tag,
            bundle_tag,
            "the updater state has no source commit",
        )
    base_tag = state.get("base_tag")
    if not isinstance(base_tag, str) or not base_tag:
        return (
            False,
            normalized_state_tag,
            bundle_tag,
            "the updater state has no official base tag",
        )
    try:
        inspect_bundle(
            destination,
            expected_bundle_identifier=expected_bundle_identifier,
            expected_source_sha=source_sha,
            expected_base_tag=base_tag,
            expected_personal_tag=expected_personal_tag,
            expected_upstream_repository=expected_upstream_repository,
            expected_upstream_main_sha=(
                str(state["upstream_main_sha"])
                if state.get("upstream_main_sha")
                else None
            ),
        )
    except ControlError as exc:
        return (
            False,
            normalized_state_tag,
            bundle_tag,
            f"the installed app failed verification: {exc}",
        )
    return True, normalized_state_tag, bundle_tag, "the installed app is verified"


def _main(resources: contextlib.ExitStack) -> int:
    parser = argparse.ArgumentParser(description="Verify and install cmux Personal releases.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--tag")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    data_root = pathlib.Path("~/.local/share/cmux-personal").expanduser()
    data_root.mkdir(parents=True, exist_ok=True)
    lock_handle = resources.enter_context(
        (data_root / "updater.lock").open("a+", encoding="utf-8")
    )
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another cmux Personal updater is already running")
        return 0

    repository = str(config["fork_repository"])
    releases = request_json(f"https://api.github.com/repos/{repository}/releases?per_page=30")
    if not isinstance(releases, list):
        raise ControlError("GitHub releases response is not a list")
    release = select_release(releases, args.tag)
    tag = str(release["tag_name"])

    state_path = data_root / "current.json"
    destination = validate_install_destination(str(config["install_path"]))
    verified_current, state_tag, bundle_tag, status_detail = installation_status(
        destination=destination,
        state=install_state(state_path),
        expected_bundle_identifier=str(config["bundle_identifier"]),
        expected_personal_tag=tag,
        expected_upstream_repository=str(config["upstream_repository"]),
    )
    if args.check_only:
        print(
            json.dumps(
                {
                    "latest": tag,
                    "state_tag": state_tag,
                    "bundle_tag": bundle_tag,
                    "verified_current": verified_current,
                    "update_available": not verified_current,
                    "detail": status_detail,
                }
            )
        )
        return 0
    if verified_current:
        print(f"cmux Personal is current at {tag}")
        return 0
    print(f"cmux Personal requires installation: {status_detail}")

    assets = release_assets(release)
    archive_name = str(config["artifact_name"])
    manifest_name = str(config["manifest_name"])
    required = [archive_name, manifest_name, f"{archive_name}.sha256"]
    missing = [name for name in required if name not in assets]
    if missing:
        raise ControlError(f"release {tag} is missing assets: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="cmux-personal-update-") as temporary:
        root = pathlib.Path(temporary)
        archive = root / archive_name
        manifest_path = root / manifest_name
        checksum_path = root / f"{archive_name}.sha256"
        for name, asset_destination in (
            (archive_name, archive),
            (manifest_name, manifest_path),
            (f"{archive_name}.sha256", checksum_path),
        ):
            download(assets[name], asset_destination)

        manifest = load_json(manifest_path)
        if manifest.get("personal_tag") != tag:
            raise ControlError("manifest release tag does not match the GitHub release")
        if manifest.get("repository") != repository:
            raise ControlError("manifest repository does not match the configured fork")
        if manifest.get("archive_name") != archive_name:
            raise ControlError("manifest archive name does not match the configured artifact")
        digest = sha256(archive)
        if manifest.get("archive_sha256") != digest:
            raise ControlError("archive digest does not match the manifest")
        checksum_fields = checksum_path.read_text(encoding="utf-8").strip().split()
        if checksum_fields != [digest, archive_name]:
            raise ControlError("checksum asset does not match the archive")
        verify_attestation(
            archive,
            repository=repository,
            allowed_workflows=list(config["allowed_attestation_workflows"]),
        )
        validate_zip_paths(archive)

        extracted = root / "extracted"
        extracted.mkdir()
        subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)],
            check=True,
        )
        app = extracted / f"{config['app_name']}.app"
        inspect_bundle(
            app,
            expected_bundle_identifier=str(config["bundle_identifier"]),
            expected_source_sha=str(manifest["source_sha"]),
            expected_base_tag=str(manifest["base_tag"]),
            expected_personal_tag=tag,
            expected_upstream_repository=str(config["upstream_repository"]),
            expected_upstream_main_sha=(
                str(manifest["upstream_main_sha"])
                if manifest.get("upstream_main_sha")
                else None
            ),
        )
        install_app(
            app,
            destination=destination,
            bundle_identifier=str(config["bundle_identifier"]),
            data_root=data_root,
        )

    data_root.mkdir(parents=True, exist_ok=True)
    installed_state = {
        "schema_version": 1,
        "personal_tag": tag,
        "source_sha": manifest["source_sha"],
        "base_tag": manifest["base_tag"],
        "installed_at": utc_now(),
        "install_path": str(validate_install_destination(str(config["install_path"]))),
    }
    if manifest.get("upstream_main_sha"):
        installed_state["upstream_main_sha"] = manifest["upstream_main_sha"]
    write_json(state_path, installed_state)
    notify("cmux Personal updated", f"Installed {tag}. It will be used the next time you open it.")
    print(f"installed cmux Personal {tag}")
    return 0


def main() -> int:
    with contextlib.ExitStack() as resources:
        return _main(resources)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppRunningError as exc:
        notify("cmux Personal update waiting", str(exc))
        raise SystemExit(3) from exc
    except (ControlError, KeyError, subprocess.CalledProcessError, OSError) as exc:
        notify("cmux Personal update stopped", str(exc))
        raise SystemExit(f"cmux Personal updater stopped: {exc}") from exc
