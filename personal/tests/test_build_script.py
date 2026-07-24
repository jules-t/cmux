from __future__ import annotations

import pathlib
import unittest


class BuildScriptTests(unittest.TestCase):
    @staticmethod
    def _script() -> str:
        return (
            pathlib.Path(__file__).resolve().parents[1] / "ci" / "build_personal.sh"
        ).read_text()

    def test_console_test_is_anchored_to_the_source_checkout(self) -> None:
        script = self._script()

        self.assertIn('GITHUB_WORKSPACE="$SOURCE_ROOT"', script)
        self.assertIn(
            '"$SOURCE_ROOT/scripts/ci/run-in-console-session.sh"',
            script,
        )
        self.assertIn(
            '"$SOURCE_ROOT/scripts/ci/run-app-host-xcodebuild.sh"',
            script,
        )

    def test_app_and_test_builds_use_separate_derived_data(self) -> None:
        script = self._script()

        self.assertIn('TEST_DERIVED_DATA="${DERIVED_DATA}-tests"', script)
        self.assertEqual(script.count('-derivedDataPath "$DERIVED_DATA"'), 2)
        self.assertEqual(script.count('-derivedDataPath "$TEST_DERIVED_DATA"'), 1)

    def test_package_downloads_are_retried_before_offline_builds(self) -> None:
        script = self._script()

        self.assertIn("for attempt in 1 2 3; do", script)
        self.assertIn("-resolvePackageDependencies", script)
        self.assertIn("failed to resolve Swift packages after 3 attempts", script)
        self.assertEqual(script.count("-disableAutomaticPackageResolution"), 2)
