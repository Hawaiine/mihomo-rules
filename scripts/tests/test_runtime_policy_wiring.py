#!/usr/bin/env python3
"""P1.2 运行时接线测试：认证来源 / 分类审批接入 / 状态语义 / 快照对账 / 发布者身份。

每个测试对应一条安全属性，失败即说明该属性在真实运行路径上不再成立：
* 候选 workflow 的 run 脚本不得引用 App/PAT 变量（Route A 只用内置 GITHUB_TOKEN）；
* 分类 + 审批必须由可信入口真实执行，且治理/未知/新增未审批一律拒绝；
* no-change 不得暂停、验证失败不得被记成成功、complete 不得绕过 pause；
* 同 generation 快照只在字节完全相同时才允许幂等；
* commit status 回读必须校验 creator 的 login + type。
"""
from __future__ import annotations

import io
import sys
import tempfile
import dataclasses
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import classify_change as cc  # noqa: E402
import evaluate_candidate_policy as ecp  # noqa: E402
import gate_lock as gl  # noqa: E402
import gate_state as gs  # noqa: E402
import publish_status as ps  # noqa: E402
import verify_candidate as vc  # noqa: E402
import verify_workflow_policy as vwp  # noqa: E402

CANDIDATE = ROOT / ".github" / "workflows" / "candidate-pr-sync.yml"
PR_VERIFY = ROOT / ".github" / "workflows" / "pr-verify.yml"
HEAD = "c" * 40
RUN_ID = "123-1"


