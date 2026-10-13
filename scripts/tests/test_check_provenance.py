#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix A：必需检查**来源/身份（provenance）**认证的真实反向测试。

这些测试调用 `check_provenance` 的**实际判定函数**（不是对验收 JSON 做字符串判断），
覆盖规格 §3.3 的五个场景：
  1. 真实必需检查为 failure，同 SHA、同 App ID 的**另一个 workflow** 提供较新的同名 success
     ⇒ 必须判失败并禁止合并；
  2. 同名成功检查仅来自另一 workflow，而预期 workflow 的检查缺失 ⇒ 拒绝；
  3. workflow path / event / head SHA / job 映射 任一错误 ⇒ 失败关闭；
  4. manifest 的 workflow/job 身份被删除、篡改或降级 ⇒ 失败关闭；
  5. 合法的同一预期 workflow/job 重跑取最新真实 run；旧失败只能在**通过来源认证之后**
     记为 superseded，不得被其它 workflow 的检查「超越」。

**mock 逻辑测试，不代表真实 GitHub 平台端到端成功。**
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import check_provenance as cp  # noqa: E402
import prov_fixture as fx  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")


def manifest():
    return cp.load_manifest(MANIFEST)


def select(check_runs, workflow_runs=None, jobs=None, head=fx.HEAD, pr_head=fx.HEAD):
    return cp.select_checks(check_runs=check_runs, manifest=manifest(),
                            workflow_runs=workflow_runs if workflow_runs is not None else [fx.workflow_run()],
                            jobs_by_id=fx.jobs_index(jobs if jobs is not None else fx.all_jobs()),
                            expected_head=head, pr_head=pr_head)


class ProvenanceAcceptanceTest(unittest.TestCase):
    def test_real_platform_shape_is_accepted(self):
        per, problems = select(fx.all_checks())
        self.assertEqual(problems, [])
        self.assertEqual(cp.overall_state(per), "success")
        for name in fx.NAMES:
            self.assertEqual(per[name]["state"], "success")
            self.assertEqual(per[name]["workflow_run_id"], fx.RUN_ID)
            self.assertEqual(per[name]["job_id"], fx.JOB_IDS[name])

    def test_wrong_app_id_still_rejected(self):
        checks = [fx.check_run("general-pr-verify", app={"slug": "github-actions", "id": 99999})]
        per, problems = select(checks)
        self.assertEqual(per["general-pr-verify"]["state"], "unverified")
        self.assertTrue(any("来源 App 不符" in p for p in problems), problems)


