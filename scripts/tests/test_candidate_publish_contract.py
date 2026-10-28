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
from urllib.parse import parse_qs, urlparse

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
                                      expect_status_id=7001,
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


class _TamperingClient(prs.Client):
    """只篡改回读端点的 status id（模拟并发覆盖 / 历史状态冒充），其余逻辑不动。"""

    def call(self, path):
        code, body = super().call(path)
        if "/statuses" in path and isinstance(body, list):
            out = []
            for item in body:
                if (isinstance(item, dict)
                        and str(item.get("context", "")).casefold() == "route-a/verify"):
                    out.append({**item, "id": 8999})
                else:
                    out.append(item)
            body = out
        return code, body


class ObjectLevelStatusReadbackTest(unittest.TestCase):
    """Round 11 P0：commit status 必须做**对象 ID 级**回读。

    同一 Actions run 的不同 attempt 共享同一个数字 run id 与 URL，所以"URL 绑定正确"
    不能证明回读对象就是本次 POST 创建的那一条；必须比对 POST 返回的 status id。
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
        self._helper.flow()

    def _publish(self, client_cls=None, state="success"):
        old = pc.prs.Client
        if client_cls is not None:
            pc.prs.Client = client_cls
        try:
            return pc.publish_candidate_result(
                repo="o/r", pr=29, head_sha=HEAD, state=state, token="tok",
                run_id=self.RUN_ID, run_attempt=self.ATTEMPT, api=self.api,
                target_url=f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
                attempts=1)
        finally:
            pc.prs.Client = old

    # ── 1. 正常：POST id == 回读 id，才允许通过 ──

    def test_post_id_equals_readback_id_and_all_fields_match(self):
        rep = self._publish()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["status"]["id"], 9001)
        self.assertEqual(rep["verify"]["id"], 9001)
        self.assertTrue(rep["verify"]["verified"])
        self.assertEqual(rep["verify"]["state"], "success")
        self.assertEqual(rep["verify"]["creator"], "github-actions[bot]")
        self.assertEqual(rep["verify"]["creator_type"], "Bot")
        self.assertEqual(rep["verify"]["target_url"],
                         f"https://github.com/o/r/actions/runs/{self.RUN_ID}")
        # attempt-aware 绑定与数字 URL 绑定仍分别成立
        self.assertEqual(rep["check_run"]["external_id"],
                         f"route-a-run:{self.RUN_ID}-{self.ATTEMPT}")
        self.assertEqual(rep["run"]["url_run_id"], self.RUN_ID)

    # ── 2/3. 回读 id 不符（并发覆盖 / 历史状态冒充）必须失败 ──

    def test_readback_id_mismatch_refused_even_if_creator_state_url_match(self):
        with self.assertRaises(prs.Refused) as ctx:
            self._publish(client_cls=_TamperingClient)
        msg = str(ctx.exception)
        self.assertIn("非本次 POST 对象", msg)
        self.assertIn("8999", msg)

    def test_readback_id_mismatch_sets_risk_and_operator_action(self):
        with self.assertRaises(prs.Refused) as ctx:
            self._publish(client_cls=_TamperingClient)
        rep = ctx.exception.report
        self.assertFalse(rep["ok"])
        self.assertTrue(rep["status_publish_attempted"])
        # 失败后两路补偿都必须尝试；本用例里补偿读回同样被篡改 → 不得宣称已撤销
        self.assertIn("compensation_log", rep)
        self.assertTrue(rep["possible_success_evidence_remains"],
                        "status 撤销未确认时必须 fail-closed")
        self.assertIn("人工只读核验", rep["operator_action"])
        self.assertNotEqual(rep["operator_action"], "无需人工核验")

    # ── 4. 回读缺失 / 分页失败 / 畸形 / 断连都不得误报成功 ──

    def _verify(self, expect_id=9001, attempts=1):
        client = prs.Client("o/r", "tok", api=self.api)
        return pc.verify_published_state(client, HEAD, "success", attempts=attempts,
                                         expect_status_id=expect_id,
                                         expect_run_id=self.RUN_ID)

    def test_missing_target_object_refused(self):
        prs_case.Stub.inject["status_read"] = "hide"
        with self.assertRaises(prs.Refused) as ctx:
            self._verify()
        self.assertIn("未找到", str(ctx.exception))

    def test_page_two_unreadable_refused(self):
        prs_case.Stub.pages = {
            1: [{"id": 8000 + i, "context": "other/check", "state": "success",
                 "target_url": f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
                 "creator": {"login": "github-actions[bot]", "type": "Bot"}}
                for i in range(100)]}
        prs_case.Stub.inject["page_http"] = {2: 403}
        with self.assertRaises(prs.Refused) as ctx:
            self._verify()
        self.assertIn("第 2 页", str(ctx.exception))

    def test_malformed_page_refused(self):
        prs_case.Stub.pages = {1: [{"id": 1, "context": "other/check", "state": "success"},
                                   "not-a-dict"]}
        with self.assertRaises(prs.Refused) as ctx:
            self._verify()
        self.assertIn("畸形", str(ctx.exception))

    def test_readback_disconnect_refused(self):
        prs_case.Stub.inject["status_read_disconnect_after"] = 0
        with self.assertRaises(prs.Refused):
            self._verify()

    def test_post_without_id_refused(self):
        prs_case.Stub.stateful = False
        prs_case.Stub.routes = {
            f"POST /repos/o/r/statuses/{HEAD}": (201, {"state": "success"})}
        client = prs.Client("o/r", "tok", api=self.api)
        with self.assertRaises(prs.Refused) as ctx:
            pc.publish_status_state(client, HEAD, "success", "x",
                                    f"https://github.com/o/r/actions/runs/{self.RUN_ID}")
        self.assertIn("缺少合法 id", str(ctx.exception))

    # ── 5. POST 成功但回读仍是历史状态 ──

    def test_post_ok_but_readback_is_historical_state(self):
        prs_case.Stub.pages = {1: [{
            "id": 7777, "context": "route-a/verify", "state": "success",
            "target_url": f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
            "creator": {"login": "github-actions[bot]", "type": "Bot"}}]}
        with self.assertRaises(prs.Refused) as ctx:
            self._verify()
        self.assertIn("7777", str(ctx.exception))

    # ── 6. 补偿也必须校验 POST id 与回读 id 一致 ──

    def test_compensation_status_verifies_post_object_id(self):
        """补偿 POST 成功但回读到的 failure 不是本次对象（URL 正确、ID 被篡改）→ 未确认。"""
        with self.assertRaises(prs.Refused) as ctx:
            self._publish(client_cls=_TamperingClient)
        rep = ctx.exception.report
        comp = rep["status_compensation"]
        self.assertFalse(comp["ok"], comp)
        self.assertIn("并非本次补偿 POST 对象", comp["error"])
        self.assertTrue(rep["possible_success_evidence_remains"])

    # ── 9/10. failure 绝不报 success，风险字段与处置指引成立 ──

    def test_failure_state_never_reported_as_success(self):
        rep = self._publish(state="failure")
        self.assertEqual(rep["state"], "failure")
        self.assertEqual(rep["verify"]["state"], "failure")
        self.assertFalse(any(c.get("conclusion") == "success"
                             for c in prs_case.Stub.remote_checks))
        self.assertFalse(any(s.get("state") == "success"
                             for s in prs_case.Stub.remote_statuses))
        self.assertFalse(rep["possible_success_evidence_remains"])
        self.assertEqual(rep["operator_action"], "无需人工核验")


class _CrossPageRaceClient(prs.Client):
    """第 1 页被重读时注入一条更新的 route-a/verify（模拟跨页扫描期间的并发写入）。"""

    NEWER_ID = 9100

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.page1_reads = 0

    def call(self, path):
        code, body = super().call(path)
        query = parse_qs(urlparse(path).query)          # 不能用 "page=1" 子串匹配：
        if "statuses" in path and query.get("page") == ["1"]:   # per_page=100 也含 page=1
            self.page1_reads += 1
            if self.page1_reads >= 2 and isinstance(body, list):
                newer = {"id": self.NEWER_ID, "context": "route-a/verify", "state": "success",
                         "target_url": "https://github.com/o/r/actions/runs/9",
                         "creator": {"login": "github-actions[bot]", "type": "Bot"}}
                return code, [newer] + body
        return code, body


class PaginationAndIdentityEdgeCaseTest(unittest.TestCase):
    """Round 12 复审补齐：分页边界、对象选择、creator 身份、POST id 类型。

    这些用例覆盖的是「证据不足时不得宣称成功」的边界，而不是顺序成功路径：

      * 目标在第 1 页满页 / 第 2 页 / 完整扫完不存在 / 达分页上限；
      * 同 SHA 上存在**多个** route-a/verify 时只允许采信最新一条，不得回退到旧对象；
      * 第 2 页命中但第 1 页已出现更新的并发状态 ⇒ 证据不可用（跨页无原子快照）；
      * creator 的 login 与 type 必须双向匹配；POST 返回的 id 必须是正整数。
    """

    RUN_ID = "40611534778"

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
        prs_case.Stub.stateful = True      # 启用分页/有状态分支（pages 只在 stateful 下生效）
        self.client = prs.Client("o/r", "tok", api=self.api)

    # ── 工具 ──

    @staticmethod
    def _other(i: int) -> dict:
        return {"id": 8000 + i, "context": "other/check", "state": "success",
                "target_url": "https://github.com/o/r/actions/runs/9",
                "creator": {"login": "github-actions[bot]", "type": "Bot"}}

    def _target(self, sid: int = 9001, **over) -> dict:
        obj = {"id": sid, "context": "route-a/verify", "state": "success",
               "target_url": f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
               "creator": {"login": "github-actions[bot]", "type": "Bot"}}
        obj.update(over)
        return obj

    # ── 分页边界 ──

    def test_target_on_first_full_page_found(self):
        prs_case.Stub.pages = {1: [self._target()] + [self._other(i) for i in range(99)]}
        found, err = pc._scan_route_a_statuses(self.client, HEAD)
        self.assertIsNone(err)
        self.assertEqual(found["id"], 9001)

    def test_target_on_second_page_found(self):
        prs_case.Stub.pages = {1: [self._other(i) for i in range(100)],
                               2: [self._target()]}
        found, err = pc._scan_route_a_statuses(self.client, HEAD)
        self.assertIsNone(err, err)
        self.assertEqual(found["id"], 9001)

    def test_second_page_match_with_concurrent_newer_status_is_evidence_unavailable(self):
        """跨页扫描期间第 1 页出现更新的 route-a/verify ⇒ 无法证明对象仍是最新。"""
        prs_case.Stub.pages = {1: [self._other(i) for i in range(100)],
                               2: [self._target()]}
        client = _CrossPageRaceClient("o/r", "tok", api=self.api)
        found, err = pc._scan_route_a_statuses(client, HEAD)
        self.assertIsNone(found, "并发更新时不得返回任何对象")
        self.assertIn("并发更新", err or "")
        # 端到端：verify_published_state 必须拒绝，而不是宣称本次写入已验证
        with self.assertRaises(prs.Refused) as ctx:
            pc.verify_published_state(_CrossPageRaceClient("o/r", "tok", api=self.api), HEAD,
                                      "success", attempts=1, expect_status_id=9001,
                                      expect_run_id=self.RUN_ID)
        self.assertIn("并发更新", str(ctx.exception))

    def test_pagination_cap_is_evidence_unavailable(self):
        prs_case.Stub.pages = {1: [self._other(i) for i in range(100)],
                               2: [self._other(i) for i in range(100, 200)]}
        found, err = pc._scan_route_a_statuses(self.client, HEAD, max_pages=2)
        self.assertIsNone(found)
        self.assertIn("分页上限", err or "")

    def test_complete_scan_absence_is_not_an_error(self):
        prs_case.Stub.pages = {1: [self._other(i) for i in range(3)]}
        found, err = pc._scan_route_a_statuses(self.client, HEAD)
        self.assertIsNone(found)
        self.assertIsNone(err, "完整扫完确认不存在不是『证据不可用』")

    # ── 多个匹配对象：只认最新，不回退 ──

    def test_multiple_matches_only_newest_is_accepted(self):
        prs_case.Stub.pages = {1: [self._target(9001), self._target(8000)]}
        found, err = pc._scan_route_a_statuses(self.client, HEAD)
        self.assertIsNone(err)
        self.assertEqual(found["id"], 9001, "必须返回最新一条（GitHub 状态列表最新在前）")
        ok = pc.verify_published_state(self.client, HEAD, "success", attempts=1,
                                       expect_status_id=9001, expect_run_id=self.RUN_ID)
        self.assertTrue(ok["verified"])
        # 期望 id 是旧对象时，绝不能因为「同为 route-a/verify 且 URL 相同」而采信
        with self.assertRaises(prs.Refused) as ctx:
            pc.verify_published_state(self.client, HEAD, "success", attempts=1,
                                      expect_status_id=8000, expect_run_id=self.RUN_ID)
        self.assertIn("非本次 POST 对象", str(ctx.exception))

    # ── creator 身份双向校验 ──

    def test_creator_login_mismatch_refused(self):
        prs_case.Stub.pages = {1: [self._target(
            creator={"login": "mallory", "type": "Bot"})]}
        with self.assertRaises(prs.Refused) as ctx:
            pc.verify_published_state(self.client, HEAD, "success", attempts=1,
                                      expect_status_id=9001, expect_run_id=self.RUN_ID)
        self.assertIn("不在可信名单", str(ctx.exception))

    def test_creator_type_mismatch_refused(self):
        prs_case.Stub.pages = {1: [self._target(
            creator={"login": "github-actions[bot]", "type": "User"})]}
        with self.assertRaises(prs.Refused) as ctx:
            pc.verify_published_state(self.client, HEAD, "success", attempts=1,
                                      expect_status_id=9001, expect_run_id=self.RUN_ID)
        self.assertIn("非 Bot", str(ctx.exception))

    # ── POST 返回 id 的类型/取值 ──

    def test_post_id_wrong_type_or_non_positive_refused(self):
        prs_case.Stub.stateful = False
        for bad in ({"id": "9001"}, {"id": 0}, {"id": -7}, {"id": None}, {}):
            with self.subTest(payload=bad):
                prs_case.Stub.routes = {f"POST /repos/o/r/statuses/{HEAD}": (201, bad)}
                client = prs.Client("o/r", "tok", api=self.api)
                with self.assertRaises(prs.Refused) as ctx:
                    pc.publish_status_state(client, HEAD, "success", "x",
                                            f"https://github.com/o/r/actions/runs/{self.RUN_ID}")
                self.assertIn("id", str(ctx.exception))

    # ── 同一 run 的 attempt 递增：旧 success 先撤销，新对象才是证据 ──

    def test_previous_attempt_success_revoked_before_new_attempt(self):
        self._helper.flow()
        # 上一 attempt（-1）留下的 success 仍在远端
        prs_case.Stub.remote_statuses = [{
            "id": 8000, "context": "route-a/verify", "state": "success",
            "target_url": f"https://github.com/o/r/actions/runs/{self.RUN_ID}",
            "creator": {"login": "github-actions[bot]", "type": "Bot"}}]
        rep = pc.publish_candidate_result(
            repo="o/r", pr=29, head_sha=HEAD, state="success", token="tok",
            run_id=self.RUN_ID, run_attempt="2", api=self.api,
            target_url=f"https://github.com/o/r/actions/runs/{self.RUN_ID}", attempts=1)
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["historical_success_observed"])
        self.assertTrue(rep["historical_success_revoked"])
        self.assertEqual(rep["check_run"]["external_id"], f"route-a-run:{self.RUN_ID}-2")
        self.assertNotEqual(rep["status"]["id"], 8000)
        self.assertEqual(rep["verify"]["id"], rep["status"]["id"])
        self.assertEqual(prs_case.Stub.remote_statuses[0]["id"], rep["status"]["id"])


if __name__ == "__main__":
    unittest.main()
