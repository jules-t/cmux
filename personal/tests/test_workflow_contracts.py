from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from personal.workflow_contracts import (
    parse_jobs,
    validate_external_action_pins,
    validate_publish_workflow,
    validate_repository,
    validate_token_contracts,
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

    def test_conflict_resolvers_use_narrow_rebase_permissions(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        config = (
            repository / ".github" / "codex" / "resolver-config.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('extends = ":workspace"', config)
        self.assertIn('".git" = "write"', config)
        self.assertIn('".agents" = "read"', config)
        self.assertIn('".codex" = "read"', config)
        self.assertNotIn(":danger-full-access", config)
        self.assertNotIn("enabled = true", config)

        for workflow_name in ("personal-main-canary.yml", "personal-update.yml"):
            with self.subTest(workflow=workflow_name):
                text = (
                    repository / ".github" / "workflows" / workflow_name
                ).read_text(encoding="utf-8")
                self.assertIn(
                    "control/.github/codex/resolver-config.toml",
                    text,
                )
                self.assertIn(
                    "codex-home: ${{ runner.temp }}/resolver-codex-home",
                    text,
                )
                self.assertIn(
                    'permission-profile: "rebase-workspace"',
                    text,
                )
                self.assertNotIn(
                    "permission-profile: \":workspace\"\n"
                    "          safety-strategy: drop-sudo\n"
                    "          working-directory: ${{ github.workspace }}/source",
                    text,
                )
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
        catalog = json.loads(
            (repository / ".github" / "codex" / "deepseek-models.json").read_text(
                encoding="utf-8"
            )
        )
        models = {model["slug"]: model for model in catalog["models"]}
        self.assertEqual(set(models), {"deepseek-v4-flash", "deepseek-v4-pro"})
        self.assertEqual(models["deepseek-v4-flash"]["context_window"], 1_048_576)
        self.assertEqual(models["deepseek-v4-flash"]["apply_patch_tool_type"], "freeform")
        self.assertEqual(
            {
                level["effort"]
                for level in models["deepseek-v4-flash"][
                    "supported_reasoning_levels"
                ]
            },
            {"low", "high", "max"},
        )

        for workflow_name in ("personal-main-canary.yml", "personal-update.yml"):
            with self.subTest(workflow=workflow_name):
                text = (
                    repository / ".github" / "workflows" / workflow_name
                ).read_text(encoding="utf-8")
                self.assertNotIn("secrets.OPENAI_API_KEY", text)
                self.assertNotIn("OPENAI_KEY", text)
                self.assertEqual(text.count("secrets.DEEPSEEK_API_KEY"), 3)
                self.assertEqual(
                    text.count("responses-api-endpoint: https://api.deepseek.com/responses"),
                    2,
                )
                self.assertEqual(text.count("model: deepseek-v4-flash"), 2)
                self.assertEqual(text.count("effort: max"), 2)
                self.assertEqual(text.count("deepseek-models.json"), 2)

    def test_deepseek_smoke_workflow_is_manual_and_read_only(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[2]
        text = (
            repository / ".github" / "workflows" / "personal-deepseek-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn('permission-profile: ":read-only"', text)
        self.assertIn("secrets.DEEPSEEK_API_KEY", text)
        self.assertIn("responses-api-endpoint: https://api.deepseek.com/responses", text)
        self.assertIn("model: deepseek-v4-flash", text)
        self.assertIn("effort: max", text)

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