class FakeSameNameOverrideTest(unittest.TestCase):
    """核心：另一个 workflow 的同名成功检查**不得覆盖**真实必需检查的失败。"""

    def _scenario(self):
        # 真实必需检查 = failure（来自 pr-verify.yml，已验证身份）
        real = [fx.check_run("general-pr-verify", "failure", started="2026-10-13T01:00:00Z")]
        # 另一个 workflow 提供**较新**的同名 success（同 SHA、同 App ID、job 记录自洽）
        fake = [fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242,
                             started="2026-10-13T02:00:00Z")]
        return real, fake

    def _api(self, real, fake):
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        return fx.FakeApi(check_runs_seq=[real + fake], workflow_runs=runs,
                          jobs_by_run={fx.RUN_ID: fx.jobs_for(real),
                                       fx.OTHER_RUN_ID: fx.jobs_for(fake)})

    def test_newer_fake_success_from_other_workflow_does_not_override_failure(self):
        real, fake = self._scenario()
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        per, problems = select(real + fake, runs, fx.jobs_for(real) + fx.jobs_for(fake))
        self.assertEqual(per["general-pr-verify"]["state"], "not-success")
        self.assertEqual(per["general-pr-verify"]["conclusion"], "failure")
        self.assertEqual(cp.overall_state(per), "failed")
        # 伪造 run 必须出现在 rejected 里，且**不能**出现在 superseded 里
        self.assertTrue(per["general-pr-verify"]["rejected"], per["general-pr-verify"])
        self.assertEqual(per["general-pr-verify"].get("superseded_verified_runs"), [])

    def test_fake_success_only_no_real_check_is_rejected(self):
        _, fake = self._scenario()
        runs = [fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH, suite=fx.OTHER_SUITE)]
        per, problems = select(fake, runs, fx.jobs_for(fake))
        self.assertEqual(per["general-pr-verify"]["state"], "unverified")
        self.assertEqual(cp.overall_state(per), "failed")
        self.assertTrue(any("身份认证失败" in p for p in problems), problems)

    def test_await_and_verify_share_same_verdict_on_fake_override(self):
        """await_checks 与 verify_required_checks 必须给出同一判定（无口径漂移）。"""
        import await_checks as ac
        import verify_required_checks as vrc
        real, fake = self._scenario()
        api = self._api(real, fake)
        clock = fx.FakeClock()
        report = ac.Poller(repo="o/r", pr=29, expected_head=fx.HEAD, base_branch="main",
                           required=manifest(), token="x", timeout=5.0, interval=1.0, max_interval=2.0,
                           fetcher=api, sleeper=clock.sleep, clock=clock,
                           log=lambda *a, **k: None).run()
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        ok, rep, problems = vrc.verify(repo="o/r", pr=29, expected_head=fx.HEAD, token="x",
                                       required_manifest=MANIFEST, getter=self._api(real, fake),
                                       log=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertTrue(any("身份认证失败" in p or "结论为" in p for p in problems), problems)


class ProvenanceFieldRejectionTest(unittest.TestCase):
    def _one(self, check=None, runs=None, jobs=None):
        cr = check if check is not None else fx.check_run("general-pr-verify")
        per, _ = select([cr], runs, jobs)
        return per["general-pr-verify"]

    def test_wrong_workflow_path(self):
        info = self._one(runs=[fx.workflow_run(path=fx.OTHER_WORKFLOW_PATH)])
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("workflow run 路径不符" in p for p in info["rejected"][0]["problems"]))

    def test_wrong_event(self):
        info = self._one(runs=[fx.workflow_run(event="workflow_dispatch")])
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("事件不是" in p for p in info["rejected"][0]["problems"]))

    def test_wrong_workflow_head_sha(self):
        info = self._one(runs=[fx.workflow_run(sha=fx.OTHER_SHA)])
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("head SHA 不符" in p for p in info["rejected"][0]["problems"]))

    def test_check_run_on_other_sha_is_not_accepted(self):
        info = self._one(check=fx.check_run("general-pr-verify", sha=fx.OTHER_SHA))
        self.assertEqual(info["state"], "unverified")

    def test_missing_job_mapping(self):
        info = self._one(jobs=[])
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("无法映射到该 workflow run 的 job" in p
                            for p in info["rejected"][0]["problems"]))

    def test_job_name_mismatch(self):
        jobs = [{"id": fx.JOB_IDS["general-pr-verify"], "name": "something-else",
                 "head_sha": fx.HEAD, "status": "completed", "conclusion": "success"}]
        info = self._one(jobs=jobs)
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("job 身份不符" in p for p in info["rejected"][0]["problems"]))

    def test_missing_check_suite_id(self):
        check = fx.check_run("general-pr-verify")
        check.pop("check_suite")
        info = self._one(check=check)
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("check_suite.id" in p for p in info["rejected"][0]["problems"]))

    def test_unmapped_suite(self):
        info = self._one(runs=[fx.workflow_run(suite=fx.OTHER_SUITE)])
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("无法映射到该 head SHA 上的 workflow run" in p
                            for p in info["rejected"][0]["problems"]))

    def test_job_and_check_conclusion_disagreement(self):
        jobs = [{"id": fx.JOB_IDS["general-pr-verify"], "name": "general-pr-verify",
                 "head_sha": fx.HEAD, "status": "completed", "conclusion": "failure"}]
        info = self._one(jobs=jobs)
        self.assertEqual(info["state"], "unverified")
        self.assertTrue(any("结论不一致" in p for p in info["rejected"][0]["problems"]))


