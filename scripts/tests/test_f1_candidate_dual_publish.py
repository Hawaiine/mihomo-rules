#!/usr/bin/env python3
"""F1 RED/GREEN：候选 PR verify job 必须调用双发发布器，并产出符合契约的运行时报告。

RED：候选 workflow 调用 publish_status.py，缺 checks: write，artifact 中无 check_run。
GREEN：候选路径改由 publish_candidate_status.py 发布后，报告含 check_run/steps/风险字段。

本文件的 schema 断言不是"字段名出现在源码文本里"，而是**真实运行** dual 发布事务
（复用 test_publish_run_status 的本地 HTTP stub 与调用脚手架）、读取其返回的运行时
报告对象后校验。故意删除关键字段时断言必须失败（见 NegativeSchemaTest）。
"""
import copy
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import test_publish_run_status as prs_case  # noqa: E402  复用其 Stub / Case 脚手架

EXPECTED_APP_ID = 15368
EXPECTED_CONTEXT = "route-a/verify"
EXPECTED_STEPS = [
    "create_check_run:in_progress",
    "verify_check_run:in_progress",
    "verify_pr:post-create",
    "finalize_check_run:success",
    "verify_check_run:success",
    "verify_pr:pre-status",
    "publish:status+verify",
    "verify_pr:final",
]
RISK_FIELDS = ("compensation", "status_compensation", "historical_success_observed",
               "historical_success_revoked", "status_evidence_unknown",
               "possible_success_evidence_remains")


def schema_problems(rep) -> list[str]:
    """校验 dual 发布报告的运行时契约；返回问题列表（空表示合规）。"""
    p: list[str] = []
    if not isinstance(rep, dict):
        return [f"报告不是 dict：{type(rep).__name__}"]

    for key in ("ok", "publish_mode", "steps", "run", "pr", "check_run", "status",
                "historical_status_probe", *RISK_FIELDS):
        if key not in rep:
            p.append(f"缺少顶层字段 {key!r}")

    if rep.get("ok") is not True:
        p.append(f"ok={rep.get('ok')!r}，期望 True")
    if not isinstance(rep.get("run"), dict):
        p.append("run 不是 dict")
    elif rep["run"].get("run_id") != "9":
        p.append(f"run.run_id={rep['run'].get('run_id')!r}，该隔离用例预期绑定 run 9")
    if not isinstance(rep.get("pr"), dict):
        p.append("pr 不是 dict")

    if rep.get("publish_mode") != "dual":
        p.append(f"publish_mode={rep.get('publish_mode')!r}，期望 'dual'")

    steps = rep.get("steps")
    if steps != EXPECTED_STEPS:
        p.append(f"事务步骤序列不符：{steps!r}")

    head = (rep.get("run") or {}).get("head_sha")
    run_id = (rep.get("run") or {}).get("run_id")

    cr = rep.get("check_run")
    if not isinstance(cr, dict):
        p.append("check_run 不是 dict")
    else:
        if not isinstance(cr.get("id"), int) or cr.get("id", 0) <= 0:
            p.append(f"check_run.id 非正整数：{cr.get('id')!r}")
        if cr.get("name") != EXPECTED_CONTEXT:
            p.append(f"check_run.name={cr.get('name')!r}")
        if cr.get("head_sha") != head:
            p.append(f"check_run.head_sha={cr.get('head_sha')!r} 未绑定 run.head_sha={head!r}")
        if cr.get("external_id") != f"route-a-run:{run_id}":
            p.append(f"check_run.external_id={cr.get('external_id')!r}，期望 route-a-run:{run_id}")
        v = cr.get("verify")
        if not isinstance(v, dict):
            p.append("check_run.verify 不是 dict（缺对象级回读证据）")
        else:
            if v.get("verified") is not True:
                p.append(f"check_run.verify.verified={v.get('verified')!r}")
            if v.get("app_id") != EXPECTED_APP_ID:
                p.append(f"check_run.verify.app_id={v.get('app_id')!r}，期望 {EXPECTED_APP_ID}")
            if v.get("status") != "completed":
                p.append(f"check_run.verify.status={v.get('status')!r}")
            if v.get("conclusion") != "success":
                p.append(f"check_run.verify.conclusion={v.get('conclusion')!r}")
            if v.get("head_sha") != head:
                p.append(f"check_run.verify.head_sha={v.get('head_sha')!r}")

    st = rep.get("status")
    if not isinstance(st, dict):
        p.append("status 不是 dict（兼容通道缺失）")
    else:
        if st.get("context") != EXPECTED_CONTEXT:
            p.append(f"status.context={st.get('context')!r}")
        if st.get("posted") is not True:
            p.append(f"status.posted={st.get('posted')!r}")
        sv = st.get("verify")
        if not isinstance(sv, dict) or sv.get("verified") is not True:
            p.append("status.verify 未确认（兼容通道未回读成功）")

    probe = rep.get("historical_status_probe")
    if not isinstance(probe, dict):
        p.append("historical_status_probe 不是 dict")
    else:
        for k, typ in (("readable", bool), ("observed", bool), ("complete", bool),
                       ("pages_read", int)):
            if not isinstance(probe.get(k), typ):
                p.append(f"historical_status_probe.{k} 类型/缺失异常：{probe.get(k)!r}")

    for k in RISK_FIELDS:
        if k in rep and rep[k] is not None and not isinstance(rep[k], bool):
            p.append(f"风险字段 {k} 既非 null 也非 bool：{rep[k]!r}")
    for k in ("historical_success_observed", "status_evidence_unknown",
              "possible_success_evidence_remains"):
        if not isinstance(rep.get(k), bool):
            p.append(f"风险字段 {k} 必须是 bool，实际 {rep.get(k)!r}")

    return p


