from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from personal import report_issue


class ReportIssueTests(unittest.TestCase):
    @mock.patch("personal.report_issue.gh")
    def test_existing_issue_body_and_comment_receive_the_latest_report(
        self,
        gh: mock.Mock,
    ) -> None:
        gh.side_effect = [
            subprocess.CompletedProcess([], 0, '[{"number": 7, "title": "attention"}]', ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        arguments = [
            "report_issue.py",
            "--repo",
            "owner/repo",
            "--title",
            "attention",
            "--body",
            "latest run",
        ]

        with mock.patch.object(sys, "argv", arguments):
            self.assertEqual(report_issue.main(), 0)

        edit = gh.call_args_list[1]
        comment = gh.call_args_list[2]
        self.assertEqual(edit.args[:3], ("issue", "edit", "7"))
        self.assertIn("latest run", edit.args)
        self.assertEqual(comment.args[:3], ("issue", "comment", "7"))
        self.assertIn("latest run", comment.args)


if __name__ == "__main__":
    unittest.main()
