from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any

from personal.common import (
    PERSONAL_TAG_RE,
    ControlError,
    load_json,
    require_release_tag,
    run,
)
from personal.verify_release_assets import verify_assets


REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
GH_TIMEOUT_SECONDS = 300


def gh(*arguments: str) -> subprocess.CompletedProcess[str]:
    return run(["gh", *arguments], timeout=GH_TIMEOUT_SECONDS)


def require_token() -> None:
    if not os.environ.get("GH_TOKEN", "").strip():
        raise ControlError("GH_TOKEN is required for release publication")


def require_repository(repository: str) -> str:
    if not REPOSITORY_RE.fullmatch(repository):
        raise ControlError(f"invalid GitHub repository: {repository!r}")
    return repository


def require_source_sha(source_sha: str) -> str:
    if not SHA_RE.fullmatch(source_sha):
        raise ControlError(f"source SHA must be a full 40-character commit SHA: {source_sha!r}")
    return source_sha.lower()


def require_personal_tag(personal_tag: str, base_tag: str) -> str:
    if not PERSONAL_TAG_RE.fullmatch(personal_tag):
        raise ControlError(f"invalid personal release tag: {personal_tag!r}")
    if not personal_tag.startswith(f"personal-{base_tag}-r"):
        raise ControlError(
            f"personal release tag {personal_tag!r} does not belong to base tag {base_tag!r}"
        )
    return personal_tag


def parse_json_output(
    result: subprocess.CompletedProcess[str],
    *,
    context: str,
) -> Any:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ControlError(f"{context} returned invalid JSON: {exc}") from exc


