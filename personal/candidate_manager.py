from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
from typing import Any

from personal.common import (
    ControlError,
    append_github_output,
    append_step_summary,
    load_json,
    run,
    utc_now,
    write_json,
)
from personal.conflict_policy import classify_conflicts, path_matches


BRANCH_RE = re.compile(r"^(?:candidate|canary)/[A-Za-z0-9._/-]+$")
AUTOMATION_COMMITTER_NAME = "cmux Personal automation"
AUTOMATION_COMMITTER_EMAIL = "actions@users.noreply.github.com"


def git(repo: pathlib.Path, *arguments: str, check: bool = True):
    return run(["git", *arguments], cwd=repo, check=check)


def git_output(repo: pathlib.Path, *arguments: str) -> str:
    return git(repo, *arguments).stdout.strip()


def resolve_commit(repo: pathlib.Path, reference: str) -> str:
    return git_output(repo, "rev-parse", "--verify", f"{reference}^{{commit}}")


def list_lines(value: str) -> list[str]:
    return [line for line in value.splitlines() if line]


def make_baseline(
    repo: pathlib.Path,
    *,
    source_ref: str,
    current_base_ref: str,
    target_ref: str,
    candidate_branch: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not BRANCH_RE.fullmatch(candidate_branch):
        raise ControlError(f"unsafe candidate branch name: {candidate_branch!r}")
    source_commit = resolve_commit(repo, source_ref)
    current_base_commit = resolve_commit(repo, current_base_ref)
    target_commit = resolve_commit(repo, target_ref)
    ancestor = git(repo, "merge-base", "--is-ancestor", current_base_commit, source_commit, check=False)
    if ancestor.returncode != 0:
        raise ControlError(
            f"{current_base_ref} is not an ancestor of {source_ref}; refusing an ambiguous rebase"
        )
    target_ancestor = git(
        repo,
        "merge-base",
        "--is-ancestor",
        current_base_commit,
        target_commit,
        check=False,
    )
    if target_ancestor.returncode != 0:
        raise ControlError(
            f"{target_ref} does not descend from {current_base_ref}; "
            "refusing to replay personal commits across rewritten history"
        )
    commit_lines = list_lines(
        git_output(
            repo,
            "log",
            "--reverse",
            "--format=%H%x09%s",
            f"{current_base_commit}..{source_commit}",
        )
    )
    if not commit_lines:
        raise ControlError("personal source branch contains no commits above its stable base")
    commits = []
    for line in commit_lines:
        commit_hash, subject = line.split("\t", 1)
        commits.append({"hash": commit_hash, "subject": subject})
    changed_files = sorted(
        set(list_lines(git_output(repo, "diff", "--name-only", current_base_commit, source_commit)))
    )
    test_files = [
        path
        for path in changed_files
        if path_matches(path, policy.get("test_patterns", []))
        and git(repo, "cat-file", "-e", f"{source_commit}:{path}", check=False).returncode == 0
    ]
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "source_ref": source_ref,
        "source_commit": source_commit,
        "current_base_ref": current_base_ref,
        "current_base_commit": current_base_commit,
        "target_ref": target_ref,
        "target_commit": target_commit,
        "candidate_branch": candidate_branch,
        "personal_commits": commits,
        "personal_changed_files": changed_files,
        "personal_test_files": test_files,
    }


def count_conflict_markers(repo: pathlib.Path, paths: list[str]) -> int:
    count = 0
    for relative in paths:
        path = repo / relative
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        count += sum(
            1
            for line in lines
            if line.startswith("<<<<<<< ")
            or line == "======="
            or line.startswith(">>>>>>> ")
        )
    return count


def rebase_candidate(
    repo: pathlib.Path,
    baseline: dict[str, Any],
    policy: dict[str, Any],
    *,
    leave_conflicts: bool,
) -> dict[str, Any]:
    git(repo, "rebase", "--abort", check=False)
    git(repo, "switch", "--detach", baseline["source_commit"])
    git(repo, "switch", "--force-create", baseline["candidate_branch"])
    git(repo, "config", "--local", "user.name", AUTOMATION_COMMITTER_NAME)
    git(repo, "config", "--local", "user.email", AUTOMATION_COMMITTER_EMAIL)
    environment = os.environ.copy()
    environment.update({"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true"})
    result = run(
        [
            "git",
            "rebase",
            "--rebase-merges",
            "--onto",
            baseline["target_commit"],
            baseline["current_base_commit"],
        ],
        cwd=repo,
        check=False,
        env=environment,
    )
    if result.returncode == 0:
        return {
            "status": "clean",
            "eligible_for_agent": False,
            "candidate_commit": resolve_commit(repo, "HEAD"),
            "conflicted_paths": [],
            "detail": "personal commits rebased without conflicts",
        }

    paths = sorted(
        set(list_lines(git_output(repo, "diff", "--name-only", "--diff-filter=U")))
    )
    detail = result.stderr.strip() or result.stdout.strip()
    if not paths:
        git(repo, "rebase", "--abort", check=False)
        raise ControlError(
            "candidate rebase failed without file conflicts"
            + (f": {detail}" if detail else "")
        )
    classification = classify_conflicts(
        paths,
        marker_count=count_conflict_markers(repo, paths),
        policy=policy,
    )
    classification.update(
        {
            "status": "conflict",
            "candidate_commit": None,
            "detail": detail,
        }
    )
    if not leave_conflicts:
        git(repo, "rebase", "--abort", check=False)
    return classification


