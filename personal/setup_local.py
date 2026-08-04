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


LABEL = "com.jules.cmux-personal-updater"


def write_executable(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the local cmux Personal updater.")
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

    logs = pathlib.Path("~/Library/Logs/cmux-personal").expanduser()
    logs.mkdir(parents=True, exist_ok=True)
    launch_agents = pathlib.Path("~/Library/LaunchAgents").expanduser()
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LABEL}.plist"
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "personal.local_updater",
            "--config",
            str(destination / "config.json"),
            "--scheduled",
        ],
        "WorkingDirectory": str(destination),
        "EnvironmentVariables": {
            "PATH": ":".join(
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
        },
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
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)

    if not args.no_load:
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["/bin/launchctl", "bootout", f"{domain}/{LABEL}"],
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
            raise ControlError(f"could not load {LABEL}: {result.stderr.strip()}")

    print(f"installed updater: {destination}")
    print(f"installed CLI: {local_bin / 'cmux-personal'}")
    print(f"installed LaunchAgent: {plist_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, KeyError, OSError) as exc:
        raise SystemExit(f"local updater setup stopped: {exc}") from exc
