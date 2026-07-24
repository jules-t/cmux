from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from personal.common import (
    ControlError,
    append_github_output,
    append_step_summary,
    load_json,
    require_release_tag,
    utc_now,
    version_tuple,
    write_json,
)


SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"


@dataclasses.dataclass(frozen=True)
class PublishedRelease:
    tag: str
    build: str | None
    download_url: str | None


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/xml;q=0.9, */*;q=0.8",
            "User-Agent": "cmux-personal-release-monitor/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ControlError(f"could not fetch {url}: {exc}") from exc


def parse_appcast(payload: bytes) -> PublishedRelease:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ControlError(f"invalid appcast XML: {exc}") from exc
    item = root.find("./channel/item")
    if item is None:
        raise ControlError("appcast does not contain channel/item")
    enclosure = item.find("enclosure")
    if enclosure is None:
        raise ControlError("appcast item does not contain an enclosure")
    version = enclosure.attrib.get(f"{{{SPARKLE_NAMESPACE}}}shortVersionString")
    if not version:
        version = item.findtext(f"{{{SPARKLE_NAMESPACE}}}shortVersionString")
    build = enclosure.attrib.get(f"{{{SPARKLE_NAMESPACE}}}version")
    if not build:
        build = item.findtext(f"{{{SPARKLE_NAMESPACE}}}version")
    if not version:
        raise ControlError("appcast enclosure has no sparkle:shortVersionString")
    tag = require_release_tag(version if version.startswith("v") else f"v{version}")
    return PublishedRelease(tag=tag, build=build, download_url=enclosure.attrib.get("url"))


def parse_github_release(payload: bytes) -> PublishedRelease:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ControlError(f"invalid GitHub release JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlError("GitHub release response is not an object")
    if value.get("draft") or value.get("prerelease"):
        raise ControlError("GitHub release is a draft or prerelease")
    tag = require_release_tag(str(value.get("tag_name", "")))
    return PublishedRelease(tag=tag, build=None, download_url=value.get("html_url"))


def github_latest_url(repository: str) -> str:
    return f"https://api.github.com/repos/{repository}/releases/latest"


def github_tag_url(repository: str, tag: str) -> str:
    return f"https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}"


def observe(
    state: dict[str, Any],
    *,
    appcast: PublishedRelease,
    github: PublishedRelease,
    observations_required: int,
    checked_at: str,
    retry_attempt: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = require_release_tag(str(state["current_stable_tag"]))
    pending = state.setdefault(
        "pending_observation",
        {"tag": None, "count": 0, "first_seen_at": None, "last_seen_at": None},
    )
    attempt = state.setdefault(
        "attempt",
        {"tag": None, "status": None, "workflow_run_id": None, "updated_at": None},
    )

    result: dict[str, Any] = {
        "status": "blocked",
        "ready": False,
        "target_tag": None,
        "current_tag": current,
        "observation_count": 0,
        "detail": "",
    }

    if appcast.tag != github.tag:
        result["status"] = "source_mismatch"
        result["detail"] = f"appcast reports {appcast.tag}, but GitHub latest reports {github.tag}"
    elif version_tuple(appcast.tag) < version_tuple(current):
        result["status"] = "rollback_detected"
        result["detail"] = f"published tag {appcast.tag} is older than installed base {current}"
    elif appcast.tag == current:
        pending.update({"tag": None, "count": 0, "first_seen_at": None, "last_seen_at": None})
        result.update(status="current", detail=f"{current} is still the latest stable release")
    else:
        already_attempted = (
            attempt.get("tag") == appcast.tag
            and not retry_attempt
            and pending.get("tag") == appcast.tag
            and int(pending.get("count", 0)) >= observations_required
        )
        if already_attempted:
            count = int(pending.get("count", 0))
            result["target_tag"] = appcast.tag
            result["observation_count"] = count
            result["status"] = "already_attempted"
            result["detail"] = (
                f"{appcast.tag} was already queued with status "
                f"{attempt.get('status') or 'unknown'}; automatic retries are suppressed"
            )
        elif pending.get("tag") == appcast.tag:
            count = int(pending.get("count", 0)) + 1
            first_seen_at = pending.get("first_seen_at") or checked_at
        else:
            count = 1
            first_seen_at = checked_at
        if not already_attempted:
            pending.update(
                {
                    "tag": appcast.tag,
                    "count": count,
                    "first_seen_at": first_seen_at,
                    "last_seen_at": checked_at,
                }
            )
            result["target_tag"] = appcast.tag
            result["observation_count"] = count
            if count < observations_required:
                result["status"] = "observing"
                result["detail"] = (
                    f"first stable observation for {appcast.tag}; "
                    f"{observations_required - count} more check(s) required"
                )
            else:
                attempt.update(
                    {
                        "tag": appcast.tag,
                        "status": "queued",
                        "workflow_run_id": None,
                        "updated_at": checked_at,
                    }
                )
                result.update(
                    status="ready",
                    ready=True,
                    detail=f"{appcast.tag} was confirmed by {count} consecutive observations",
                )

    next_last_check = {
        "status": result["status"],
        "appcast_tag": appcast.tag,
        "github_tag": github.tag,
        "checked_at": checked_at,
        "detail": result["detail"],
    }
    previous_last_check = state.get("last_check")
    comparable_keys = ("status", "appcast_tag", "github_tag", "detail")
    if not isinstance(previous_last_check, dict) or any(
        previous_last_check.get(key) != next_last_check.get(key) for key in comparable_keys
    ):
        state["last_check"] = next_last_check
    return state, result


def read_payload(path: str | None, url: str) -> bytes:
    if path:
        return pathlib.Path(path).read_bytes()
    return _request_bytes(url)


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe the official cmux stable release.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--appcast-file")
    parser.add_argument("--github-json-file")
    parser.add_argument("--target-tag", help="Manually retry a specific published stable tag.")
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    state = load_json(args.state)
    repository = str(config["upstream_repository"])
    feed_url = str(config["release_feed_url"])

    if args.target_tag:
        target = require_release_tag(args.target_tag)
        github_url = github_tag_url(repository, target)
    else:
        github_url = github_latest_url(repository)

    appcast = parse_appcast(read_payload(args.appcast_file, feed_url))
    github = parse_github_release(read_payload(args.github_json_file, github_url))
    if args.target_tag and github.tag != target:
        raise ControlError(f"requested {target}, but GitHub returned {github.tag}")
    if args.target_tag:
        appcast = PublishedRelease(
            tag=target,
            build=appcast.build if appcast.tag == target else None,
            download_url=appcast.download_url if appcast.tag == target else github.download_url,
        )

    checked_at = utc_now()
    state, result = observe(
        state,
        appcast=appcast,
        github=github,
        observations_required=int(config.get("observations_required", 2)),
        checked_at=checked_at,
        retry_attempt=args.retry or bool(args.target_tag),
    )
    write_json(args.state, state)
    write_json(args.output, result)
    append_github_output(result)
    append_step_summary(
        "\n".join(
            [
                "### cmux stable release observation",
                "",
                f"- status: `{result['status']}`",
                f"- current base: `{result['current_tag']}`",
                f"- observed: `{appcast.tag}`",
                f"- detail: {result['detail']}",
            ]
        )
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"release monitor blocked: {exc}") from exc
