from __future__ import annotations

import argparse
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from personal.common import ControlError, load_json


UPDATER_LABEL = "com.jules.cmux-personal-updater"
MONITOR_LABEL = "com.jules.cmux-personal-monitor"


def write_executable(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def install_monitor_control(
    control_root: pathlib.Path,
    destination: pathlib.Path,
    *,
    fork_repository: str,
    upstream_repository: str,
) -> pathlib.Path:
    """Install a launchd-readable copy of the monitor and its trusted inputs."""
    monitor_root = destination / "monitor-control"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", "tests")
    shutil.copytree(
        control_root / "personal",
        monitor_root / "personal",
        dirs_exist_ok=True,
        ignore=ignore,
    )
    shutil.copytree(
        control_root / ".github" / "pi",
        monitor_root / ".github" / "pi",
        dirs_exist_ok=True,
        ignore=ignore,
    )

    git_directory = monitor_root / ".git"
    if not git_directory.is_dir():
        result = subprocess.run(
            ["git", "init", "--quiet", str(monitor_root)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise ControlError(
                f"could not initialize installed monitor repository: {result.stderr.strip()}"
            )
    for name, repository in (
        ("origin", fork_repository),
        ("upstream", upstream_repository),
    ):
        url = f"https://github.com/{repository}.git"
        existing = subprocess.run(
            ["git", "-C", str(monitor_root), "remote", "get-url", name],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        operation = "set-url" if existing.returncode == 0 else "add"
        result = subprocess.run(
            ["git", "-C", str(monitor_root), "remote", operation, name, url],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise ControlError(
                f"could not configure installed monitor remote {name}: "
                f"{result.stderr.strip()}"
            )
    return monitor_root


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install the local cmux Personal release commands and services."
    )
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--no-load", action="store_true")
    args = parser.parse_args()

    control_root = pathlib.Path(args.control_root).resolve()
    source_config = pathlib.Path(args.config).resolve()
    config = load_json(source_config)
    expected_app_path = pathlib.Path.home() / "Applications" / "cmux Personal.app"
    configured_app_path = pathlib.Path(str(config["install_path"])).expanduser()
    if configured_app_path != expected_app_path:
        raise ControlError(
            f"refusing unexpected install path {configured_app_path}; expected {expected_app_path}"
        )
    gh_path = shutil.which("gh")
    if not gh_path:
        raise ControlError("GitHub CLI is required for provenance verification")
    destination = pathlib.Path("~/.local/share/cmux-personal").expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    monitor_control = install_monitor_control(
        control_root,
        destination,
        fork_repository=str(config["fork_repository"]),
        upstream_repository=str(config["upstream_repository"]),
    )
    package_destination = destination / "personal"
    package_destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        control_root / "personal" / "local_updater.py",
        package_destination / "local_updater.py",
    )
    shutil.copy2(control_root / "personal" / "common.py", package_destination / "common.py")
    shutil.copy2(
        control_root / "personal" / "official_runtime_manifest.py",
        package_destination / "official_runtime_manifest.py",
    )
    shutil.copy2(control_root / "personal" / "__init__.py", package_destination / "__init__.py")
    shutil.copy2(source_config, destination / "config.json")

    local_bin = pathlib.Path("~/.local/bin").expanduser()
    bundle_identifier = str(config["bundle_identifier"])
    app_path = configured_app_path
    write_executable(
        local_bin / "cmux-personal",
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f'APP={str(app_path)!r}',
                'CLI="$APP/Contents/Resources/bin/cmux"',
                'if [[ ! -x "$CLI" ]]; then',
                '  echo "cmux Personal is not installed at $APP" >&2',
                "  exit 1",
                "fi",
                f"export CMUX_BUNDLE_ID={bundle_identifier!r}",
                'exec "$CLI" "$@"',
                "",
            ]
        ),
    )
    python = pathlib.Path(sys.executable).resolve()
    write_executable(
        local_bin / "cmux-personal-update",
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"cd {str(destination)!r}",
                f"exec {str(python)!r} -m personal.local_updater "
                f"--config {str(destination / 'config.json')!r} \"$@\"",
                "",
            ]
        ),
    )
    write_executable(
        local_bin / "cmux-personal-sync",
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"exec {str(control_root / 'personal/ci/local_sync.sh')!r} \"$@\"",
                "",
            ]
        ),
    )
    write_executable(
        local_bin / "cmux-personal-build",
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"exec {str(control_root / 'personal/ci/local_release_check.sh')!r} \"$@\"",
                "",
            ]
        ),
    )
    write_executable(
        local_bin / "cmux-personal-publish",
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"exec {str(control_root / 'personal/ci/local_publish.sh')!r} \"$@\"",
                "",
            ]
        ),
    )

    logs = pathlib.Path("~/Library/Logs/cmux-personal").expanduser()
    logs.mkdir(parents=True, exist_ok=True)
    launch_agents = pathlib.Path("~/Library/LaunchAgents").expanduser()
    launch_agents.mkdir(parents=True, exist_ok=True)
    environment_path = ":".join(
        dict.fromkeys(
            [
                str(pathlib.Path(gh_path).parent),
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            ]
        )
    )
    updater_plist_path = launch_agents / f"{UPDATER_LABEL}.plist"
    updater_plist = {
        "Label": UPDATER_LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "personal.local_updater",
            "--config",
            str(destination / "config.json"),
            "--scheduled",
        ],
        "WorkingDirectory": str(destination),
        "EnvironmentVariables": {"PATH": environment_path},
        "RunAtLoad": True,
        # Ticks are cheap: a tick only contacts GitHub once per discovery interval, and
        # otherwise just checks whether a staged update can be swapped in. Polling this
        # often is what makes quitting the app enough to pick up a new release.
        "StartInterval": 120,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(logs / "updater.log"),
        "StandardErrorPath": str(logs / "updater-error.log"),
    }
    monitor_plist_path = launch_agents / f"{MONITOR_LABEL}.plist"
    monitor_plist = {
        "Label": MONITOR_LABEL,
        "ProgramArguments": [
            "/bin/bash",
            str(monitor_control / "personal" / "ci" / "local_sync.sh"),
            "--channel",
            "auto",
            "--data-root",
            str(destination),
            "--scheduled",
        ],
        "WorkingDirectory": str(monitor_control),
        "EnvironmentVariables": {"PATH": environment_path},
        "RunAtLoad": True,
        "StartInterval": 6 * 60 * 60,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(logs / "monitor.log"),
        "StandardErrorPath": str(logs / "monitor-error.log"),
    }
    for plist_path, plist in (
        (updater_plist_path, updater_plist),
        (monitor_plist_path, monitor_plist),
    ):
        with plist_path.open("wb") as handle:
            plistlib.dump(plist, handle, sort_keys=False)

    if not args.no_load:
        domain = f"gui/{os.getuid()}"
        for label, plist_path in (
            (UPDATER_LABEL, updater_plist_path),
            (MONITOR_LABEL, monitor_plist_path),
        ):
            subprocess.run(
                ["/bin/launchctl", "bootout", f"{domain}/{label}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            result = subprocess.run(
                ["/bin/launchctl", "bootstrap", domain, str(plist_path)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                raise ControlError(f"could not load {label}: {result.stderr.strip()}")

    print(f"installed updater: {destination}")
    print(f"installed monitor control: {monitor_control}")
    print(f"installed CLI: {local_bin / 'cmux-personal'}")
    print(f"installed local sync command: {local_bin / 'cmux-personal-sync'}")
    print(f"installed local build command: {local_bin / 'cmux-personal-build'}")
    print(f"installed local publish command: {local_bin / 'cmux-personal-publish'}")
    print(f"installed updater LaunchAgent: {updater_plist_path}")
    print(f"installed monitor LaunchAgent: {monitor_plist_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, KeyError, OSError) as exc:
        raise SystemExit(f"local pipeline setup stopped: {exc}") from exc
