from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any, Iterable


TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
PERSONAL_TAG_RE = re.compile(r"^personal-v(\d+)\.(\d+)\.(\d+)-r([1-9]\d*)$")


class ControlError(RuntimeError):
    """A policy or control-plane validation failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: os.PathLike[str] | str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ControlError(f"expected a JSON object in {path}")
    return value


def write_json(path: os.PathLike[str] | str, value: dict[str, Any]) -> None:
    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_release_tag(value: str) -> str:
    if not TAG_RE.fullmatch(value):
        raise ControlError(f"invalid stable release tag: {value!r}")
    return value


def version_tuple(tag: str) -> tuple[int, int, int]:
    match = TAG_RE.fullmatch(tag)
    if not match:
        raise ControlError(f"cannot compare non-release tag: {tag!r}")
    return tuple(int(part) for part in match.groups()[:3])  # type: ignore[return-value]


def run(
    command: Iterable[str],
    *,
    cwd: os.PathLike[str] | str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if check and result.returncode != 0:
        rendered = " ".join(command)
        detail = result.stderr.strip() or result.stdout.strip()
        raise ControlError(f"command failed ({result.returncode}): {rendered}\n{detail}")
    return result


def append_github_output(values: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif value is None:
                rendered = ""
            else:
                rendered = str(value)
            handle.write(f"{key}={rendered}\n")


def append_step_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(text.rstrip())
        handle.write("\n")
