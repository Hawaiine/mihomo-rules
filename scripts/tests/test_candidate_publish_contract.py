#!/usr/bin/env python3
"""候选路径发布器的**真实参数契约**回归测试（RED → GREEN）。

背景（本轮实测缺陷）
====================
`candidate-pr-sync.yml` 的 verify job 自 PR #32 起调用
`publish_run_status.py --publish-mode=dual --base-branch ... --run-url ... --state ...`，
但 `publish_run_status.py` 的 argparse **不接受** `--base-branch` / `--run-url` /
`--state`，也从不读取它们。真实运行会以 exit 2 结束：

    publish_run_status.py: error: unrecognized arguments: --base-branch main
    --run-url ... --state success

于是候选路径的 `route-a/verify` **从未被发布过**——job 在发布步骤就死了，而且
`test_f1_candidate_dual_publish.py` 只 grep 了子串，所以一直是假绿。

本文件把契约钉死：
1. 从 workflow 里**逐字提取**真实命令行，替换占位符后交给同一个 CLI parser；
2. 用本地 HTTP stub 真实跑完整事务，校验 Check Run / status 的 head SHA 绑定；
3. 反向证明旧写法必被 argparse 拒绝（防止回退）；
4. 证明 `--state failure` 会如实发布 failure，而不是被硬编码成 success。
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "scripts" / "ci"))
sys.path.insert(0, str(Path(__file__).parent))

import publish_candidate_status as pc  # noqa: E402
import publish_run_status as prs  # noqa: E402
import test_publish_run_status as prs_case  # noqa: E402

HEAD = prs_case.HEAD
WORKFLOW = _ROOT / ".github" / "workflows" / "candidate-pr-sync.yml"
OLD_ARGS = [
    "--publish-mode=dual", "--repo", "o/r", "--pr", "29", "--head-sha", HEAD,
    "--base-branch", "main", "--run-id", "9", "--run-url",
    "https://github.com/o/r/actions/runs/9", "--state", "success",
]


def extract_workflow_command() -> list[str]:
    """从 workflow YAML 中提取 publish_candidate_status.py 的真实命令行 token。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "publish_candidate_status.py" in line:
            start = i
            break
    if start is None:
        raise AssertionError("workflow 未调用 publish_candidate_status.py")
    chunk = []
    for line in lines[start:]:
        body = line.strip()
        chunk.append(body[:-1].rstrip() if body.endswith("\\") else body)
        if not body.endswith("\\"):
            break
    command = " ".join(chunk)
    # 占位符 → 具体值（与 stub 的 repo/PR/HEAD 对齐）
    subs = {
        "${GITHUB_REPOSITORY}": "o/r",
        "${{ needs.publish.outputs.pr_number }}": "29",
        "${{ needs.publish.outputs.head_sha }}": HEAD,
        "${GITHUB_RUN_ID}": "9",
        "${GITHUB_RUN_ATTEMPT}": "1",
        "${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}":
            "https://github.com/o/r/actions/runs/9",
        "${{ steps.verify.outputs.state }}": "success",
        "${{ steps.verify.outputs.description }}": "受信校验通过（contract-test）",
        "$RUNNER_TEMP/run-status.json": "PLACEHOLDER_JSON",
    }
    for key, val in subs.items():
        command = command.replace(f'"{key}"', val)
    tokens = shlex.split(command)
    # 去掉解释器与脚本名，只保留参数
    idx = next(i for i, t in enumerate(tokens) if t.endswith("publish_candidate_status.py"))
    return tokens[idx + 1:]


