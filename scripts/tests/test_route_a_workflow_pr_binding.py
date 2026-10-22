#!/usr/bin/env python3
"""Route A workflow_run 必须把事件 payload 的 PR 编号传给受信 publisher。"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/route-a-status.yml"


class RouteAWorkflowPRBindingTest(unittest.TestCase):
    def test_workflow_run_pr_number_is_forwarded_before_zero_fallback(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        expected = "github.event.workflow_run.pull_requests[0].number"
        self.assertIn(expected, text)
        self.assertIn("PR_NUM:", text)
        line = next(line for line in text.splitlines() if "PR_NUM:" in line)
        self.assertIn(expected, line)
        self.assertIn("inputs.pr", line)
        self.assertIn("'0'", line)
        self.assertLess(line.index(expected), line.index("'0'"))


if __name__ == "__main__":
    unittest.main()