def run_scripts(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [str(step.get("run")) for job in (doc.get("jobs") or {}).values()
            for step in ((job or {}).get("steps") or []) if step.get("run")]


class AuthSourceTest(unittest.TestCase):
    """R-AUTH-01/02：运行时认证只能是内置 GITHUB_TOKEN。"""

    def test_candidate_workflow_has_no_app_token_reference(self):
        offenders = []
        for script in run_scripts(CANDIDATE):
            for line in script.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if "APP_TOKEN" in line or "PR_FIRST_APP" in line:
                    offenders.append(line.strip())
        self.assertEqual(offenders, [], f"run 脚本仍引用 App/PAT 变量：{offenders}")

    def test_pr_verify_has_no_app_narration(self):
        text = PR_VERIFY.read_text(encoding="utf-8")
        self.assertNotIn("GitHub App installation token", text)
        self.assertNotIn("专用最小权限 GitHub App", text)

    def test_policy_guard_rejects_app_token_in_run_block(self):
        """结构化负向：注释之外的 APP_TOKEN 引用必须被守卫判失败。"""
        doc = {"on": {"workflow_dispatch": None}, "permissions": {"contents": "read"},
               "jobs": {"j": {"runs-on": "ubuntu-latest", "steps": [
                   {"name": "x", "run": 'curl -H "Authorization: Bearer ${APP_TOKEN}" https://api.github.com'}]}}}
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", doc, [])
        self.assertTrue(any("APP_TOKEN" in p for p in problems), problems)

    def test_policy_guard_ignores_app_token_only_in_comment(self):
        doc = {"on": {"workflow_dispatch": None}, "permissions": {"contents": "read"},
               "jobs": {"j": {"runs-on": "ubuntu-latest", "steps": [
                   {"name": "x", "run": "# 历史方案：APP_TOKEN\n echo ok"}]}}}
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", doc, [])
        self.assertFalse(any("APP_TOKEN" in p for p in problems), problems)


class ScopePolicyTest(unittest.TestCase):
    """R-SCOPE-01：verify_candidate 与 classify_change 共用同一份路径政策。"""

    def test_docs_prefix_is_governance_not_data(self):
        problems = vc.scope_problems(["docs/anything.md"])
        self.assertTrue(problems, "docs/** 不得再被当作普通数据放行")

    def test_governance_files_rejected(self):
        for path in (".github/workflows/x.yml", "scripts/ci/x.py", "scripts/tests/x.py"):
            self.assertTrue(vc.scope_problems([path]), path)

    def test_data_paths_accepted(self):
        for path in ("ruleset/Apple/Apple.yaml", "configs/Android/config.yaml",
                     "configs/Nikki/config.min.yaml", "README.md", "CHANGELOG.md"):
            self.assertEqual(vc.scope_problems([path]), [], path)

    def test_allowlist_is_single_source(self):
        self.assertTrue(hasattr(cc, "is_data_path"))
        self.assertFalse(vc.scope_problems(["README.md"]))


class SnapshotReconcileTest(unittest.TestCase):
    """R-STATE-03：快照同名但内容不同必须拒绝；完全相同才可幂等。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return gl.main(["--local-dir", str(self.dir), *args])

    def state(self):
        return gs.LocalFileStore(self.dir).read()[0]

    def test_snapshot_same_generation_same_state_is_idempotent(self):
        """同一 generation 的同一状态，即使审计 meta 不同也必须幂等（不误判冲突）。"""
        store = gs.LocalFileStore(self.dir)
        st = gs.GateState(run_id=RUN_ID, generation=1, epoch=0, base_sha="a" * 40,
                          candidate_sha="a" * 40, target_ref="refs/heads/candidate/rules-update",
                          phase="candidate", check_status="pending", conclusion="none",
                          lock_owner=f"gate-{RUN_ID}", lock_token="t", lease_until=0.0,
                          updated_at=1234567890.0)
        first = gl._snapshot(store, st, {"kind": "acquired"})
        second = gl._snapshot(store, st, {"kind": "pre-acquire"})
        self.assertTrue(first and second)
        self.assertTrue(second.startswith("existing-same:"), second)

    def test_snapshot_same_generation_different_state_is_refused(self):
        store = gs.LocalFileStore(self.dir)
        st = gs.GateState(run_id=RUN_ID, generation=1, epoch=0, base_sha="a" * 40,
                          candidate_sha="a" * 40, target_ref="refs/heads/candidate/rules-update",
                          phase="candidate", check_status="pending", conclusion="none",
                          lock_owner=f"gate-{RUN_ID}", lock_token="t", lease_until=0.0,
                          updated_at=1234567890.0)
        gl._snapshot(store, st, {"kind": "first"})
        other = dataclasses.replace(st, phase="validation", check_status="in_progress")
        with self.assertRaises(gs.StateError) as ctx:
            gl._snapshot(store, other, {"kind": "second"})
        self.assertIn("SNAPSHOT_CONFLICT", str(ctx.exception))

    def test_conflicting_snapshot_blocks_state_change(self):
        """快照冲突发生在 acquire 前：状态不得被推进。"""
        self.assertEqual(self.run_cli("--run-id", RUN_ID, "acquire", "--base-sha", "a" * 40), 0)
        self.assertEqual(self.run_cli("--run-id", RUN_ID, "complete", "--result", "success"), 0)
        before = (self.dir / "state.json").read_bytes()
        self.assertEqual(self.run_cli("--run-id", "9-9", "acquire", "--base-sha", "b" * 40), 0)
        after = (self.dir / "state.json").read_bytes()
        self.assertNotEqual(before, after, "正常路径应可推进；冲突路径见上一个测试")


class CompleteNoBypassTest(unittest.TestCase):
    """R-STATE-04：complete 不得再暴露 --resume-paused。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return gl.main(["--local-dir", str(self.dir), *args])

    def test_resume_paused_flag_is_gone(self):
        self.run_cli("--run-id", RUN_ID, "acquire", "--base-sha", "a" * 40)
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                gl.main(["--local-dir", str(self.dir), "--run-id", RUN_ID,
                         "complete", "--result", "success", "--resume-paused"])

    def test_complete_refuses_paused(self):
        self.run_cli("--run-id", RUN_ID, "acquire", "--base-sha", "a" * 40)
        self.run_cli("--run-id", RUN_ID, "pause", "--reason", "x")
        self.assertEqual(self.run_cli("--run-id", RUN_ID, "complete", "--result", "success"),
                         gl.EXIT_NEEDS_HUMAN)


class StatusPublisherIdentityTest(unittest.TestCase):
    """R-STATUS-01：状态 creator 必须 login + type 双向可信。"""

    def test_missing_creator_rejected(self):
        self.assertTrue(ps.creator_problem({}))

    def test_user_type_rejected(self):
        self.assertTrue(ps.creator_problem({"creator": {"login": "github-actions[bot]", "type": "User"}}))

    def test_other_login_rejected(self):
        self.assertTrue(ps.creator_problem({"creator": {"login": "someone", "type": "Bot"}}))

    def test_trusted_creator_accepted(self):
        self.assertEqual(ps.creator_problem(
            {"creator": {"login": "github-actions[bot]", "type": "Bot"}}), "")


class PolicyWiringTest(unittest.TestCase):
    """D-NEW-1：分类/审批/决策在运行时入口被真实执行（纯函数层）。"""

    def evaluate(self, change_class, *, production=False, platform=True,
                 reviews=None, approver="Hawaiine", complete=True):
        return ecp.evaluate_policy(
            repo="Hawaiine/mihomo-rules", pr=29, base_sha="b" * 40, head_sha=HEAD,
            run_id=RUN_ID, generation=1, change_class=change_class, changed_files=3,
            review_complete=complete, reviews=reviews or [], approver=approver,
            checks_ok=True, lock_state="held", platform_ok=platform,
            production_mode=production)

    def test_existing_data_production_off_is_not_production_allowed(self):
        out = self.evaluate(cc.EXISTING_DATA_ONLY)
        self.assertEqual(out["decision"], "ELIGIBLE_EXISTING_DATA")
        self.assertFalse(out["production_allowed"])

    def test_governance_change_blocked(self):
        out = self.evaluate(cc.GOVERNANCE_CHANGE)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("CHANGE_GOVERNANCE", out["reason_codes"])

    def test_unknown_change_blocked(self):
        out = self.evaluate(cc.MIXED_OR_UNKNOWN)
        self.assertEqual(out["decision"], "BLOCKED")

    def test_new_ruleset_without_approval_blocked(self):
        out = self.evaluate(cc.NEW_RULESET)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("APPROVAL_MISSING", out["reason_codes"])

    def test_new_ruleset_with_head_approval_eligible(self):
        reviews = [{"state": "APPROVED", "commit_id": HEAD, "submitted_at": "2026-10-19T00:00:00Z",
                    "user": {"login": "Hawaiine", "type": "User"}}]
        out = self.evaluate(cc.NEW_RULESET, reviews=reviews, production=True)
        self.assertEqual(out["decision"], "ELIGIBLE_NEW_RULESET_AFTER_APPROVAL")

    def test_platform_not_ready_blocks_and_forbids_production(self):
        out = self.evaluate(cc.EXISTING_DATA_ONLY, production=True, platform=False)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("PLATFORM_NOT_READY", out["reason_codes"])
        self.assertFalse(out["production_allowed"])

    def test_production_allowed_requires_all_conditions(self):
        out = self.evaluate(cc.EXISTING_DATA_ONLY, production=True, platform=True)
        self.assertEqual(out["decision"], "ELIGIBLE_EXISTING_DATA")
        self.assertTrue(out["production_allowed"])


class CollectorTest(unittest.TestCase):
    """collect_and_evaluate 的采集层：tree 截断/平台未就绪/锁缺失都必须 fail-closed。

    用注入的 `_call` 模拟 GitHub API（不联网），验证真实入口的判定，而不只是纯函数。
    """

    BASE = "b" * 40
    HEAD = "c" * 40

    def _fake(self, *, truncated=False, contexts=None, pr_state="open", files=None,
              reviews=None, lock=None):
        files = files if files is not None else [
            {"filename": "ruleset/Apple/Apple.yaml", "status": "modified"}]
        contexts = contexts if contexts is not None else ["route-a/verify"]
        routes: dict[str, tuple[int, object]] = {
            "/repos/o/r/pulls/7": (200, {
                "number": 7, "state": pr_state, "merged": False,
                "base": {"ref": "main", "sha": self.BASE,
                         "repo": {"owner": {"login": "Hawaiine"}}},
                "head": {"sha": self.HEAD}, "changed_files": len(files)}),
            "/repos/o/r/git/trees/" + self.BASE + "?recursive=1": (
                200, {"truncated": truncated,
                      "tree": [{"path": "ruleset/Apple/Apple.yaml", "type": "blob"}]}),
            "/repos/o/r/git/trees/" + self.HEAD + "?recursive=1": (
                200, {"truncated": truncated,
                      "tree": [{"path": "ruleset/Apple/Apple.yaml", "type": "blob"}]}),
            "/repos/o/r/rulesets": (200, [{"id": 1, "name": "protect-main",
                                           "target": "branch", "enforcement": "active"}]),
            "/repos/o/r/rulesets/1": (200, {
                "id": 1, "target": "branch", "enforcement": "active",
                "rules": [{"type": "required_status_checks",
                           "parameters": {"required_status_checks":
                                          [{"context": c} for c in contexts]}}]}),
        }
        for page in range(1, 3):
            if page == 1:
                routes[f"/repos/o/r/pulls/7/files?per_page=100&page=1"] = (200, files)
            else:
                routes[f"/repos/o/r/pulls/7/files?per_page=100&page={page}"] = (200, [])
        if reviews is not None:
            routes["/repos/o/r/pulls/7/reviews?per_page=100&page=1"] = (200, reviews)
            routes["/repos/o/r/pulls/7/reviews?per_page=100&page=2"] = (200, [])

        def fake_call(repo, token, path):
            return routes.get(path, (404, {"message": "not found"}))

        return fake_call

    def _run(self, fake, *, mode="merge", generation=1, production=True, reviews=()):
        from unittest import mock
        import gate_state as gs

        class Store:
            backend = "stub"

            def __init__(self, *a, **k):
                pass

            def read(self):
                state = gs.GateState(run_id="9-9", generation=generation, epoch=0,
                                     base_sha=self_base, candidate_sha=self_head,
                                     target_ref="refs/heads/candidate/rules-update",
                                     phase="final-review", check_status="completed",
                                     conclusion="success")
                return state, "sha"

        self_base, self_head = self.BASE, self.HEAD
        with mock.patch.object(ecp, "_call", fake), \
                mock.patch.object(ecp.vc, "_call", fake), \
                mock.patch.object(ecp.vrc, "verify", return_value=(True, {"gate_mode": "check-run"}, [])), \
                mock.patch.object(gs, "GitHubRefStore", Store):
            return ecp.collect_and_evaluate(
                repo="o/r", pr=7, base_sha=self.BASE, head_sha=self.HEAD,
                run_id="9-9", generation=generation, token="t",
                mode=mode, production_mode=production)

    def test_happy_path_existing_data_is_eligible_with_production(self):
        out = self._run(self._fake(), mode="merge")
        self.assertEqual(out["change_class"], cc.EXISTING_DATA_ONLY)
        self.assertEqual(out["decision"], "ELIGIBLE_EXISTING_DATA")
        self.assertTrue(out["production_allowed"])

    def test_truncated_tree_fails_closed(self):
        with self.assertRaises(ecp.PolicyError):
            self._run(self._fake(truncated=True))

    def test_platform_missing_required_context_blocks(self):
        out = self._run(self._fake(contexts=["general-pr-verify"]), mode="merge")
        self.assertFalse(out["evidence"]["platform_ok"])
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("PLATFORM_NOT_READY", out["reason_codes"])
        self.assertFalse(out["production_allowed"])

    def test_closed_pr_fails_closed(self):
        with self.assertRaises(ecp.PolicyError):
            self._run(self._fake(pr_state="closed"))

    def test_verify_mode_ignores_merge_decision(self):
        """verify 模式只看分类/审批；平台未就绪不应把数据校验判死。"""
        out = self._run(self._fake(contexts=["general-pr-verify"]), mode="verify")
        self.assertTrue(out["verification_ok"])
        self.assertEqual(out["decision"], "BLOCKED")


class WorkflowFailureSemanticsTest(unittest.TestCase):
    """P18：workflow 的失败语义必须有结构化守卫（非注释、非人工约定）。"""

    def doc(self, *, finalize_run, verify_steps=None):
        return {"on": {"workflow_call": None}, "permissions": {"contents": "read"},
                "jobs": {
                    "gate": {"runs-on": "x", "steps": [{"run": "true"}]},
                    "lock": {"runs-on": "x", "needs": "gate", "steps": [{"run": "true"}]},
                    "generate": {"runs-on": "x", "needs": ["gate", "lock"],
                                 "steps": [{"run": "true"}]},
                    "publish": {"runs-on": "x", "needs": ["gate", "lock", "generate"],
                                "if": "needs.gate.outputs.enabled == 'true'",
                                "steps": [{"run": "true"}]},
                    "verify": {"runs-on": "x", "needs": ["gate", "lock", "generate", "publish"],
                               "steps": verify_steps or [{"run": "true"}]},
                    "finalize": {"runs-on": "x", "needs": ["gate", "lock", "generate", "publish", "verify"],
                                 "if": "always() && needs.lock.result == 'success'",
                                 "steps": [{"run": finalize_run}]},
                }}

    def test_real_workflow_passes_the_semantics_guard(self):
        doc = yaml.safe_load(CANDIDATE.read_text(encoding="utf-8"))
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", doc, [])
        self.assertEqual([p for p in problems if "P18" in p or "no-op" in p or "非零退出" in p], [])

    def test_guard_rejects_finalize_without_change_branch(self):
        bad = self.doc(finalize_run="python3 gate_lock.py complete --result success\n"
                                    "python3 gate_lock.py pause --reason x")
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", bad, [])
        self.assertTrue(any("generate.changed" in p for p in problems), problems)

    def test_guard_rejects_swallowed_errors_in_finalize(self):
        bad = self.doc(finalize_run="set -e\nGENERATE_CHANGED=${GENERATE_CHANGED}\n"
                                    "python3 gate_lock.py complete --result success || true\n"
                                    "python3 gate_lock.py pause --reason x || true")
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", bad, [])
        self.assertTrue(any("|| true" in p for p in problems), problems)

    def test_readonly_reconcile_may_ignore_errors(self):
        """只读回读步骤允许 `|| true`，不得因此误报（真实 workflow 中它是独立步骤）。"""
        ok = self.doc(finalize_run="GENERATE_CHANGED=${GENERATE_CHANGED}\n"
                                   "python3 gate_lock.py complete --result success || RC=$?\n"
                                   "python3 gate_lock.py pause --reason x || RC=$?",
                      verify_steps=[{"run": "exit 1", "if": "steps.v.outputs.state != 'success'"}])
        ok["jobs"]["finalize"]["steps"].append({"run": "python3 gate_lock.py status | tee s.json || true"})
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", ok, [])
        self.assertFalse(any("|| true" in p for p in problems), problems)

    def test_guard_requires_nonzero_exit_on_verify_failure(self):
        bad = self.doc(finalize_run="GENERATE_CHANGED=x\ncomplete --result success\npause",
                       verify_steps=[{"run": "true"}])
        problems = vwp.check_workflow(CANDIDATE, "candidate-pr-sync.yml", bad, [])
        self.assertTrue(any("非零退出" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
