from __future__ import annotations

import argparse
import json
import pathlib

from personal.common import (
    ControlError,
    PERSONAL_TAG_RE,
    append_github_output,
    load_json,
    require_release_tag,
    run,
    write_json,
)
from personal.release_publisher import list_releases, require_repository


def next_personal_tag(
    existing_tags: list[str],
    base_tag: str,
    *,
    existing_release_tags: list[str] | None = None,
    existing_local_tags: list[str] | None = None,
) -> tuple[str, int]:
    require_release_tag(base_tag)
    prefix = f"personal-{base_tag}-r"
    revisions = []
    for tag in [
        *existing_tags,
        *(existing_release_tags or []),
        *(existing_local_tags or []),
    ]:
        match = PERSONAL_TAG_RE.fullmatch(tag)
        if match and tag.startswith(prefix):
            revisions.append(int(match.group(4)))
    revision = max(revisions, default=0) + 1
    return f"{prefix}{revision}", revision


def local_personal_tags(build_runs_root: pathlib.Path) -> list[str]:
    if not build_runs_root.exists():
        return []
    if not build_runs_root.is_dir():
        raise ControlError(f"local build runs root is not a directory: {build_runs_root}")

    tags: list[str] = []
    for record in sorted(build_runs_root.glob("*/metadata/release-name.json")):
        value = load_json(record)
        tag = value.get("personal_tag")
        if not isinstance(tag, str) or not PERSONAL_TAG_RE.fullmatch(tag):
            raise ControlError(f"invalid personal tag in local build record: {record}")
        tags.append(tag)
    return tags


def main() -> int:
    parser = argparse.ArgumentParser(description="Allocate an immutable personal release tag.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base-tag", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--local-build-root")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    base_tag = require_release_tag(args.base_tag)
    result = run(
        [
            "git",
            "ls-remote",
            "--tags",
            args.remote,
            f"refs/tags/personal-{base_tag}-r*",
        ],
        cwd=args.repo,
    )
    tags: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        reference = line.split("\t", 1)[-1]
        if reference.endswith("^{}"):
            continue
        tags.append(reference.removeprefix("refs/tags/"))
    repository = require_repository(args.repository)
    release_tags = [
        tag
        for release in list_releases(repository)
        if isinstance((tag := release.get("tag_name")), str)
    ]
    local_tags = (
        local_personal_tags(pathlib.Path(args.local_build_root))
        if args.local_build_root
        else []
    )
    tag, revision = next_personal_tag(
        tags,
        base_tag,
        existing_release_tags=release_tags,
        existing_local_tags=local_tags,
    )
    value = {"personal_tag": tag, "revision": revision, "base_tag": base_tag}
    write_json(args.output, value)
    append_github_output(value)
    print(json.dumps(value, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"release naming blocked: {exc}") from exc