class WorkflowContractTest(unittest.TestCase):
    """workflow 的真实参数必须被 CLI 完整接受（不是 grep 子串）。"""

    def test_every_workflow_flag_is_known_to_the_parser(self):
        argv = extract_workflow_command()
        parser = pc.build_parser()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            argv = [tmp.name if a == "PLACEHOLDER_JSON" else a for a in argv]
        known, unknown = parser.parse_known_args(argv)
        self.assertEqual(unknown, [], f"workflow 传了 parser 不认识的参数：{unknown}")
        self.assertEqual(known.repo, "o/r")
        self.assertEqual(known.pr, 29)
        self.assertEqual(known.head_sha, HEAD)
        self.assertEqual(known.state, "success")
        self.assertEqual(known.run_id, "9")
        self.assertEqual(known.run_attempt, "1")

    def test_old_invocation_is_rejected_by_argparse(self):
        """RED 证据：修复前的写法必然 exit 2（这就是候选路径从未发布的原因）。"""
        script = _ROOT / "scripts" / "ci" / "publish_run_status.py"
        proc = subprocess.run([sys.executable, str(script), *OLD_ARGS],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unrecognized arguments", proc.stderr)
        for flag in ("--base-branch", "--run-url", "--state"):
            self.assertIn(flag, proc.stderr)


class LiveTransactionTest(unittest.TestCase):
    """用本地 stub 真实跑 CLI，校验 head SHA 绑定与状态如实发布。"""

    @classmethod
    def setUpClass(cls):
        prs_case.Case.setUpClass()
        cls.api = prs_case.Case.api

    @classmethod
    def tearDownClass(cls):
        prs_case.Case.tearDownClass()

    def setUp(self):
        prs_case.Case(methodName="runTest").setUp()
        Stub = prs_case.Stub
        Stub.routes = {}
        Stub.stateful = True
        Stub.remote_statuses = []
        Stub.remote_checks = []
        Stub.inject = {}
        Stub.counters = {}
        Stub.pages = None
        Stub.run_doc_over = {}
        Stub.pr_doc_over = {}
        prs_case.Case(methodName="runTest").base_routes()

    def _run(self, state: str, attempt: str = "1", **over):
        Stub = prs_case.Stub
        if over.get("pr_head"):
            # 直接改写已构建的路由（pr_doc_over 只影响 base_routes() 的构建期）
            Stub.routes["GET /repos/o/r/pulls/29"] = (200, {
                "state": "open", "head": {"sha": over["pr_head"]},
                "base": {"ref": "main", "sha": "m" * 40}, "user": {"login": "Hawaiine"}})
        out = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        out.close()
        argv = ["--repo", "o/r", "--pr", "29", "--head-sha", HEAD,
                "--state", state, "--run-id", "9", "--run-attempt", attempt,
                "--base-branch", "main",
                "--run-url", "https://github.com/o/r/actions/runs/9",
                "--description", f"contract-test {state}",
                "--json", out.name]
        old = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL")}
        os.environ["GITHUB_TOKEN"] = "tok"
        os.environ["GITHUB_API_URL"] = self.api
        try:
            rc = pc.main(argv)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        try:
            doc = json.loads(Path(out.name).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = None
        os.unlink(out.name)
        return rc, doc

    def test_success_binds_exact_head_on_both_channels(self):
        rc, doc = self._run("success")
        self.assertEqual(rc, 0, doc)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["check_run"]["head_sha"], HEAD)
        self.assertEqual(doc["check_run"]["verify"]["head_sha"], HEAD)
        self.assertEqual(doc["check_run"]["verify"]["conclusion"], "success")
        self.assertEqual(doc["status"]["state"], "success")
        self.assertEqual(doc["run"]["head_sha"], HEAD)
        # 两条通道都真的落在远端
        Stub = prs_case.Stub
        self.assertTrue(any(c.get("conclusion") == "success" for c in Stub.remote_checks))
        self.assertTrue(any(s.get("state") == "success" for s in Stub.remote_statuses))

    def test_failure_state_is_published_honestly(self):
        rc, doc = self._run("failure")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["state"], "failure")
        self.assertEqual(doc["check_run"]["verify"]["conclusion"], "failure")
        self.assertEqual(doc["status"]["state"], "failure")
        Stub = prs_case.Stub
        self.assertFalse(any(c.get("conclusion") == "success" for c in Stub.remote_checks),
                         "失败不得留下任何 success Check Run")
        self.assertFalse(any(s.get("state") == "success" for s in Stub.remote_statuses),
                         "失败不得留下任何 success status")

    def test_head_sha_mismatch_refused_before_any_write(self):
        rc, doc = self._run("success", pr_head="f" * 40)
        self.assertEqual(rc, 1, doc)
        self.assertFalse(doc["ok"])
        Stub = prs_case.Stub
        self.assertEqual(Stub.remote_checks, [], "head 不符时不得创建 Check Run")
        self.assertEqual(Stub.remote_statuses, [], "head 不符时不得发布 status")

    def test_external_id_carries_attempt(self):
        rc, doc = self._run("success", attempt="3")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["check_run"]["external_id"], "route-a-run:9-3")
        self.assertEqual(doc["status"]["verify"]["target_url"],
                         "https://github.com/o/r/actions/runs/9")