class ManifestIdentityTest(unittest.TestCase):
    def _write(self, obj):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(obj, fh)
            return fh.name

    def test_legacy_required_list_is_rejected(self):
        path = self._write({"required": ["general-pr-verify"]})
        with self.assertRaises(cp.ManifestError):
            cp.load_manifest(path)

    def test_missing_job_id_is_rejected(self):
        path = self._write({"checks": [{"name": "general-pr-verify",
                                        "workflow": fx.PR_VERIFY_PATH,
                                        "source": {"app_slug": "github-actions", "app_id": 15368}}]})
        with self.assertRaises(cp.ManifestError):
            cp.load_manifest(path)

    def test_missing_source_is_rejected(self):
        path = self._write({"checks": [{"name": "general-pr-verify", "job_id": "general-pr-verify",
                                        "workflow": fx.PR_VERIFY_PATH}]})
        with self.assertRaises(cp.ManifestError):
            cp.load_manifest(path)

    def test_wrong_workflow_path_is_rejected(self):
        path = self._write({"checks": [{"name": "general-pr-verify", "job_id": "general-pr-verify",
                                        "workflow": ".github/workflows/other.yml",
                                        "source": {"app_slug": "github-actions", "app_id": 15368}}]})
        with self.assertRaises(cp.ManifestError):
            cp.load_manifest(path)

    def test_empty_checks_is_rejected(self):
        with self.assertRaises(cp.ManifestError):
            cp.load_manifest(self._write({"checks": []}))

    def test_real_manifest_declares_identity(self):
        for entry in manifest():
            self.assertEqual(entry["workflow"], fx.PR_VERIFY_PATH)
            self.assertEqual(entry["job_id"], entry["name"])
            self.assertEqual(entry["app_slug"], "github-actions")
            self.assertEqual(entry["app_id"], 15368)


class RerunAndSupersedeTest(unittest.TestCase):
    def test_verified_rerun_uses_latest_and_records_superseded(self):
        checks = [fx.check_run("general-pr-verify", "failure", rid=fx.JOB_IDS["general-pr-verify"],
                               started="2026-10-13T01:00:00Z")]
        for name in fx.NAMES[1:]:
            checks.append(fx.check_run(name))
        # 同一 workflow/job 的合法重跑：新的一次尝试是**新的 job id**、同一 suite、较新的 success
        rerun = fx.check_run("general-pr-verify", "success", rid=115069523999,
                             started="2026-10-13T03:00:00Z")
        per, problems = select(checks + [rerun], jobs=fx.jobs_for(checks) + fx.jobs_for([rerun]))
        self.assertEqual(problems, [])
        self.assertEqual(per["general-pr-verify"]["state"], "success")
        self.assertEqual(len(per["general-pr-verify"]["superseded_verified_runs"]), 1)
        self.assertEqual(per["general-pr-verify"]["superseded_verified_runs"][0]["conclusion"], "failure")

    def test_unverified_run_cannot_supersede_verified_failure(self):
        """未经来源认证的旧/新 run 都不得参与 superseded 记录。"""
        real = fx.check_run("general-pr-verify", "failure", started="2026-10-13T05:00:00Z")
        fake_old = fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=777,
                                started="2026-10-13T01:00:00Z")
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        per, _ = select([real, fake_old], runs, fx.jobs_for([real, fake_old]))
        self.assertEqual(per["general-pr-verify"]["state"], "not-success")
        self.assertEqual(per["general-pr-verify"]["superseded_verified_runs"], [])

    def test_pending_when_same_name_run_incomplete(self):
        checks = [fx.check_run("general-pr-verify", None, status="in_progress")]
        per, _ = select(checks)
        self.assertEqual(per["general-pr-verify"]["state"], "in-progress")
        self.assertEqual(cp.overall_state(per), "pending")


if __name__ == "__main__":
    unittest.main()