def rebase_in_progress(repo: pathlib.Path) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        path = pathlib.Path(git_output(repo, "rev-parse", "--git-path", name))
        if not path.is_absolute():
            path = repo / path
        if path.exists():
            return True
    return False


def verify_candidate(
    repo: pathlib.Path,
    *,
    baseline: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if rebase_in_progress(repo):
        reasons.append("a rebase is still in progress")
    unmerged = list_lines(git_output(repo, "diff", "--name-only", "--diff-filter=U"))
    if unmerged:
        reasons.append("unmerged paths remain: " + ", ".join(unmerged))
    head = resolve_commit(repo, "HEAD")
    target = baseline["target_commit"]
    if git(repo, "merge-base", "--is-ancestor", target, head, check=False).returncode != 0:
        reasons.append("target release is not an ancestor of the candidate")

    expected_subjects = [entry["subject"] for entry in baseline["personal_commits"]]
    actual_subjects = list_lines(
        git_output(repo, "log", "--reverse", "--format=%s", f"{target}..{head}")
    )
    if actual_subjects != expected_subjects:
        reasons.append(
            "personal commit sequence changed "
            f"(expected {expected_subjects!r}, found {actual_subjects!r})"
        )

    missing_tests = [
        path
        for path in baseline.get("personal_test_files", [])
        if git(repo, "cat-file", "-e", f"{head}:{path}", check=False).returncode != 0
    ]
    if missing_tests:
        reasons.append("personal test files disappeared: " + ", ".join(missing_tests))

    candidate_files = sorted(
        set(list_lines(git_output(repo, "diff", "--name-only", target, head)))
    )
    original_files = set(baseline.get("personal_changed_files", []))
    new_protected = [
        path
        for path in candidate_files
        if path not in original_files
        and path_matches(path, policy.get("protected_patterns", []))
    ]
    if new_protected:
        reasons.append(
            "resolution introduced changes to protected base paths: " + ", ".join(new_protected)
        )

    if git(repo, "diff", "--check", target, head, check=False).returncode != 0:
        reasons.append("candidate fails git diff --check")

    result = {
        "verified": not reasons,
        "candidate_commit": head,
        "target_commit": target,
        "personal_commit_count": len(actual_subjects),
        "candidate_changed_files": candidate_files,
        "reasons": reasons,
    }
    if reasons:
        raise ControlError("candidate verification failed: " + "; ".join(reasons))
    return result


def create_bundle(repo: pathlib.Path, output: pathlib.Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    git(repo, "bundle", "create", str(output), "HEAD")
    git(repo, "bundle", "verify", str(output))


def prepare_command(args: argparse.Namespace) -> int:
    repo = pathlib.Path(args.repo).resolve()
    policy = load_json(args.policy)
    baseline = make_baseline(
        repo,
        source_ref=args.source_ref,
        current_base_ref=args.current_base_ref,
        target_ref=args.target_ref,
        candidate_branch=args.candidate_branch,
        policy=policy,
    )
    write_json(args.baseline, baseline)
    result = rebase_candidate(
        repo,
        baseline,
        policy,
        leave_conflicts=args.leave_conflicts,
    )
    if result["status"] == "clean":
        result["verification"] = verify_candidate(repo, baseline=baseline, policy=policy)
        if args.bundle:
            create_bundle(repo, pathlib.Path(args.bundle).resolve())
    write_json(args.output, result)
    append_github_output(
        {
            "status": result["status"],
            "eligible_for_agent": result.get("eligible_for_agent", False),
            "candidate_commit": result.get("candidate_commit"),
            "candidate_branch": baseline["candidate_branch"],
            "target_commit": baseline["target_commit"],
            "source_commit": baseline["source_commit"],
        }
    )
    append_step_summary(
        "\n".join(
            [
                "### cmux personal candidate",
                "",
                f"- status: `{result['status']}`",
                f"- source: `{baseline['source_commit']}`",
                f"- target: `{baseline['target_commit']}`",
                f"- personal commits: {len(baseline['personal_commits'])}",
                f"- conflicts: {len(result.get('conflicted_paths', []))}",
            ]
        )
    )
    print(json.dumps(result, indent=2))
    return 0


def verify_command(args: argparse.Namespace) -> int:
    repo = pathlib.Path(args.repo).resolve()
    result = verify_candidate(
        repo,
        baseline=load_json(args.baseline),
        policy=load_json(args.policy),
    )
    if args.bundle:
        create_bundle(repo, pathlib.Path(args.bundle).resolve())
    write_json(args.output, result)
    append_github_output(result)
    print(json.dumps(result, indent=2))
    return 0


def clone_bundle_command(args: argparse.Namespace) -> int:
    bundle = pathlib.Path(args.bundle).resolve()
    destination = pathlib.Path(args.destination).resolve()
    if destination.exists():
        raise ControlError(f"bundle clone destination already exists: {destination}")
    run(["git", "clone", str(bundle), str(destination)])
    expected = args.expected_commit
    if expected and resolve_commit(destination, "HEAD") != expected:
        raise ControlError("bundle HEAD does not match the expected candidate commit")
    return 0


def publish_candidate(
    repo: pathlib.Path,
    *,
    remote_url: str,
    candidate_branch: str,
    expected_commit: str,
    github_auth: bool = False,
) -> None:
    if not BRANCH_RE.fullmatch(candidate_branch):
        raise ControlError(f"unsafe candidate branch name: {candidate_branch!r}")
    if git(repo, "check-ref-format", "--branch", candidate_branch, check=False).returncode != 0:
        raise ControlError(f"invalid candidate branch name: {candidate_branch!r}")
    head = resolve_commit(repo, "HEAD")
    if head != expected_commit:
        raise ControlError(
            f"candidate HEAD {head} does not match the expected commit {expected_commit}"
        )
    if not remote_url or remote_url.startswith("-"):
        raise ControlError(f"unsafe candidate remote: {remote_url!r}")

    authentication = (
        ["-c", "credential.helper=!gh auth git-credential"] if github_auth else []
    )
    git(
        repo,
        *authentication,
        "push",
        "--force",
        remote_url,
        f"{expected_commit}:refs/heads/{candidate_branch}",
    )
    published = git(
        repo,
        *authentication,
        "ls-remote",
        "--exit-code",
        remote_url,
        f"refs/heads/{candidate_branch}",
    ).stdout.splitlines()
    remote_commits = [line.split("\t", 1)[0] for line in published if "\t" in line]
    if remote_commits != [expected_commit]:
        raise ControlError(
            f"published candidate ref does not resolve to {expected_commit}: {remote_commits!r}"
        )


def publish_command(args: argparse.Namespace) -> int:
    publish_candidate(
        pathlib.Path(args.repo).resolve(),
        remote_url=args.remote_url,
        candidate_branch=args.candidate_branch,
        expected_commit=args.expected_commit,
        github_auth=args.github_auth,
    )
    print(f"published {args.expected_commit} to {args.candidate_branch}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and verify personal cmux rebases.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--source-ref", required=True)
    prepare.add_argument("--current-base-ref", required=True)
    prepare.add_argument("--target-ref", required=True)
    prepare.add_argument("--candidate-branch", required=True)
    prepare.add_argument("--policy", required=True)
    prepare.add_argument("--baseline", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--bundle")
    prepare.add_argument("--leave-conflicts", action="store_true")
    prepare.set_defaults(handler=prepare_command)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--repo", required=True)
    verify.add_argument("--policy", required=True)
    verify.add_argument("--baseline", required=True)
    verify.add_argument("--output", required=True)
    verify.add_argument("--bundle")
    verify.set_defaults(handler=verify_command)

    clone = subparsers.add_parser("clone-bundle")
    clone.add_argument("--bundle", required=True)
    clone.add_argument("--destination", required=True)
    clone.add_argument("--expected-commit")
    clone.set_defaults(handler=clone_bundle_command)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--remote-url", required=True)
    publish.add_argument("--candidate-branch", required=True)
    publish.add_argument("--expected-commit", required=True)
    publish.add_argument("--github-auth", action="store_true")
    publish.set_defaults(handler=publish_command)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, KeyError, ValueError) as exc:
        raise SystemExit(f"candidate manager blocked: {exc}") from exc