class CompensationBindingTest(unittest.TestCase):
    """PR #39 复审缺陷：失败补偿把「拼接 run_id」当成数字 URL run id。

    RED：`report["run"]["run_id"] = "<numeric>-<attempt>"` 被 `_compensate_all()` 原样传给
    `compensate_status_failure()`，后者要求 commit status 的 `target_url` 里是**纯数字**
    run id，于是 status 补偿回读必然判失败（`回读 target_url 未绑定 run 40611534778-2`）。
    GREEN：Check Run 通道用 attempt-aware 绑定 id，status 通道用数字 `url_run_id`，两路独立。
    """

    RUN_ID = "40611534778"
    ATTEMPT = "2"

    @classmethod
    def setUpClass(cls):
        prs_case.Case.setUpClass()
        cls.api = prs_case.Case.api

    @classmethod
    def tearDownClass(cls):
        prs_case.Case.tearDownClass()

    def setUp(self):
        self._helper = prs_case.Case(methodName="runTest")
        self._helper.setUp()

    def _run(self, inject=None, state="success", attempt=None):
        self._helper.flow(**(inject or {}))
        out = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        out.close()
        argv = ["--repo", "o/r", "--pr", "29", "--head-sha", HEAD,
                "--state", state, "--run-id", self.RUN_ID,
                "--run-attempt", attempt or self.ATTEMPT,
                "--base-branch", "main",
                "--run-url", f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
                "--description", "compensation-binding-test", "--json", out.name]
        old = {k: os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL")}
        os.environ["GITHUB_TOKEN"] = "tok"
        os.environ["GITHUB_API_URL"] = self.api
        try:
            rc = pc.main(argv)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        try:
            doc = json.loads(Path(out.name).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = None
        os.unlink(out.name)
        return rc, doc

    # ── 1. external_id 必须是 attempt-aware，URL 必须是纯数字 ──

    def test_external_id_is_attempt_aware_but_url_run_id_is_numeric(self):
        rc, doc = self._run()
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["check_run"]["external_id"],
                         f"route-a-run:{self.RUN_ID}-{self.ATTEMPT}")
        self.assertEqual(doc["run"]["url_run_id"], self.RUN_ID)
        self.assertEqual(doc["status"]["verify"]["target_url"],
                         f"https://github.com/o/r/actions/runs/{self.RUN_ID}")

    # ── 2. status 补偿必须用数字 id 才能回读通过 ──

    def test_status_compensation_reads_back_with_numeric_run_id(self):
        rc, doc = self._run({"drift_after_status_post": True})
        self.assertEqual(rc, 1, doc)                     # 收尾复核失败 → 事务失败
        self.assertFalse(doc["ok"])
        ev = doc["evidence"]
        comp = ev["status_compensation"]
        self.assertTrue(comp["ok"],
                        f"status 补偿必须用数字 url_run_id 才能回读通过：{comp}")
        self.assertEqual(comp["readback"]["state"], "failure")
        self.assertEqual(comp["readback"]["target_url"],
                         f"https://github.com/o/r/actions/runs/{self.RUN_ID}")
        self.assertTrue(ev["compensation"]["ok"], ev["compensation"])
        self.assertEqual(ev["compensation"]["readback"]["external_id"],
                         f"route-a-run:{self.RUN_ID}-{self.ATTEMPT}")
        self.assertFalse(ev["possible_success_evidence_remains"])

    # ── 3. 两路补偿互相独立 ──

    def test_status_compensation_failure_does_not_block_check_run(self):
        rc, doc = self._run({"drift_after_status_post": True, "comp_post": 403})
        self.assertEqual(rc, 1, doc)
        ev = doc["evidence"]
        self.assertFalse(ev["status_compensation"]["ok"])
        self.assertTrue(ev["compensation"]["ok"], ev["compensation"])
        self.assertEqual({e["channel"] for e in ev["compensation_log"]},
                         {"status", "check_run"})
        self.assertTrue(ev["possible_success_evidence_remains"],
                        "status 撤销未确认时必须 fail-closed")

    def test_check_run_compensation_failure_does_not_block_status(self):
        rc, doc = self._run({"drift_after_status_post": True,
                             "check_patch_fail_after": 1})
        self.assertEqual(rc, 1, doc)
        ev = doc["evidence"]
        self.assertTrue(ev["status_compensation"]["ok"], ev["status_compensation"])
        self.assertFalse(ev["compensation"]["ok"])
        self.assertTrue(ev["possible_success_evidence_remains"])

    # ── 4. 回读对象 ID / URL 不符必须失败 ──

    def test_status_compensation_wrong_object_id_rejected(self):
        """URL 绑定正确、但回读到的 failure 不是本次 POST 的对象 → 必须判未确认。"""
        prs_case.Stub.routes = {
            f"POST /repos/o/r/statuses/{HEAD}": (201, {"id": 9002}),
            f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [{
                "id": 9999, "context": "route-a/verify", "state": "failure",
                "target_url": f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
                "creator": {"login": "github-actions[bot]", "type": "Bot"}}])}
        client = prs.Client("o/r", "tok", api=self.api)
        out = prs.compensate_status_failure(
            client, HEAD, self.RUN_ID, "id-binding-test",
            f"https://github.com/o/r/actions/runs/{self.RUN_ID}")
        self.assertFalse(out["ok"])
        self.assertIn("并非本次补偿 POST 对象", out["error"])

    def test_verify_published_state_rejects_wrong_run_url(self):
        """status 的 target_url 指向别的 run → 发布后回读必须拒绝。"""
        prs_case.Stub.routes = {
            f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [{
                "id": 7001, "context": "route-a/verify", "state": "success",
                "target_url": "https://github.com/o/r/actions/runs/1",
                "creator": {"login": "github-actions[bot]", "type": "Bot"}}])}
        client = prs.Client("o/r", "tok", api=self.api)
        with self.assertRaises(prs.Refused) as ctx:
            pc.verify_published_state(client, HEAD, "success", attempts=1,
                                      expect_run_id=self.RUN_ID)
        self.assertIn("run_id=1", str(ctx.exception))

    # ── 5. 传输异常 / 回读断连不得算成功 ──

    def test_compensation_post_disconnect_is_not_success(self):
        rc, doc = self._run({"drift_after_status_post": True,
                             "comp_post_disconnect_no_write": True})
        self.assertEqual(rc, 1, doc)
        ev = doc["evidence"]
        self.assertFalse(ev["status_compensation"]["ok"])
        self.assertTrue(ev["possible_success_evidence_remains"])
        self.assertIn("人工只读核验", ev["operator_action"])

    def test_compensation_readback_disconnect_is_not_success(self):
        rc, doc = self._run({"drift_after_status_post": True,
                             "status_read_disconnect_after": 2})
        self.assertEqual(rc, 1, doc)
        ev = doc["evidence"]
        self.assertFalse(ev["status_compensation"]["ok"])
        self.assertTrue(ev["possible_success_evidence_remains"])

    def test_compensation_readback_overwritten_to_success_is_not_success(self):
        rc, doc = self._run({"drift_after_status_post": True,
                             "check_readback_override_success_after": 2})
        self.assertEqual(rc, 1, doc)
        ev = doc["evidence"]
        self.assertFalse(ev["compensation"]["ok"])
        self.assertTrue(ev["possible_success_evidence_remains"])

    # ── 6. failure 状态绝不能变成 success ──

    def test_failure_state_with_drift_never_leaves_success(self):
        # state=failure 的 status POST 会被 stub 当作补偿（is_comp），改用 PATCH 后漂移
        rc, doc = self._run({"drift_after_patch": True}, state="failure")
        self.assertEqual(rc, 1, doc)
        Stub = prs_case.Stub
        self.assertFalse(any(c.get("conclusion") == "success" for c in Stub.remote_checks))
        self.assertFalse(any(s.get("state") == "success" for s in Stub.remote_statuses))


if __name__ == "__main__":
    unittest.main()
