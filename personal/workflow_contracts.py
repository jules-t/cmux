from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
from collections.abc import Iterable


PINNED_ACTION_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
NEEDS_RE = re.compile(r"^    needs:\s*(.+?)\s*$", re.MULTILINE)
RUNNER_RE = re.compile(r"^    runs-on:\s*(.+?)\s*$", re.MULTILINE)
JOB_IF_RE = re.compile(r"^    if:\s*(.+?)\s*$", re.MULTILINE)
USES_RE = re.compile(r"^\s+(?:-\s+)?uses:\s*([^#\s]+)", re.MULTILINE)
PERSONAL_TOKEN = "${{ secrets.PERSONAL_FORK_TOKEN }}"
MUTATION_MARKERS = (
    r"release_publisher\.py\s+(?:reserve|ensure-reservation|publish)",
    r"state_manager\.py\s+(?:claim-publication|finalize-publication)",
    r"candidate_manager\.py\s+publish",
    r"\bgh workflow run\b",
    r"git/refs/heads/personal/stable",
    r"--force-with-lease=.*refs/heads/personal/stable",
    r"\bgh release (?:create|edit|upload|delete)\b",
)


@dataclasses.dataclass(frozen=True)
class Step:
    name: str
    text: str


@dataclasses.dataclass(frozen=True)
class Job:
    name: str
    text: str
    needs: frozenset[str]
    runner: str | None
    has_job_if: bool
    steps: tuple[Step, ...]


class WorkflowContractError(RuntimeError):
    pass


def parse_needs(raw: str, *, path: pathlib.Path, job: str) -> frozenset[str]:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        names = [part.strip() for part in value[1:-1].split(",")]
    elif re.fullmatch(r"[A-Za-z0-9_-]+", value):
        names = [value]
    else:
        raise WorkflowContractError(
            f"{path}: job {job!r} uses an unsupported needs format: {raw!r}"
        )
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
        raise WorkflowContractError(f"{path}: job {job!r} has an invalid needs entry")
    return frozenset(names)


def parse_steps(job_text: str) -> tuple[Step, ...]:
    lines = job_text.splitlines(keepends=True)
    starts = [
        index
        for index, line in enumerate(lines)
        if line.startswith("      - ")
    ]
    result: list[Step] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        text = "".join(lines[start:end])
        match = re.match(r"^      - name:\s*(.+?)\s*$", lines[start])
        name = match.group(1) if match else "(unnamed action step)"
        result.append(Step(name=name, text=text))
    return tuple(result)


