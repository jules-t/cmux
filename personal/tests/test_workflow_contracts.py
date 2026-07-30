from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
