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

    @mock.patch("personal.report_issue.gh")
    def test_unchanged_blocked_condition_refreshes_without_notifying(
        self,
        gh: mock.Mock,
    ) -> None:
        marker = report_issue.status_marker("automatic-update:v0.64.22")
        listing = (
            '[{"number": 7, "title": "attention", '
            f'"body": "earlier run\\n\\n{marker}"}}]'
        )
        gh.side_effect = [
            subprocess.CompletedProcess([], 0, listing, ""),
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
            "--dedupe-key",
            "automatic-update:v0.64.22",
        ]

        with mock.patch.object(sys, "argv", arguments):
            self.assertEqual(report_issue.main(), 0)

        self.assertEqual(gh.call_count, 2)
        edit = gh.call_args_list[1]
        self.assertEqual(edit.args[:3], ("issue", "edit", "7"))
        self.assertNotIn("comment", [call.args[1] for call in gh.call_args_list])

    @mock.patch("personal.report_issue.gh")
    def test_new_blocked_condition_still_notifies(self, gh: mock.Mock) -> None:
        marker = report_issue.status_marker("automatic-update:v0.64.21")
        listing = (
            '[{"number": 7, "title": "attention", '
            f'"body": "earlier run\\n\\n{marker}"}}]'
        )
        gh.side_effect = [
            subprocess.CompletedProcess([], 0, listing, ""),
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
            "--dedupe-key",
            "automatic-update:v0.64.22",
        ]

        with mock.patch.object(sys, "argv", arguments):
            self.assertEqual(report_issue.main(), 0)

        comment = gh.call_args_list[2]
        self.assertEqual(comment.args[:3], ("issue", "comment", "7"))
        self.assertIn(
            report_issue.status_marker("automatic-update:v0.64.22"),
            comment.args[-1],
        )


if __name__ == "__main__":
    unittest.main()