class TestCandidatePublisherIntegration(unittest.TestCase):
    """测试候选 PR 路径的发布器调用及 artifact 格式。"""

    def setUp(self):
        self.repo_root = Path(__file__).parents[2]
        self.candidate_workflow = self.repo_root / ".github/workflows/candidate-pr-sync.yml"

    def test_candidate_verify_job_uses_dual_publisher(self):
        """候选 verify job 必须调用候选专用双发发布器。

        注意：这里只断言入口名，**不**证明参数契约成立——参数契约由
        ``test_candidate_publish_contract.py`` 用真实 argparse + HTTP stub 验证。
        """
        content = self.candidate_workflow.read_text()
        self.assertIn("publish_candidate_status.py", content,
                      "候选 verify job 必须调用 publish_candidate_status.py")
        self.assertNotIn("publish_run_status.py --publish-mode=dual", content,
                         "候选路径不得复用 run 级核验发布器（其前提是 pr-verify run）")

    def test_candidate_verify_job_has_checks_write(self):
        """验证 candidate-pr-sync.yml verify job 拥有 checks: write 权限。"""
        content = self.candidate_workflow.read_text()
        self.assertIn("checks: write", content,
                      "候选 verify job 必须声明 checks: write 权限以创建 Check Run")


class DualPublishHarness(unittest.TestCase):
    """组合复用 test_publish_run_status 的脚手架，但**不继承** Case。

    ``Case`` 自身承载了全部 113 个发布器测试方法；直接继承会把它们一并重复收集，
    导致计数虚增。这里改用组合：借用其 Stub/服务端与 base_routes/flow/publish helper。
    """

    @classmethod
    def setUpClass(cls):
        prs_case.Case.setUpClass()
        cls.api = prs_case.Case.api
        cls._helper = prs_case.Case(methodName="runTest")

    @classmethod
    def tearDownClass(cls):
        prs_case.Case.tearDownClass()

    def setUp(self):
        self._helper.setUp()

    def publish(self, mode="dual"):
        return self._helper.publish(mode=mode)


class TestDualPublishRuntimeSchema(DualPublishHarness):
    """F-B：对 dual 发布事务的**真实运行时输出**做 schema 校验（非源码文本匹配）。"""

    def test_artifact_schema_includes_check_run_fields(self):
        """真实跑一次 dual 事务，校验报告含 check_run/steps/historical_status_probe 及绑定值。"""
        self._helper.base_routes()
        self._helper.flow()
        rep = self.publish(mode="dual")
        problems = schema_problems(rep)
        self.assertEqual(problems, [], "dual 发布报告未满足运行时契约：\n" + "\n".join(problems))

    def test_report_binds_exact_head_and_run(self):
        self._helper.base_routes()
        self._helper.flow()
        rep = self.publish(mode="dual")
        self.assertEqual(rep["check_run"]["head_sha"], prs_case.HEAD)
        self.assertEqual(rep["check_run"]["external_id"], "route-a-run:9")
        self.assertEqual(rep["check_run"]["verify"]["app_id"], EXPECTED_APP_ID)


class NegativeSchemaTest(DualPublishHarness):
    """证明上述断言有效：删除或篡改任意关键字段都必须被 schema 检查捕获。"""

    def _report(self):
        self._helper.base_routes()
        self._helper.flow()
        rep = self.publish(mode="dual")
        self.assertEqual(schema_problems(rep), [], "基线报告应先合规")
        return rep

    def test_missing_check_run_detected(self):
        rep = self._report()
        del rep["check_run"]
        self.assertTrue(schema_problems(rep), "删除 check_run 必须被捕获")

    def test_missing_external_id_detected(self):
        rep = copy.deepcopy(self._report())
        rep["check_run"].pop("external_id", None)
        self.assertTrue(schema_problems(rep), "删除 external_id 必须被捕获")

    def test_wrong_app_id_detected(self):
        rep = copy.deepcopy(self._report())
        rep["check_run"]["verify"]["app_id"] = 99999
        self.assertTrue(schema_problems(rep), "app.id 不符必须被捕获")

    def test_step_order_drift_detected(self):
        rep = copy.deepcopy(self._report())
        rep["steps"] = list(reversed(rep["steps"]))
        self.assertTrue(schema_problems(rep), "事务步骤顺序漂移必须被捕获")

    def test_missing_risk_field_detected(self):
        rep = copy.deepcopy(self._report())
        rep.pop("possible_success_evidence_remains", None)
        self.assertTrue(schema_problems(rep), "删除风险字段必须被捕获")


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
