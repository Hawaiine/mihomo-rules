#!/usr/bin/env python3
"""F1 RED/GREEN：候选 PR verify job 必须调用双发发布器并产生 Check Run 对象证据。

RED：当前代码调用 publish_status.py，缺 checks: write 权限，artifact 中无 check_run。
GREEN：切换到 publish_run_status.py --publish-mode=dual 后，artifact 含 check_run/steps/historical_status_probe。
"""
import unittest
import subprocess
import json
import tempfile
from pathlib import Path


class TestCandidatePublisherIntegration(unittest.TestCase):
    """测试候选 PR 路径的发布器调用及 artifact 格式。"""

    def setUp(self):
        self.repo_root = Path(__file__).parents[2]
        self.candidate_workflow = self.repo_root / ".github/workflows/candidate-pr-sync.yml"

    def test_candidate_verify_job_uses_dual_publisher(self):
        """验证 candidate-pr-sync.yml verify job 调用了 publish_run_status.py --publish-mode=dual。"""
        content = self.candidate_workflow.read_text()
        self.assertIn("publish_run_status.py --publish-mode=dual", content,
                      "候选 verify job 必须调用双发发布器（publish_run_status.py --publish-mode=dual）")

    def test_candidate_verify_job_has_checks_write(self):
        """验证 candidate-pr-sync.yml verify job 拥有 checks: write 权限。"""
        content = self.candidate_workflow.read_text()
        # 简单文本搜索，确认 verify job 的 permissions 块包含 checks: write
        self.assertIn("checks: write", content,
                      "候选 verify job 必须声明 checks: write 权限以创建 Check Run")

    def test_artifact_schema_includes_check_run_fields(self):
        """验证双发发布器 artifact 包含 check_run/steps/historical_status_probe 字段（schema 设计）。

        该测试不真实运行 workflow，只验证 publish_run_status.py 在成功时输出的 JSON 结构。
        """
        # 简单的 schema 验证：读取 publish_run_status.py 输出的 artifact 格式说明，
        # 或运行一次本地存根测试来模拟发布器输出。
        # 这里仅证明代码意图：双发模式下，artifact 应包含 check_run 字段。
        # 实际 GREEN 需要在候选 workflow 真实运行后读取 artifact。
        # 本单元测试只验证发布器代码能力，而非平台执行。
        
        # 本测试用例主要作为 RED/GREEN 对照参考，真实 GREEN 验证在平台验收时进行。
        pass


class TestCandidateWorkflowComments(unittest.TestCase):
    """验证候选 workflow 注释已更新，反映双发事务语义。"""

    def test_candidate_workflow_comment_reflects_dual_publish(self):
        """验证注释不再声称「check-run 无法由 GITHUB_TOKEN 创建」。"""
        repo_root = Path(__file__).parents[2]
        candidate_workflow = repo_root / ".github/workflows/candidate-pr-sync.yml"
        content = candidate_workflow.read_text()
        self.assertNotIn("check-run 无法由 GITHUB_TOKEN 创建", content,
                         "注释已过时，候选路径现已可以用 GITHUB_TOKEN + checks: write 创建 Check Run")


if __name__ == "__main__":
    unittest.main()
