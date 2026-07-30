from __future__ import annotations

import argparse
import json
import pathlib
import plistlib
import re

from personal.common import ControlError, load_json
from personal.official_runtime_manifest import validate_official_runtime_manifest


SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def patch_bundle_plist(
    plist: dict,
    *,
    app_name: str,
    bundle_identifier: str,
    auth_callback_scheme: str,
    source_sha: str,
    base_tag: str,
    personal_tag: str,
    remote_daemon_manifest: dict,
    upstream_main_sha: str | None = None,
) -> dict:
    plist["CFBundleName"] = app_name
    plist["CFBundleDisplayName"] = app_name
    plist["CFBundleIdentifier"] = bundle_identifier
    plist["CMUXPersonalSourceSHA"] = source_sha
    plist["CMUXPersonalBaseTag"] = base_tag
    plist["CMUXPersonalReleaseTag"] = personal_tag
    if upstream_main_sha:
        if not SOURCE_SHA_RE.fullmatch(upstream_main_sha):
            raise ControlError("upstream main SHA is not a full lowercase commit SHA")
        plist["CMUXPersonalUpstreamMainSHA"] = upstream_main_sha
    else:
        plist.pop("CMUXPersonalUpstreamMainSHA", None)
    plist["CMUXRemoteDaemonManifestJSON"] = json.dumps(
        remote_daemon_manifest,
        separators=(",", ":"),
        sort_keys=True,
    )
    plist.pop("SUFeedURL", None)
    plist.pop("SUPublicEDKey", None)

    environment = dict(plist.get("LSEnvironment") or {})
    environment["CMUX_BUNDLE_ID"] = bundle_identifier
    environment["CMUX_AUTH_CALLBACK_SCHEME"] = auth_callback_scheme
    plist["LSEnvironment"] = environment

    for entry in plist.get("CFBundleURLTypes", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("CFBundleURLName", ""))
        if name.endswith(".auth"):
            entry["CFBundleURLName"] = f"{bundle_identifier}.auth"
            entry["CFBundleURLSchemes"] = [auth_callback_scheme]
        elif name.endswith(".web"):
            entry["LSHandlerRank"] = "Alternate"
    return plist


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the isolated cmux Personal identity.")
    parser.add_argument("--plist", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--base-tag", required=True)
    parser.add_argument("--personal-tag", required=True)
    parser.add_argument("--upstream-main-sha", default="")
    parser.add_argument("--remote-daemon-manifest", required=True)
    args = parser.parse_args()

    config = load_json(args.config)
    path = pathlib.Path(args.plist)
    if not path.is_file():
        raise ControlError(f"Info.plist not found: {path}")
    with path.open("rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        raise ControlError("Info.plist root is not a dictionary")
    remote_daemon_manifest = validate_official_runtime_manifest(
        load_json(args.remote_daemon_manifest),
        repository=str(config["upstream_repository"]),
        base_tag=args.base_tag,
        channel="nightly" if args.upstream_main_sha else "stable",
    )
    patched = patch_bundle_plist(
        plist,
        app_name=str(config["app_name"]),
        bundle_identifier=str(config["bundle_identifier"]),
        auth_callback_scheme=str(config["auth_callback_scheme"]),
        source_sha=args.source_sha,
        base_tag=args.base_tag,
        personal_tag=args.personal_tag,
        remote_daemon_manifest=remote_daemon_manifest,
        upstream_main_sha=args.upstream_main_sha or None,
    )
    with path.open("wb") as handle:
        plistlib.dump(patched, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlError as exc:
        raise SystemExit(f"bundle packaging blocked: {exc}") from exc
