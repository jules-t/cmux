from __future__ import annotations

import pathlib
import unittest


class BuildScriptTests(unittest.TestCase):
    def test_console_test_is_anchored_to_the_source_checkout(self) -> None:
        script = (
            pathlib.Path(__file__).resolve().parents[1] / "ci" / "build_personal.sh"
        ).read_text()

        self.assertIn('GITHUB_WORKSPACE="$SOURCE_ROOT"', script)
        self.assertIn(
            '"$SOURCE_ROOT/scripts/ci/run-in-console-session.sh"',
            script,
        )
        self.assertIn(
            '"$SOURCE_ROOT/scripts/ci/run-app-host-xcodebuild.sh"',
            script,
        )