def flatten_pages(value: Any, *, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ControlError(f"{context} did not return a JSON list")
    if all(isinstance(item, dict) for item in value):
        return value
    if not all(isinstance(page, list) for page in value):
        raise ControlError(f"{context} returned malformed paginated JSON")
    flattened: list[dict[str, Any]] = []
    for page in value:
        for item in page:
            if not isinstance(item, dict):
                raise ControlError(f"{context} returned a non-object entry")
            flattened.append(item)
    return flattened


def paginated_api(path: str, *, context: str) -> list[dict[str, Any]]:
    result = gh("api", "--paginate", "--slurp", path)
    return flatten_pages(parse_json_output(result, context=context), context=context)


def list_releases(repository: str) -> list[dict[str, Any]]:
    return paginated_api(
        f"repos/{repository}/releases?per_page=100",
        context="GitHub release listing",
    )


def find_release(repository: str, personal_tag: str) -> dict[str, Any] | None:
    matches = [
        release
        for release in list_releases(repository)
        if release.get("tag_name") == personal_tag
    ]
    if len(matches) > 1:
        raise ControlError(f"GitHub returned duplicate releases for {personal_tag}")
    return matches[0] if matches else None


def release_id(release: Mapping[str, Any]) -> int:
    value = release.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlError("GitHub release has no valid numeric ID")
    return value


def release_is_draft(release: Mapping[str, Any]) -> bool:
    value = release.get("draft")
    if not isinstance(value, bool):
        raise ControlError("GitHub release has no valid draft state")
    return value


def list_release_assets(repository: str, release: Mapping[str, Any]) -> list[dict[str, Any]]:
    return paginated_api(
        f"repos/{repository}/releases/{release_id(release)}/assets?per_page=100",
        context="GitHub release asset listing",
    )


def object_reference(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ControlError(f"{context} has no object")
    object_type = value.get("type")
    sha = value.get("sha")
    if object_type not in {"commit", "tag"}:
        raise ControlError(f"{context} points to unsupported object type {object_type!r}")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise ControlError(f"{context} has an invalid object SHA")
    return object_type, sha.lower()


def resolve_tag_commit(repository: str, personal_tag: str) -> str:
    encoded_tag = urllib.parse.quote(personal_tag, safe="")
    reference = parse_json_output(
        gh("api", f"repos/{repository}/git/ref/tags/{encoded_tag}"),
        context=f"GitHub tag {personal_tag}",
    )
    if not isinstance(reference, dict):
        raise ControlError(f"GitHub tag {personal_tag} is not a JSON object")
    object_type, sha = object_reference(
        reference.get("object"),
        context=f"GitHub tag {personal_tag}",
    )
    visited: set[str] = set()
    while object_type == "tag":
        if sha in visited:
            raise ControlError(f"annotated tag {personal_tag} contains an object cycle")
        visited.add(sha)
        tag_object = parse_json_output(
            gh("api", f"repos/{repository}/git/tags/{sha}"),
            context=f"GitHub annotated tag object {sha}",
        )
        if not isinstance(tag_object, dict):
            raise ControlError(f"GitHub annotated tag object {sha} is not a JSON object")
        object_type, sha = object_reference(
            tag_object.get("object"),
            context=f"GitHub annotated tag object {sha}",
        )
    return sha


def require_release_source(
    repository: str,
    personal_tag: str,
    source_sha: str,
) -> None:
    actual_sha = resolve_tag_commit(repository, personal_tag)
    if actual_sha != source_sha:
        raise ControlError(
            f"release tag {personal_tag} points to {actual_sha}, expected {source_sha}"
        )


def release_title(base_tag: str, personal_tag: str) -> str:
    return f"cmux Personal {base_tag} ({personal_tag})"


def release_notes(
    base_tag: str,
    source_sha: str,
    *,
    run_url: str | None = None,
) -> str:
    lines = [
        "Automated cmux Personal build.",
        "",
        f"- Base cmux release: `{base_tag}`",
        f"- Source commit: `{source_sha}`",
    ]
    if run_url:
        lines.append(f"- Build workflow: {run_url}")
    lines.extend(
        [
            "",
            "The release becomes public only after its assets and provenance pass verification.",
        ]
    )
    return "\n".join(lines)


def reserve_release(
    *,
    repository: str,
    personal_tag: str,
    source_sha: str,
    base_tag: str,
    run_url: str | None = None,
) -> dict[str, Any]:
    require_token()
    repository = require_repository(repository)
    base_tag = require_release_tag(base_tag)
    source_sha = require_source_sha(source_sha)
    personal_tag = require_personal_tag(personal_tag, base_tag)
    title = release_title(base_tag, personal_tag)
    notes = release_notes(base_tag, source_sha, run_url=run_url)

    release = find_release(repository, personal_tag)
    if release is None:
        gh(
            "release",
            "create",
            personal_tag,
            "--repo",
            repository,
            "--target",
            source_sha,
            "--title",
            title,
            "--notes",
            notes,
            "--draft",
        )
        release = find_release(repository, personal_tag)
        if release is None:
            raise ControlError(f"draft release {personal_tag} was not visible after creation")
        if not release_is_draft(release):
            raise ControlError(f"new release {personal_tag} is unexpectedly public")
        if list_release_assets(repository, release):
            raise ControlError(f"new draft release {personal_tag} is not empty")
        require_release_source(repository, personal_tag, source_sha)
        return release

    if not release_is_draft(release):
        raise ControlError(f"release {personal_tag} is already published")
    require_release_source(repository, personal_tag, source_sha)
    if list_release_assets(repository, release):
        raise ControlError(
            f"existing draft release {personal_tag} contains assets; "
            "resume its publication instead of rebuilding"
        )
    gh(
        "release",
        "edit",
        personal_tag,
        "--repo",
        repository,
        "--title",
        title,
        "--notes",
        notes,
        "--draft",
    )
    return release


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configured_asset_paths(
    directory: pathlib.Path,
    config: Mapping[str, Any],
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    archive_name = config.get("artifact_name")
    manifest_name = config.get("manifest_name")
    if not isinstance(archive_name, str) or not isinstance(manifest_name, str):
        raise ControlError("release config has invalid asset names")
    names = (archive_name, manifest_name, f"{archive_name}.sha256")
    if len(set(names)) != 3:
        raise ControlError("release config does not define three distinct assets")
    for name in names:
        if not name or pathlib.PurePath(name).name != name or name in {".", ".."}:
            raise ControlError(f"release config contains an unsafe asset name: {name!r}")
    paths = tuple(directory / name for name in names)
    for path in paths:
        if not path.is_file():
            raise ControlError(f"release asset is missing: {path.name}")
    return paths  # type: ignore[return-value]


def expected_asset_metadata(paths: Iterable[pathlib.Path]) -> dict[str, tuple[int, str]]:
    return {
        path.name: (path.stat().st_size, sha256(path))
        for path in paths
    }


def verify_remote_assets(
    repository: str,
    release: Mapping[str, Any],
    expected: Mapping[str, tuple[int, str]],
) -> None:
    remote_assets = list_release_assets(repository, release)
    by_name: dict[str, dict[str, Any]] = {}
    for asset in remote_assets:
        name = asset.get("name")
        if not isinstance(name, str):
            raise ControlError("GitHub release contains an asset with no valid name")
        if name in by_name:
            raise ControlError(f"GitHub release contains duplicate asset {name!r}")
        by_name[name] = asset
    if set(by_name) != set(expected):
        missing = sorted(set(expected) - set(by_name))
        unexpected = sorted(set(by_name) - set(expected))
        raise ControlError(
            f"remote release asset set differs (missing={missing}, unexpected={unexpected})"
        )
    for name, (expected_size, expected_digest) in expected.items():
        asset = by_name[name]
        size = asset.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size != expected_size:
            raise ControlError(
                f"remote release asset {name!r} has size {size!r}, expected {expected_size}"
            )
        digest = asset.get("digest")
        expected_api_digest = f"sha256:{expected_digest}"
        if not isinstance(digest, str) or digest.lower() != expected_api_digest:
            raise ControlError(
                f"remote release asset {name!r} has digest {digest!r}, "
                f"expected {expected_api_digest!r}"
            )


def publish_release(
    *,
    repository: str,
    directory: pathlib.Path,
    config: Mapping[str, Any],
    source_sha: str,
    base_tag: str,
    personal_tag: str,
) -> dict[str, Any]:
    require_token()
    repository = require_repository(repository)
    base_tag = require_release_tag(base_tag)
    source_sha = require_source_sha(source_sha)
    personal_tag = require_personal_tag(personal_tag, base_tag)
    paths = configured_asset_paths(directory, config)
    verify_assets(
        directory,
        config=dict(config),
        source_sha=source_sha,
        base_tag=base_tag,
        personal_tag=personal_tag,
    )
    expected = expected_asset_metadata(paths)

    release = find_release(repository, personal_tag)
    if release is None:
        raise ControlError(
            f"reserved draft release {personal_tag} does not exist; run reserve first"
        )
    require_release_source(repository, personal_tag, source_sha)

    if not release_is_draft(release):
        verify_remote_assets(repository, release, expected)
        return release

    gh(
        "release",
        "upload",
        personal_tag,
        *(str(path) for path in paths),
        "--repo",
        repository,
        "--clobber",
    )
    release = find_release(repository, personal_tag)
    if release is None:
        raise ControlError(f"draft release {personal_tag} disappeared after asset upload")
    require_release_source(repository, personal_tag, source_sha)
    verify_remote_assets(repository, release, expected)

    if release_is_draft(release):
        gh(
            "release",
            "edit",
            personal_tag,
            "--repo",
            repository,
            "--draft=false",
        )

    release = find_release(repository, personal_tag)
    if release is None:
        raise ControlError(f"release {personal_tag} disappeared after publication")
    if release_is_draft(release):
        raise ControlError(f"release {personal_tag} remained a draft after publication")
    require_release_source(repository, personal_tag, source_sha)
    verify_remote_assets(repository, release, expected)
    return release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reserve and publish verified cmux Personal GitHub releases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    reserve = subparsers.add_parser("reserve", help="reserve an empty draft release")
    reserve.add_argument("--repo", required=True)
    reserve.add_argument("--personal-tag", required=True)
    reserve.add_argument("--source-sha", required=True)
    reserve.add_argument("--base-tag", required=True)
    reserve.add_argument("--run-url")

    publish = subparsers.add_parser("publish", help="publish verified release assets")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--directory", required=True)
    publish.add_argument("--config", required=True)
    publish.add_argument("--source-sha", required=True)
    publish.add_argument("--base-tag", required=True)
    publish.add_argument("--personal-tag", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "reserve":
        reserve_release(
            repository=args.repo,
            personal_tag=args.personal_tag,
            source_sha=args.source_sha,
            base_tag=args.base_tag,
            run_url=args.run_url,
        )
        print(f"release reservation: {args.personal_tag} is an exact draft")
        return 0
    publish_release(
        repository=args.repo,
        directory=pathlib.Path(args.directory),
        config=load_json(args.config),
        source_sha=args.source_sha,
        base_tag=args.base_tag,
        personal_tag=args.personal_tag,
    )
    print(f"release publication: {args.personal_tag} is public and verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, OSError, KeyError) as exc:
        raise SystemExit(f"release publication blocked: {exc}") from exc
