from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from personal.workflow_contracts import (
    channel_aware_commands,
    parse_jobs,
    validate_blocked_reporters,
    validate_build_workflow,
    validate_channel_identity_forwarding,
    validate_channel_script_forwarding,
    validate_external_action_pins,
    validate_publish_workflow,
    validate_repository,
    validate_target_history_checkouts,
    validate_token_contracts,
    validate_update_workflow,
)


class WorkflowContractTests(unittest.TestCase):
    def test_rejects_personal_token_fallback(self) -> None:
        text = """\
jobs:
  dispatch:
    runs-on: ubuntu-24.04
    steps:
      - name: Dispatch
        env:
          GH_TOKEN: ${{ secrets.PERSONAL_FORK_TOKEN || github.token }}
        run: gh workflow run personal-build.yml
"""
        path = pathlib.Path("fallback.yml")
        errors: list[str] = []
        validate_token_contracts(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("fall back" in error for error in errors))

    def test_rejects_builtin_token_for_release_mutation(self) -> None:
        text = """\
jobs:
  publish:
    runs-on: ubuntu-24.04
    steps:
      - name: Publish
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release edit personal-v0.64.20-r1 --draft=false
"""
        path = pathlib.Path("builtin-token.yml")
        errors: list[str] = []
        validate_token_contracts(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("does not use PERSONAL_FORK_TOKEN" in error for error in errors))

    def test_rejects_a_token_guard_after_the_mutation(self) -> None:
        text = """\
jobs:
  publish:
    runs-on: ubuntu-24.04
    steps:
      - run: |
          gh release edit personal-v0.64.20-r1 --draft=false
          if [[ -z "${GH_TOKEN:-}" ]]; then exit 1; fi
        env:
          GH_TOKEN: ${{ secrets.PERSONAL_FORK_TOKEN }}
"""
        path = pathlib.Path("late-guard.yml")
        errors: list[str] = []
        validate_token_contracts(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("only after the first mutation" in error for error in errors))

    def test_unnamed_run_steps_are_checked_for_mutations(self) -> None:
        text = """\
jobs:
  publish:
    runs-on: ubuntu-24.04
    steps:
      - run: gh release edit personal-v0.64.20-r1 --draft=false
"""
        path = pathlib.Path("unnamed-step.yml")
        errors: list[str] = []
        validate_token_contracts(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("does not use PERSONAL_FORK_TOKEN" in error for error in errors))

    def test_rejects_unpinned_external_action(self) -> None:
        text = """\
jobs:
  validate:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v6
"""
        errors: list[str] = []
        validate_external_action_pins(pathlib.Path("unpinned.yml"), text, errors)
        self.assertEqual(len(errors), 1)

    def test_accepts_full_sha_action_pin(self) -> None:
        text = """\
jobs:
  validate:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
"""
        errors: list[str] = []
        validate_external_action_pins(pathlib.Path("pinned.yml"), text, errors)
        self.assertEqual(errors, [])

    def test_parser_rejects_ambiguous_needs_format(self) -> None:
        text = """\
jobs:
  build:
    needs:
      - validate
    runs-on: macos-26
"""
        with self.assertRaisesRegex(RuntimeError, "unsupported needs format"):
            parse_jobs(pathlib.Path("multiline.yml"), text)

    def test_checked_in_workflows_satisfy_project_contracts(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        self.assertEqual(validate_repository(repository), [])

    def test_rejects_a_manual_canary_without_a_non_mutating_terminal_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            workflow_root = repository / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "personal-main-canary.yml").write_text(
                """\
name: Unsafe manual canary
on:
  workflow_dispatch:
jobs:
  observe:
    runs-on: ubuntu-24.04
  prepare:
    needs: observe
    runs-on: ubuntu-24.04
  resolve:
    needs: [observe, prepare]
    runs-on: ubuntu-24.04
  review:
    needs: [observe, prepare, resolve]
    runs-on: ubuntu-24.04
  dispatch:
    needs: [observe, prepare, resolve, review]
    runs-on: ubuntu-24.04
  report-blocked:
    needs: [observe, prepare, resolve, review, dispatch]
    runs-on: ubuntu-24.04
""",
                encoding="utf-8",
            )

            errors = validate_repository(repository)

        self.assertTrue(
            any("manual runs can reach candidate dispatch" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("manual runs can update the blocked issue" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("manual preflight result job" in error for error in errors),
            errors,
        )

    def test_yaml_extension_is_included_in_repository_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = pathlib.Path(temporary)
            workflow_root = repository / ".github" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "unsafe.yaml").write_text(
                """\
jobs:
  validate:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v6
""",
                encoding="utf-8",
            )
            errors = validate_repository(repository)
        self.assertTrue(any("not pinned to a full SHA" in error for error in errors))

    def test_recovery_requires_a_durable_claim_before_publication(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-publish.yml"
        text = path.read_text(encoding="utf-8").replace(
            "state_manager.py claim-publication",
            "state_manager.py missing-claim",
        )
        errors: list[str] = []
        validate_publish_workflow(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("durably claim" in error for error in errors))

    def test_recovery_ensures_an_exact_reservation_before_publication(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-publish.yml"
        text = path.read_text(encoding="utf-8").replace(
            "release_publisher.py ensure-reservation",
            "release_publisher.py missing-reservation",
        )
        errors: list[str] = []
        validate_publish_workflow(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("exact reservation" in error for error in errors))

    def test_source_promotion_requires_an_atomic_lease(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-publish.yml"
        text = path.read_text(encoding="utf-8").replace(
            "--force-with-lease=",
            "--force=",
        )
        errors: list[str] = []
        validate_publish_workflow(path, text, parse_jobs(path, text), errors)
        self.assertTrue(any("atomic force-with-lease" in error for error in errors))

    def test_build_metadata_rejects_an_all_refs_source_checkout(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-build.yml"
        text = path.read_text(encoding="utf-8").replace(
            "fetch-depth: 1\n          fetch-tags: false\n          filter: blob:none",
            "fetch-depth: 0",
            1,
        )
        errors: list[str] = []
        validate_build_workflow(path, parse_jobs(path, text), errors)
        self.assertTrue(any("blobless shallow fetch" in error for error in errors))

    def test_build_metadata_requires_the_targeted_history_fetch(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-build.yml"
        text = path.read_text(encoding="utf-8").replace(
            'control/personal/ci/fetch_target_history.sh "$GITHUB_WORKSPACE/source"',
            "git fetch origin",
            1,
        )
        errors: list[str] = []
        validate_build_workflow(path, parse_jobs(path, text), errors)
        self.assertTrue(any("immutable requested-source history" in error for error in errors))

    def test_build_metadata_rejects_a_broad_origin_refspec(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-build.yml"
        text = path.read_text(encoding="utf-8").replace(
            'control/personal/ci/fetch_target_history.sh "$GITHUB_WORKSPACE/source"',
            "git fetch origin '+refs/heads/*:refs/remotes/origin/*'",
            1,
        )
        errors: list[str] = []
        validate_build_workflow(path, parse_jobs(path, text), errors)
        self.assertTrue(any("must not fetch all branch" in error for error in errors))

    def test_full_history_checkout_rejects_fetching_every_ref(self) -> None:
        text = """\
jobs:
  prepare:
    runs-on: ubuntu-24.04
    steps:
      - name: Check out current personal source
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
        with:
          path: source
          fetch-depth: 0
      - name: Continue
        run: true
"""
        path = pathlib.Path("all-refs.yml")
        errors: list[str] = []
        validate_target_history_checkouts(path, parse_jobs(path, text), errors)
        self.assertTrue(any("fetches every repository ref" in error for error in errors))

    def test_full_history_contract_does_not_depend_on_step_names(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-update.yml"
        text = (
            path.read_text(encoding="utf-8")
            .replace(
                "name: Check out current personal source",
                "name: Renamed source checkout",
                1,
            )
            .replace(
                'run: control/personal/ci/fetch_target_history.sh '
                '"$GITHUB_WORKSPACE/source"',
                "run: echo skipped",
                1,
            )
        )
        errors: list[str] = []
        validate_target_history_checkouts(path, parse_jobs(path, text), errors)
        self.assertTrue(any("immutable target-history fetch" in error for error in errors))

    def test_full_history_contract_requires_an_exact_helper_command(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-update.yml"
        expected = (
            'run: control/personal/ci/fetch_target_history.sh '
            '"$GITHUB_WORKSPACE/source"'
        )
        text = path.read_text(encoding="utf-8").replace(
            expected,
            f"run: echo '{expected}'",
            1,
        )
        errors: list[str] = []
        validate_target_history_checkouts(path, parse_jobs(path, text), errors)
        self.assertTrue(any("immutable target-history fetch" in error for error in errors))

    def test_build_metadata_rejects_checkout_tag_fetching(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-build.yml"
        text = path.read_text(encoding="utf-8").replace(
            "fetch-tags: false",
            "fetch-tags: true",
            1,
        )
        errors: list[str] = []
        validate_build_workflow(path, parse_jobs(path, text), errors)
        self.assertTrue(any("blobless shallow fetch" in error for error in errors))

    def test_build_failure_reporter_covers_failure_and_cancellation(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-build.yml"
        original = path.read_text(encoding="utf-8")
        for outcome in ("failure", "cancelled"):
            with self.subTest(outcome=outcome):
                text = original.replace(
                    f" || needs.metadata.result == '{outcome}'",
                    "",
                    1,
                )
                errors: list[str] = []
                validate_build_workflow(path, parse_jobs(path, text), errors)
                self.assertTrue(
                    any(
                        f"ignores {outcome} of 'metadata'" in error
                        for error in errors
                    )
                )

    def test_build_failure_reporter_requires_always(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-build.yml"
        text = path.read_text(encoding="utf-8").replace("always() && ", "", 1)
        errors: list[str] = []
        validate_build_workflow(path, parse_jobs(path, text), errors)
        self.assertTrue(any("must run after failed dependencies" in error for error in errors))

    def test_channel_aware_commands_are_discovered_from_their_parsers(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        commands = channel_aware_commands(repository)
        self.assertIn("release_publisher.py publish", commands)
        self.assertIn("state_manager.py finalize-publication", commands)
        self.assertIn("verify_release_assets.py", commands)
        self.assertNotIn("state_manager.py claim-publication", commands)

    def test_publication_must_forward_the_release_channel(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        path = repository / ".github" / "workflows" / "personal-publish.yml"
        original = path.read_text(encoding="utf-8")
        for command in (
            "release_publisher.py publish",
            "state_manager.py finalize-publication",
            "verify_release_assets.py",
        ):
            with self.subTest(command=command):
                index = original.index(command)
                tail = original.index('--upstream-main-sha "', index)
                end = original.index("\n", tail)
                text = original[:tail] + original[end + 1 :]
                errors: list[str] = []
                validate_channel_identity_forwarding(
                    repository, path, parse_jobs(path, text), errors
                )
                self.assertTrue(
                    any(command in error for error in errors),
                    msg=f"dropping {command}'s channel argument was not detected",
                )

    def test_build_script_must_forward_the_release_channel(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        script = repository / "personal" / "ci" / "build_personal.sh"
        original = script.read_text(encoding="utf-8")
        patched = original.replace(
            '  --upstream-main-sha "$UPSTREAM_MAIN_SHA" \\\n', "", 1
        )
        self.assertNotEqual(original, patched)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "personal" / "ci").mkdir(parents=True)
            for name in ("release_publisher.py", "package_bundle.py", "build_manifest.py"):
                (root / "personal" / name).write_text(
                    (repository / "personal" / name).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            (root / "personal" / "ci" / "build_personal.sh").write_text(
                patched, encoding="utf-8"
            )
            errors: list[str] = []
            validate_channel_script_forwarding(root, errors)
        self.assertTrue(any("packaged as a stable one" in error for error in errors))

    def test_blocked_reporter_rejects_notifying_for_cancelled_runs(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        for name in ("personal-update.yml", "personal-main-canary.yml"):
            with self.subTest(workflow=name):
                path = repository / ".github" / "workflows" / name
                text = path.read_text(encoding="utf-8").replace(
                    "!cancelled() && needs.dispatch.result != 'cancelled' && ",
                    "always() && ",
                    1,
                )
                errors: list[str] = []
                validate_blocked_reporters(path, parse_jobs(path, text), errors)
                self.assertTrue(
                    any("cancelled by hand" in error for error in errors)
                )

    def test_blocked_reporter_requires_a_dedupe_key(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        for name, key in (
            ("personal-update.yml", 'automatic-update:${TARGET_TAG:-unknown}'),
            ("personal-main-canary.yml", 'main-canary:${UPSTREAM_SHA:-unknown}'),
        ):
            with self.subTest(workflow=name):
                path = repository / ".github" / "workflows" / name
                text = path.read_text(encoding="utf-8").replace(
                    f'            --dedupe-key "{key}" \\\n',
                    "",
                    1,
                )
                errors: list[str] = []
                validate_blocked_reporters(path, parse_jobs(path, text), errors)
                self.assertTrue(any("dedupe key" in error for error in errors))

    def test_daily_main_workflow_dispatches_only_the_gated_publishing_build(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        text = (
            repository / ".github" / "workflows" / "personal-main-canary.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('cron: "41 3 * * *"', text)
        self.assertEqual(text.count("cron:"), 1)
        self.assertIn("-f expected_upstream_main_sha=\"$UPSTREAM_SHA\"", text)
        self.assertIn("-f runtime_manifest_asset=\"$RUNTIME_MANIFEST_ASSET\"", text)
        self.assertIn("-f promote_source=true", text)
        self.assertIn("-f publish_release=true", text)
        self.assertIn("gh attestation verify", text)
        self.assertIn("review-cmux-resolution.txt", text)

    def test_stable_update_rejects_state_recording_after_build_dispatch(self) -> None:
        text = """\
jobs:
  dispatch:
    runs-on: ubuntu-24.04
    steps:
      - name: Dispatch
        run: |
          gh workflow run personal-build.yml
          python3 state_manager.py mark-attempt
          push origin HEAD:personal-control
"""
        path = pathlib.Path("unsafe-update.yml")
        errors: list[str] = []
        validate_update_workflow(path, parse_jobs(path, text), errors)
        self.assertTrue(any("record state before build dispatch" in error for error in errors))

    def test_conflict_resolvers_use_narrow_rebase_permissions(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        sandbox = (repository / "personal" / "ci" / "run_pi_agent.sh").read_text(
            encoding="utf-8"
        )
        runner = (repository / "personal" / "pi" / "run_agent.mjs").read_text(
            encoding="utf-8"
        )
        dockerfile = (repository / "personal" / "pi" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("target=/workspace", sandbox)
        self.assertIn("target=/workspace,readonly", sandbox)
        self.assertIn("source=$CONTROL_ROOT,target=/control,readonly", sandbox)
        self.assertIn("--read-only", sandbox)
        self.assertIn("--cap-drop ALL", sandbox)
        self.assertIn("--security-opt no-new-privileges", sandbox)
        self.assertIn("--pull never", sandbox)
        self.assertIn("env -u DEEPSEEK_API_KEY docker run", sandbox)
        self.assertNotIn("--env DEEPSEEK_API_KEY", sandbox)
        self.assertRegex(dockerfile.splitlines()[0], r"^FROM .+@sha256:[0-9a-f]{64}$")
        self.assertIn('resolver: ["read", "bash", "edit", "write"', runner)
        self.assertIn('reviewer: ["read", "bash", "grep", "find", "ls"]', runner)
        self.assertNotIn('reviewer: ["read", "bash", "edit"', runner)
        self.assertIn("process.stdin.setEncoding", runner)
        self.assertNotIn("process.env.DEEPSEEK_API_KEY", runner)

        for workflow_name in ("personal-main-canary.yml", "personal-update.yml"):
            with self.subTest(workflow=workflow_name):
                text = (
                    repository / ".github" / "workflows" / workflow_name
                ).read_text(encoding="utf-8")
                self.assertIn('--workspace-root "$GITHUB_WORKSPACE/source"', text)
                self.assertIn("--profile resolver", text)
                self.assertIn('--workspace-root "$GITHUB_WORKSPACE"', text)
                self.assertIn("--profile reviewer", text)
                self.assertIn(
                    ".source_commit | select(type == \"string\"",
                    text,
                )
                self.assertIn(
                    "refs/remotes/personal-source/stable",
                    text,
                )
                self.assertIn(
                    'if [[ "$fetched_source_commit" != "$expected_source_commit" ]]',
                    text,
                )

    def test_conflict_agents_use_deepseek_v4_flash(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        package = json.loads(
            (repository / "personal" / "pi" / "package.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            package["dependencies"],
            {"@earendil-works/pi-coding-agent": "0.83.0"},
        )
        runner = (repository / "personal" / "pi" / "run_agent.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn('const PROVIDER = "deepseek";', runner)
        self.assertIn('const MODEL = "deepseek-v4-flash";', runner)
        self.assertIn('const THINKING_LEVEL = "max";', runner)
        self.assertIn("ModelRuntime.create", runner)
        self.assertIn("setRuntimeApiKey", runner)
        self.assertNotIn("process.env.DEEPSEEK_API_KEY", runner)
        self.assertFalse(
            any(
                path.is_file()
                for path in (repository / ".github" / "codex").rglob("*")
            )
        )

        for workflow_name in ("personal-main-canary.yml", "personal-update.yml"):
            with self.subTest(workflow=workflow_name):
                text = (
                    repository / ".github" / "workflows" / workflow_name
                ).read_text(encoding="utf-8")
                self.assertNotIn("secrets.OPENAI_API_KEY", text)
                self.assertNotIn("OPENAI_KEY", text)
                self.assertEqual(text.count("secrets.DEEPSEEK_API_KEY"), 3)
                self.assertNotIn("openai/", text.lower())
                self.assertNotIn("codex", text.lower())
                self.assertEqual(text.count("run_pi_agent.sh"), 2)
                self.assertEqual(text.count("setup_pi.sh"), 2)

    def test_control_checks_validate_the_pinned_pi_runner(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        check_script = (
            repository / "personal" / "ci" / "check_control_plane.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("--prefix personal/pi", check_script)
        self.assertIn("npm --prefix personal/pi run check", check_script)

        for workflow_name in ("personal-build.yml", "personal-control-checks.yml"):
            with self.subTest(workflow=workflow_name):
                text = (
                    repository / ".github" / "workflows" / workflow_name
                ).read_text(encoding="utf-8")
                self.assertIn('node-version: "24"', text)
                self.assertIn(
                    "cache-dependency-path: personal/pi/package-lock.json",
                    text,
                )

    def test_conflict_resolvers_require_a_valid_durable_report(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        prompt = (
            repository / ".github" / "pi" / "prompts" / "resolve-cmux-conflicts.txt"
        ).read_text(encoding="utf-8")
        report_script = (repository / "personal" / "resolver_report.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("write a JSON object to `.cmux-resolver-output.json`", prompt)
        self.assertIn("Your conversational final\nmessage is not used", prompt)
        self.assertIn(
            'EXPECTED_KEYS = {"decision", "confidence", "summary", "files"}',
            report_script,
        )
        self.assertIn("source.unlink(missing_ok=True)", report_script)

        for workflow_name in ("personal-main-canary.yml", "personal-update.yml"):
            with self.subTest(workflow=workflow_name):
                text = (
                    repository / ".github" / "workflows" / workflow_name
                ).read_text(encoding="utf-8")
                self.assertIn("name: Collect and validate resolver report", text)
                self.assertIn(
                    "python3 control/personal/resolver_report.py collect",
                    text,
                )
                self.assertIn(
                    "python3 control/personal/resolver_report.py require-resolved",
                    text,
                )
                self.assertNotIn("python3 -c", text)
                self.assertNotIn("<<'PY'", text)

    def test_deepseek_smoke_workflow_is_manual_and_read_only(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        text = (
            repository / ".github" / "workflows" / "personal-deepseek-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("--profile smoke", text)
        self.assertIn('--workspace-root "$GITHUB_WORKSPACE/control"', text)
        self.assertIn("secrets.DEEPSEEK_API_KEY", text)
        self.assertIn("agent_output.py smoke", text)
        self.assertNotIn("openai/", text.lower())
        self.assertNotIn("codex", text.lower())

        runner = (repository / "personal" / "pi" / "run_agent.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn('smoke: ["read", "grep", "find", "ls"]', runner)

    def test_stable_update_rebases_from_a_recorded_main_base_when_present(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        text = (
            repository / ".github" / "workflows" / "personal-update.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            ".current_upstream_main_sha // .current_stable_tag",
            text,
        )
        self.assertIn('--current-base-ref "$CURRENT_SOURCE_BASE"', text)
        self.assertNotIn('--current-base-ref "$CURRENT_TAG"', text)

    def test_main_build_requires_exact_nightly_runtime_provenance(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        text = (
            repository / ".github" / "workflows" / "personal-build.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("expected_upstream_main_sha:", text)
        self.assertIn("runtime_manifest_asset:", text)
        self.assertIn(
            '--signer-workflow "$UPSTREAM_REPOSITORY/.github/workflows/nightly.yml"',
            text,
        )
        self.assertIn('--source-digest "$UPSTREAM_MAIN_SHA"', text)


if __name__ == "__main__":
    unittest.main()
