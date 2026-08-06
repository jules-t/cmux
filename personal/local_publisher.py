from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any

from personal.candidate_manager import publish_candidate
from personal.common import ControlError, load_json, run, utc_now, write_json
from personal.local_candidate import validate_candidate
from personal.local_updater import (
    install_state,
    installation_status,
    validate_install_destination,
)
from personal.local_validation import verify_receipt
from personal.release_publisher import (
    ensure_release_reservation,
    find_release,
    publish_release,
    release_is_draft,
)
from personal.state_manager import (
    claim_publication,
    finalize_publication,
    release_publication_claim,
)


def ensure_gh_token() -> None:
    if os.environ.get("GH_TOKEN", "").strip():
        return
    result = run(["gh", "auth", "token"])
    token = result.stdout.strip()
    if not token:
        raise ControlError("GitHub CLI authentication has no usable token")
    os.environ["GH_TOKEN"] = token


def require_installed_receipt(
    receipt: dict[str, Any],
    *,
    config: dict[str, Any],
    data_root: pathlib.Path,
) -> None:
    destination = validate_install_destination(str(config["install_path"]))
    current = install_state(data_root / "current.json")
    exact_fields = ("personal_tag", "source_sha", "base_tag", "upstream_main_sha")
    if any(
        (current.get(field) or None) != (receipt.get(field) or None)
        for field in exact_fields
    ):
        raise ControlError(
            "the locally validated build is not the exact build recorded as installed"
        )
    verified, _, _, detail = installation_status(
        destination=destination,
        state=current,
        expected_bundle_identifier=str(config["bundle_identifier"]),
        expected_personal_tag=str(receipt["personal_tag"]),
        expected_upstream_repository=str(config["upstream_repository"]),
    )
    if not verified:
        raise ControlError(
            "the locally validated build must be installed before publication: " + detail
        )


def remote_branch_sha(repository: pathlib.Path, branch: str) -> str | None:
    result = run(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=repository,
    )
    lines = [line.split() for line in result.stdout.splitlines() if line.split()]
    if not lines:
        return None
    if len(lines) != 1 or len(lines[0]) != 2:
        raise ControlError(f"GitHub returned an ambiguous {branch} branch")
    return lines[0][0]


def publication_run_id(source_sha: str) -> str:
    """Return a stable numeric claim ID so retries of one receipt are idempotent."""
    return str(int(source_sha, 16))


def mutate_remote_state(
    repository: pathlib.Path,
    *,
    message: str,
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    origin_url = run(["git", "remote", "get-url", "origin"], cwd=repository).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="cmux-personal-state-") as temporary:
        checkout = pathlib.Path(temporary) / "control"
        run(
            [
                "git",
                "-c",
                "credential.helper=!gh auth git-credential",
                "clone",
                "--filter=blob:none",
                "--single-branch",
                "--branch",
                "personal-control",
                origin_url,
                str(checkout),
            ]
        )
        state_path = checkout / "personal" / "state.json"
        state = load_json(state_path)
        mutator(state)
        write_json(state_path, state)
        run(["git", "config", "user.name", "cmux Personal local automation"], cwd=checkout)
        run(
            ["git", "config", "user.email", "local-automation@users.noreply.github.com"],
            cwd=checkout,
        )
        run(["git", "add", "personal/state.json"], cwd=checkout)
        changed = run(["git", "diff", "--cached", "--quiet"], cwd=checkout, check=False)
        if changed.returncode == 0:
            return state
        run(["git", "commit", "-m", message], cwd=checkout)
        run(
            [
                "git",
                "-c",
                "credential.helper=!gh auth git-credential",
                "push",
                "origin",
                "HEAD:personal-control",
            ],
            cwd=checkout,
        )
        return state


def clone_validated_candidate(
    receipt_path: pathlib.Path,
    receipt: dict[str, Any],
    *,
    repository: pathlib.Path,
    destination: pathlib.Path,
) -> pathlib.Path:
    bundle = receipt_path.resolve().parent / str(receipt["candidate_bundle"])
    run(["git", "clone", str(bundle), str(destination)])
    source_sha = str(receipt["source_sha"])
    head = run(["git", "rev-parse", "HEAD"], cwd=destination).stdout.strip()
    if head != source_sha:
        raise ControlError(
            f"validated candidate bundle contains {head}, expected {source_sha}"
        )
    origin_url = run(["git", "remote", "get-url", "origin"], cwd=repository).stdout.strip()
    run(["git", "remote", "set-url", "origin", origin_url], cwd=destination)
    return destination


def promote_personal_stable(
    candidate_repository: pathlib.Path,
    *,
    personal_source_sha: str,
    source_sha: str,
) -> None:
    current = remote_branch_sha(candidate_repository, "personal/stable")
    if current == source_sha:
        return
    if current != personal_source_sha:
        raise ControlError(
            "personal/stable moved after local validation "
            f"(expected {personal_source_sha}, found {current or 'missing'})"
        )
    run(
        [
            "git",
            "-c",
            "credential.helper=!gh auth git-credential",
            "push",
            f"--force-with-lease=refs/heads/personal/stable:{personal_source_sha}",
            "origin",
            f"{source_sha}:refs/heads/personal/stable",
        ],
        cwd=candidate_repository,
    )
    observed = remote_branch_sha(candidate_repository, "personal/stable")
    if observed != source_sha:
        raise ControlError(
            f"personal/stable promotion resolved to {observed}, expected {source_sha}"
        )