def parse_jobs(path: pathlib.Path, text: str) -> dict[str, Job]:
    lines = text.splitlines(keepends=True)
    try:
        jobs_line = next(index for index, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration as exc:
        raise WorkflowContractError(f"{path}: no top-level jobs mapping") from exc
    starts = [
        (index, match.group(1))
        for index, line in enumerate(lines[jobs_line + 1 :], start=jobs_line + 1)
        if (match := JOB_RE.match(line))
    ]
    if not starts:
        raise WorkflowContractError(f"{path}: no jobs found")
    jobs: dict[str, Job] = {}
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        block = "".join(lines[start:end])
        needs_match = NEEDS_RE.search(block)
        needs = (
            parse_needs(needs_match.group(1), path=path, job=name)
            if needs_match
            else frozenset()
        )
        runner_match = RUNNER_RE.search(block)
        jobs[name] = Job(
            name=name,
            text=block,
            needs=needs,
            runner=runner_match.group(1).strip() if runner_match else None,
            has_job_if=bool(JOB_IF_RE.search(block)),
            steps=parse_steps(block),
        )
    return jobs


def ancestors(job_name: str, jobs: dict[str, Job]) -> frozenset[str]:
    seen: set[str] = set()
    pending = list(jobs[job_name].needs)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        if name not in jobs:
            raise WorkflowContractError(
                f"job {job_name!r} needs missing job {name!r}"
            )
        seen.add(name)
        pending.extend(jobs[name].needs)
    return frozenset(seen)


def step_input(step: Step, name: str) -> str | None:
    match = re.search(rf"^\s{{10}}{re.escape(name)}:\s*(.+?)\s*$", step.text, re.MULTILINE)
    return match.group(1).strip() if match else None


def job_if_expression(job: Job) -> str | None:
    match = JOB_IF_RE.search(job.text)
    return match.group(1).strip() if match else None


def step_run_command(step: Step) -> str | None:
    match = re.search(r"^\s+run:\s*([^\n]+?)\s*$", step.text, re.MULTILINE)
    if match is None or match.group(1) in {"|", ">", "|-", ">-"}:
        return None
    return match.group(1)


def is_personal_mutation(step: Step) -> bool:
    return personal_mutation_offset(step) is not None


def personal_mutation_offset(step: Step) -> int | None:
    offsets = [
        match.start()
        for marker in MUTATION_MARKERS
        if (match := re.search(marker, step.text))
    ]
    return min(offsets) if offsets else None


def normalized_artifact_name(value: str) -> str:
    return re.sub(
        r"\$\{\{\s*(?:steps\.naming|needs\.metadata)\.outputs\.personal_tag\s*\}\}",
        "<personal-tag>",
        value,
    )


def validate_external_action_pins(
    path: pathlib.Path, text: str, errors: list[str]
) -> None:
    for value in USES_RE.findall(text):
        if value.startswith("./") or value.startswith("docker://"):
            continue
        if not PINNED_ACTION_RE.fullmatch(value):
            errors.append(f"{path}: external action is not pinned to a full SHA: {value}")


def validate_token_contracts(
    path: pathlib.Path, text: str, jobs: dict[str, Job], errors: list[str]
) -> None:
    if "secrets.PERSONAL_FORK_TOKEN || github.token" in text:
        errors.append(f"{path}: PERSONAL_FORK_TOKEN must never fall back to github.token")
    for job in jobs.values():
        if job.runner and "macos-" in job.runner and "PERSONAL_FORK_TOKEN" in job.text:
            errors.append(f"{path}: macOS job {job.name!r} exposes the personal token")
        for step in job.steps:
            state_push = (
                job.name in {"publish", "dispatch"}
                and "git -C state" in step.text
                and "push origin" in step.text
            )
            if not is_personal_mutation(step) and not state_push:
                continue
            mutation_offset = personal_mutation_offset(step)
            if state_push:
                push_offset = step.text.find("push origin")
                mutation_offset = (
                    push_offset
                    if mutation_offset is None
                    else min(mutation_offset, push_offset)
                )
            expected = f"GH_TOKEN: {PERSONAL_TOKEN}"
            if expected not in step.text:
                errors.append(
                    f"{path}: mutation step {job.name!r}/{step.name!r} does not use "
                    "PERSONAL_FORK_TOKEN exactly"
                )
            guard = 'if [[ -z "${GH_TOKEN:-}" ]]'
            guard_offset = step.text.find(guard)
            if guard_offset < 0:
                errors.append(
                    f"{path}: mutation step {job.name!r}/{step.name!r} has no "
                    "fail-closed token guard"
                )
            elif mutation_offset is not None and guard_offset > mutation_offset:
                errors.append(
                    f"{path}: mutation step {job.name!r}/{step.name!r} checks "
                    "the token only after the first mutation"
                )
    if "PERSONAL_FORK_TOKEN" in text and "|| github.token" in text:
        errors.append(f"{path}: token fallback syntax is forbidden")


def validate_artifacts(
    path: pathlib.Path, jobs: dict[str, Job], errors: list[str]
) -> None:
    uploads: dict[str, set[str]] = {}
    for job in jobs.values():
        for step in job.steps:
            if "uses: actions/upload-artifact@" not in step.text:
                continue
            name = step_input(step, "name")
            if not name:
                errors.append(f"{path}: {job.name!r}/{step.name!r} has no artifact name")
                continue
            uploads.setdefault(normalized_artifact_name(name), set()).add(job.name)
            if step_input(step, "if-no-files-found") != "error":
                errors.append(
                    f"{path}: {job.name!r}/{step.name!r} must fail when files are missing"
                )
            if (
                job.name == "attest"
                and normalized_artifact_name(name) == "cmux-personal-<personal-tag>"
            ):
                retention = step_input(step, "retention-days")
                if not retention or not retention.isdigit() or int(retention) < 7:
                    errors.append(
                        f"{path}: recoverable attested artifact retention must be >= 7 days"
                    )

    for job in jobs.values():
        job_ancestors = ancestors(job.name, jobs)
        for step in job.steps:
            if "uses: actions/download-artifact@" not in step.text:
                continue
            run_id = step_input(step, "run-id")
            if run_id:
                for required in ("repository", "github-token"):
                    if not step_input(step, required):
                        errors.append(
                            f"{path}: cross-run download {job.name!r}/{step.name!r} "
                            f"is missing {required}"
                        )
                if not step_input(step, "artifact-ids"):
                    errors.append(
                        f"{path}: cross-run download {job.name!r}/{step.name!r} "
                        "must use an immutable artifact ID"
                    )
                continue
            name = step_input(step, "name")
            producers = uploads.get(normalized_artifact_name(name or ""), set())
            if not name or not (producers & job_ancestors):
                errors.append(
                    f"{path}: same-run download {job.name!r}/{step.name!r} has no "
                    f"matching ancestor upload for {name!r}"
                )


def validate_target_history_checkouts(
    path: pathlib.Path, jobs: dict[str, Job], errors: list[str]
) -> None:
    requirements_by_workflow = {
        "personal-build.yml": {("metadata", "source")},
        "personal-main-canary.yml": {
            ("observe", "source"),
            ("prepare", "source"),
            ("resolve", "source"),
        },
        "personal-publish.yml": {("publish", "control")},
        "personal-update.yml": {
            ("prepare", "source"),
            ("resolve", "source"),
        },
    }
    for job in jobs.values():
        for step in job.steps:
            if "uses: actions/checkout@" not in step.text:
                continue
            if step_input(step, "fetch-depth") == "0":
                errors.append(
                    f"{path}: {job.name!r}/{step.name!r} fetches every repository ref"
                )

    for job_name, checkout_path in requirements_by_workflow.get(path.name, set()):
        job = jobs.get(job_name)
        if job is None:
            errors.append(f"{path}: missing full-history job {job_name!r}")
            continue
        matches = [
            (index, step)
            for index, step in enumerate(job.steps)
            if "uses: actions/checkout@" in step.text
            and step_input(step, "path") == checkout_path
        ]
        if len(matches) != 1:
            errors.append(
                f"{path}: {job_name!r} must have one {checkout_path!r} checkout"
            )
            continue
        index, checkout = matches[0]
        if (
            step_input(checkout, "fetch-depth") != "1"
            or step_input(checkout, "fetch-tags") != "false"
            or step_input(checkout, "filter") != "blob:none"
        ):
            errors.append(
                f"{path}: {job_name!r}/{checkout.name!r} must use a blobless shallow checkout"
            )
        history_step = job.steps[index + 1] if index + 1 < len(job.steps) else None
        expected = (
            "control/personal/ci/fetch_target_history.sh "
            f'"$GITHUB_WORKSPACE/{checkout_path}"'
        )
        if history_step is None or step_run_command(history_step) != expected:
            errors.append(
                f"{path}: {job_name!r}/{checkout.name!r} is not followed by "
                "an immutable target-history fetch"
            )


def validate_blocked_reporters(
    path: pathlib.Path, jobs: dict[str, Job], errors: list[str]
) -> None:
    job = jobs.get("report-blocked")
    if job is None:
        return
    condition = job_if_expression(job) or ""
    if "always()" in condition:
        errors.append(
            f"{path}: blocked reporter must not notify for runs cancelled by hand"
        )
    for required in ("!cancelled()", "needs.dispatch.result != 'cancelled'"):
        if required not in condition:
            errors.append(f"{path}: blocked reporter is missing {required}")
    for step in job.steps:
        if "report_issue.py" in step.text and "--dedupe-key" not in step.text:
            errors.append(
                f"{path}: {job.name!r}/{step.name!r} must suppress repeat "
                "notifications with a dedupe key"
            )


def validate_build_workflow(
    path: pathlib.Path, jobs: dict[str, Job], errors: list[str]
) -> None:
    required = {
        "validate",
        "metadata",
        "preflight",
        "ghostty_helper",
        "build",
        "attest",
        "publish",
        "report-failure",
    }
    missing = required - jobs.keys()
    if missing:
        errors.append(f"{path}: missing required jobs: {', '.join(sorted(missing))}")
        return
    if jobs["preflight"].has_job_if:
        errors.append(f"{path}: preflight must run as a successful no-op when publishing is off")

    metadata_checkout = next(
        (
            step
            for step in jobs["metadata"].steps
            if step.name == "Check out requested source"
        ),
        None,
    )
    if metadata_checkout is None:
        errors.append(f"{path}: metadata has no requested-source checkout")
    elif (
        step_input(metadata_checkout, "fetch-depth") != "1"
        or step_input(metadata_checkout, "fetch-tags") != "false"
        or step_input(metadata_checkout, "filter") != "blob:none"
    ):
        errors.append(
            f"{path}: metadata requested-source checkout must be a blobless shallow fetch"
        )

    metadata_history = next(
        (
            step
            for step in jobs["metadata"].steps
            if step.name == "Fetch requested source history"
        ),
        None,
    )
    if (
        metadata_history is None
        or 'fetch_target_history.sh "$GITHUB_WORKSPACE/source"'
        not in metadata_history.text
    ):
        errors.append(
            f"{path}: metadata must fetch only the immutable requested-source history"
        )
    if re.search(
        r"refs/(?:heads|tags)/\*|\bgit fetch\b[^\n]*--all\b",
        jobs["metadata"].text,
    ):
        errors.append(f"{path}: metadata must not fetch all branch or tag refs")

    report_condition = job_if_expression(jobs["report-failure"]) or ""
    if "always()" not in report_condition:
        errors.append(f"{path}: failure reporter must run after failed dependencies")
    for job_name in required - {"report-failure"}:
        for outcome in ("failure", "cancelled"):
            if f"needs.{job_name}.result == '{outcome}'" not in report_condition:
                errors.append(
                    f"{path}: failure reporter ignores {outcome} of {job_name!r}"
                )
    for job in jobs.values():
        if job.runner and "macos-" in job.runner:
            lineage = ancestors(job.name, jobs)
            for gate in ("validate", "preflight"):
                if gate not in lineage:
                    errors.append(
                        f"{path}: macOS job {job.name!r} is not downstream of {gate}"
                    )
    if "attest" not in ancestors("publish", jobs):
        errors.append(f"{path}: publication must be downstream of attestation")
    expected_report_needs = required - {"report-failure"}
    absent_report_needs = expected_report_needs - jobs["report-failure"].needs
    if absent_report_needs:
        errors.append(
            f"{path}: failure reporter does not need: "
            + ", ".join(sorted(absent_report_needs))
        )


def validate_main_canary_workflow(
    path: pathlib.Path,
    text: str,
    jobs: dict[str, Job],
    errors: list[str],
) -> None:
    if "workflow_dispatch:" not in text:
        errors.append(f"{path}: main canary has no manual preflight trigger")
    for job_name, message in (
        ("dispatch", "manual runs can reach candidate dispatch"),
        ("report-blocked", "manual runs can update the blocked issue"),
    ):
        job = jobs.get(job_name)
        if job is None:
            errors.append(f"{path}: missing required job {job_name!r}")
        elif "github.event_name == 'schedule'" not in (
            job_if_expression(job) or ""
        ):
            errors.append(f"{path}: {message}")

    preflight = jobs.get("preflight")
    if preflight is None:
        errors.append(f"{path}: missing manual preflight result job")
        return
    required_needs = frozenset({"observe", "prepare", "resolve", "review"})
    missing_needs = required_needs - preflight.needs
    if missing_needs:
        errors.append(
            f"{path}: manual preflight result job does not need: "
            + ", ".join(sorted(missing_needs))
        )
    preflight_if = job_if_expression(preflight) or ""
    if "always()" not in preflight_if or (
        "github.event_name == 'workflow_dispatch'" not in preflight_if
    ):
        errors.append(
            f"{path}: manual preflight result job is not an always-run manual gate"
        )
    if not any("preflight_gate.py" in step.text for step in preflight.steps):
        errors.append(
            f"{path}: manual preflight result job does not execute preflight_gate.py"
        )
    if any(is_personal_mutation(step) for step in preflight.steps):
        errors.append(f"{path}: manual preflight result job performs a mutation")


def validate_update_workflow(
    path: pathlib.Path, jobs: dict[str, Job], errors: list[str]
) -> None:
    dispatch = jobs.get("dispatch")
    if dispatch is None:
        errors.append(f"{path}: stable update workflow has no dispatch job")
        return
    dispatch_step = next(
        (
            step
            for step in dispatch.steps
            if "gh workflow run personal-build.yml" in step.text
        ),
        None,
    )
    if dispatch_step is None:
        errors.append(f"{path}: stable update does not dispatch the personal build")
        return
    state_update = dispatch_step.text.find("state_manager.py mark-attempt")
    state_push = dispatch_step.text.find("push origin HEAD:personal-control")
    build_dispatch = dispatch_step.text.find("gh workflow run personal-build.yml")
    if not 0 <= state_update < state_push < build_dispatch:
        errors.append(
            f"{path}: stable update must durably record state before build dispatch"
        )


def validate_publish_workflow(
    path: pathlib.Path,
    text: str,
    jobs: dict[str, Job],
    errors: list[str],
) -> None:
    if "publish" not in jobs:
        errors.append(f"{path}: recovery workflow has no publish job")
        return
    steps = jobs["publish"].steps
    identity = next(
        (
            index
            for index, step in enumerate(steps)
            if "verify_release_assets.py" in step.text
        ),
        None,
    )
    provenance = next(
        (
            index
            for index, step in enumerate(steps)
            if "gh attestation verify" in step.text
        ),
        None,
    )
    policy = next(
        (
            index
            for index, step in enumerate(steps)
            if "state_manager.py check-promotion" in step.text
        ),
        None,
    )
    claim = next(
        (
            index
            for index, step in enumerate(steps)
            if "state_manager.py claim-publication" in step.text
        ),
        None,
    )
    reservation = next(
        (
            index
            for index, step in enumerate(steps)
            if "release_publisher.py ensure-reservation" in step.text
        ),
        None,
    )
    publication = next(
        (
            index
            for index, step in enumerate(steps)
            if "release_publisher.py publish" in step.text
        ),
        None,
    )
    finalization = next(
        (
            index
            for index, step in enumerate(steps)
            if "state_manager.py finalize-publication" in step.text
        ),
        None,
    )
    mutation_indexes = [
        index for index, step in enumerate(steps) if is_personal_mutation(step)
    ]
    if identity is None or provenance is None:
        errors.append(f"{path}: recovery must verify asset identity and build provenance")
        return
    if mutation_indexes and min(mutation_indexes) <= max(identity, provenance):
        errors.append(f"{path}: recovery exposes mutation credentials before verification")
    if policy is None or (mutation_indexes and policy >= min(mutation_indexes)):
        errors.append(f"{path}: recovery must reject stale state before mutation")
    if claim is None or publication is None or finalization is None:
        errors.append(
            f"{path}: recovery must durably claim, publish, and finalize state"
        )
    else:
        if not (policy is not None and policy < claim < publication < finalization):
            errors.append(
                f"{path}: durable claim must precede publication and finalization"
            )
        if "push origin HEAD:personal-control" not in steps[claim].text:
            errors.append(
                f"{path}: publication claim is not durably pushed before release exposure"
            )
    if reservation is None:
        errors.append(
            f"{path}: recovery must ensure an exact reservation before publication"
        )
    elif (
        claim is not None
        and publication is not None
        and not claim < reservation < publication
    ):
        errors.append(
            f"{path}: exact reservation must follow the durable claim and precede publication"
        )
    if "group: cmux-personal-publication" not in text:
        errors.append(f"{path}: all publication runs must share one concurrency group")
    job_text = jobs["publish"].text
    if "--force-with-lease=" not in job_text:
        errors.append(f"{path}: source promotion must use an atomic force-with-lease")
    if "--method PATCH" in job_text and "personal/stable" in job_text:
        errors.append(f"{path}: source promotion must not use a non-atomic ref PATCH")
    if "--personal-stable-sha" not in job_text:
        errors.append(
            f"{path}: published source and actual personal/stable state must be separate"
        )
    for required in (
        "actions: read",
        "attestations: read",
        "contents: read",
        "--signer-digest",
        "--source-digest",
        "--source-ref",
        "--deny-self-hosted-runners",
    ):
        if required not in job_text:
            errors.append(f"{path}: recovery publication is missing {required!r}")


def validate_repository(root: pathlib.Path) -> list[str]:
    workflow_root = root / ".github" / "workflows"
    workflows = sorted(
        {
            *workflow_root.glob("*.yml"),
            *workflow_root.glob("*.yaml"),
        }
    )
    if not workflows:
        return [f"{root}: no workflows found"]
    errors: list[str] = []
    parsed: dict[str, dict[str, Job]] = {}
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        try:
            jobs = parse_jobs(path, text)
        except WorkflowContractError as exc:
            errors.append(str(exc))
            continue
        parsed[path.name] = jobs
        validate_external_action_pins(path, text, errors)
        validate_token_contracts(path, text, jobs, errors)
        validate_target_history_checkouts(path, jobs, errors)
        validate_blocked_reporters(path, jobs, errors)
        try:
            validate_artifacts(path, jobs, errors)
        except WorkflowContractError as exc:
            errors.append(f"{path}: {exc}")
    build_path = root / ".github" / "workflows" / "personal-build.yml"
    if "personal-build.yml" in parsed:
        try:
            validate_build_workflow(build_path, parsed["personal-build.yml"], errors)
        except WorkflowContractError as exc:
            errors.append(f"{build_path}: {exc}")
    else:
        errors.append(f"{build_path}: required workflow is missing")
    main_canary_path = root / ".github" / "workflows" / "personal-main-canary.yml"
    if "personal-main-canary.yml" in parsed:
        validate_main_canary_workflow(
            main_canary_path,
            main_canary_path.read_text(encoding="utf-8"),
            parsed["personal-main-canary.yml"],
            errors,
        )
    else:
        errors.append(f"{main_canary_path}: required workflow is missing")
    update_path = root / ".github" / "workflows" / "personal-update.yml"
    if "personal-update.yml" in parsed:
        validate_update_workflow(
            update_path,
            parsed["personal-update.yml"],
            errors,
        )
    else:
        errors.append(f"{update_path}: required stable update workflow is missing")
    publish_path = root / ".github" / "workflows" / "personal-publish.yml"
    if "personal-publish.yml" in parsed:
        validate_publish_workflow(
            publish_path,
            publish_path.read_text(encoding="utf-8"),
            parsed["personal-publish.yml"],
            errors,
        )
    else:
        errors.append(f"{publish_path}: required recovery workflow is missing")
    return errors


def render_errors(errors: Iterable[str]) -> str:
    return "\n".join(f"- {error}" for error in errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate cmux Personal workflow contracts.")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    args = parser.parse_args()
    errors = validate_repository(args.root.resolve())
    if errors:
        raise SystemExit("workflow contract validation failed:\n" + render_errors(errors))
    print("workflow contracts: verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
