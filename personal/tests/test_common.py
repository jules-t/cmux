from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from personal.common import ControlError, run


class CommonTests(unittest.TestCase):
    @mock.patch("personal.common.subprocess.run")
    def test_run_converts_subprocess_timeouts_to_control_errors(
        self,
        subprocess_run: mock.Mock,
    ) -> None:
        subprocess_run.side_effect = subprocess.TimeoutExpired(["gh", "api"], 30)

        with self.assertRaisesRegex(ControlError, "timed out after 30 seconds"):
            run(["gh", "api"], timeout=30)


if __name__ == "__main__":
    unittest.main()