def publish_validated_receipt(
    receipt_path: pathlib.Path,
    *,
    config_path: pathlib.Path,
    repository: pathlib.Path,
    data_root: pathlib.Path,
) -> dict[str, str]:
    receipt_path = receipt_path.resolve()
    receipt = verify_receipt(
        argparse.Namespace(receipt=str(receipt_path), config=str(config_path))
    )
    candidate = validate_candidate(receipt.get("candidate"))
    config = load_json(config_path)
    require_installed_receipt(receipt, config=config, data_root=data_root)
    ensure_gh_token()

    source_sha = str(receipt["source_sha"])
    personal_source_sha = str(candidate["personal_source_sha"])
    personal_tag = str(receipt["personal_tag"])
    base_tag = str(receipt["base_tag"])
    upstream_main_sha = (
        str(receipt["upstream_main_sha"])
        if receipt.get("upstream_main_sha")
        else None
    )
    run_id = publication_run_id(source_sha)
    fork_repository = str(config["fork_repository"])
    assets = receipt_path.parent / str(receipt["assets_directory"])

    with tempfile.TemporaryDirectory(prefix="cmux-personal-publish-") as temporary:
        candidate_repository = clone_validated_candidate(
            receipt_path,
            receipt,
            repository=repository,
            destination=pathlib.Path(temporary) / "candidate",
        )
        current_stable = remote_branch_sha(candidate_repository, "personal/stable")
        if current_stable not in {personal_source_sha, source_sha}:
            raise ControlError(
                "personal/stable moved after local validation "
                f"(expected {personal_source_sha}, found {current_stable or 'missing'})"
            )
        publish_candidate(
            candidate_repository,
            remote_url=run(
                ["git", "remote", "get-url", "origin"], cwd=candidate_repository
            ).stdout.strip(),
            candidate_branch=str(candidate["candidate_branch"]),
            expected_commit=source_sha,
            github_auth=True,
        )

        def claim(state: dict[str, Any]) -> None:
            claim_publication(
                state,
                base_tag=base_tag,
                personal_tag=personal_tag,
                source_sha=source_sha,
                run_id=run_id,
                updated_at=utc_now(),
            )

        mutate_remote_state(
            repository,
            message=f"chore(personal): claim local publication {personal_tag}",
            mutator=claim,
        )
        try:
            ensure_release_reservation(
                repository=fork_repository,
                personal_tag=personal_tag,
                source_sha=source_sha,
                base_tag=base_tag,
            )
            publish_release(
                repository=fork_repository,
                directory=assets,
                config=config,
                source_sha=source_sha,
                base_tag=base_tag,
                personal_tag=personal_tag,
                upstream_main_sha=upstream_main_sha,
            )
        except Exception:
            try:
                release = find_release(fork_repository, personal_tag)
                nothing_exposed = release is None or release_is_draft(release)
            except Exception:
                nothing_exposed = False
            if nothing_exposed:
                def release_claim(state: dict[str, Any]) -> None:
                    release_publication_claim(
                        state,
                        base_tag=base_tag,
                        personal_tag=personal_tag,
                        source_sha=source_sha,
                        run_id=run_id,
                    )

                mutate_remote_state(
                    repository,
                    message=f"chore(personal): release failed local publication {personal_tag}",
                    mutator=release_claim,
                )
            raise

        promote_personal_stable(
            candidate_repository,
            personal_source_sha=personal_source_sha,
            source_sha=source_sha,
        )

    finalized_state: dict[str, Any] = {}

    def finalize(state: dict[str, Any]) -> None:
        nonlocal finalized_state
        finalize_publication(
            state,
            base_tag=base_tag,
            personal_tag=personal_tag,
            source_sha=source_sha,
            personal_stable_sha=source_sha,
            run_id=run_id,
            updated_at=utc_now(),
            upstream_main_sha=upstream_main_sha,
        )
        finalized_state = state

    mutate_remote_state(
        repository,
        message=f"chore(personal): record local publication {personal_tag}",
        mutator=finalize,
    )
    write_json(data_root / "monitor-state.json", finalized_state)
    return {
        "status": "published",
        "personal_tag": personal_tag,
        "source_sha": source_sha,
        "candidate_channel": str(candidate["channel"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish an installed, locally validated cmux Personal build."
    )
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument(
        "--data-root", default="~/.local/share/cmux-personal", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    result = publish_validated_receipt(
        pathlib.Path(args.receipt),
        config_path=pathlib.Path(args.config),
        repository=pathlib.Path(args.repository).resolve(),
        data_root=pathlib.Path(args.data_root).expanduser(),
    )
    print(json.dumps(result, indent=2))
    print("The exact locally installed archive is now the published release.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, KeyError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"local publication blocked: {exc}") from exc
