#!/usr/bin/env python3
"""生产前置检查器（verify_production_prerequisites）的负向/正向测试。

安全属性：**任何一项不可证明，都不能 READY**；检查器本身永远只读
（`writes_performed=false`），且 `writes_performed` 不依赖调用方自觉。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import gate_state as gs  # noqa: E402
import verify_production_prerequisites as vp  # noqa: E402

BASE = "a" * 40
CAND = "b" * 40


def state_doc(*, generation=2, epoch=0, phase="final-review",
              check_status="completed", conclusion="success"):
    doc = {"schema_version": 1, "run_id": "100-1", "generation": generation, "epoch": epoch,
           "base_sha": BASE, "candidate_sha": CAND, "target_ref": "refs/heads/main",
           "phase": phase, "check_status": check_status, "conclusion": conclusion,
           "lock_owner": "", "lock_token": "", "lease_until": 0.0,
           "snapshot_digest": "", "updated_at": 0.0}
    doc["snapshot_digest"] = gs.canonical_digest(doc)
    return doc


def ruleset_body(*, include=("~DEFAULT_BRANCH",), exclude=(), enforcement="active",
                 target="branch", contexts=(("route-a/verify", 15368),)):
    return {"id": 1, "name": "protect-main", "target": target, "enforcement": enforcement,
            "conditions": {"ref_name": {"include": list(include), "exclude": list(exclude)}},
            "rules": [{"type": "required_status_checks",
                       "parameters": {"required_status_checks":
                                      [{"context": c, "integration_id": i} for c, i in contexts]}}]}


class World:
    """一套可控的假平台：按 path 返回 (status, body)。"""

    def __init__(self, **over):
        self.routes = {
            "/repos/o/r": (200, {"default_branch": "main"}),
            "/repos/o/r/rulesets?per_page=100&page=1": (
                200, [{"id": 1, "name": "protect-main", "target": "branch",
                       "enforcement": "active"}]),
            "/repos/o/r/rulesets?per_page=100&page=2": (200, []),
            "/repos/o/r/rulesets/1": (200, ruleset_body()),
            "/repos/o/r/actions/permissions/workflow": (
                200, {"default_workflow_permissions": "read",
                      "can_approve_pull_request_reviews": True}),
            "/repos/o/r/actions/variables?per_page=100": (200, {"variables": []}),
            "/repos/o/r/git/ref/heads/state/gate": (200, {"object": {"sha": "t1", "type": "tag"}}),
            "/repos/o/r/git/tags/t1": (200, {"tag": "state/gate", "message": json.dumps(state_doc())}),
            "/repos/o/r/git/matching-refs/tags/state/gate/gen-": (
                200, [{"ref": "refs/tags/state/gate/gen-2",
                       "object": {"sha": "t2", "type": "tag"}}]),
            "/repos/o/r/git/tags/t2": (200, {"tag": "state/gate/gen-2",
                                             "message": json.dumps(state_doc())}),
        }
        self.routes.update(over)

    def __call__(self, path):
        return self.routes.get(path, (404, {"message": "Not Found"}))


class ReportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for rel in (".github/required-checks.json", ".github/workflows/pr-verify.yml"):
            dst = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), dst)
        self.write_policy()

    def write_policy(self, **over):
        doc = {"schema": "mihomo-rules-approval-policy", "version": 1,
               "approvers": ["octocat"], "required_approvals": 1,
               "allow_bot_approvers": False, "head_binding": "exact",
               "on_missing": "fail-closed"}
        doc.update(over)
        dst = os.path.join(self.tmp, ".github", "approval-policy.json")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def report(self, world):
        return vp.build_report(repo="o/r", target_branch="main", call=world, root=self.tmp)

    def assertBlocked(self, report):
        self.assertFalse(report["ok"])
        self.assertEqual(report["decision"], "BLOCKED")
        self.assertFalse(report["writes_performed"])


class ReadyTest(ReportBase):
    def test_all_conditions_met_is_ready_and_read_only(self):
        r = self.report(World())
        self.assertTrue(r["ok"], {k: v["reason_codes"] for k, v in r["checks"].items()})
        self.assertEqual(r["decision"], "READY")
        self.assertFalse(r["writes_performed"])
        self.assertEqual(r["schema_version"], 1)
        self.assertEqual(set(r["checks"]), set(vp.CHECK_ORDER))
        for name, c in r["checks"].items():
            self.assertIn("ok", c, name)
            self.assertIn("reason_codes", c, name)
            self.assertIn("evidence", c, name)


class RulesetNegativeTest(ReportBase):
    def test_other_branch_only_is_blocked(self):
        w = World(**{"/repos/o/r/rulesets/1": (200, ruleset_body(include=["refs/heads/dev"]))})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("RULESET_NOT_COVERING_TARGET",
                      r["checks"]["ruleset_active"]["reason_codes"])

    def test_inactive_ruleset_is_blocked(self):
        w = World(**{"/repos/o/r/rulesets/1": (200, ruleset_body(enforcement="evaluate"))})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("RULESET_NOT_ACTIVE", r["checks"]["ruleset_active"]["reason_codes"])

    def test_missing_route_a_context_is_blocked(self):
        w = World(**{"/repos/o/r/rulesets/1": (
            200, ruleset_body(contexts=(("general-pr-verify", 15368),)))})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertTrue(any("REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify" in x
                            for x in r["checks"]["route_a_required_status"]["reason_codes"]))

    def test_untrusted_source_is_blocked(self):
        w = World(**{"/repos/o/r/rulesets/1": (
            200, ruleset_body(contexts=(("route-a/verify", 9999),)))})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertTrue(any("REQUIRED_STATUS_SOURCE_UNVERIFIED" in x
                            for x in r["checks"]["route_a_required_status"]["reason_codes"]))

    def test_ruleset_detail_403_is_unverifiable(self):
        w = World(**{"/repos/o/r/rulesets/1": (403, "forbidden")})
        r = self.report(w)
        self.assertFalse(r["ok"])
        self.assertEqual(r["decision"], "UNVERIFIABLE")

    def test_ruleset_list_500_is_unverifiable(self):
        w = World(**{"/repos/o/r/rulesets?per_page=100&page=1": (500, "boom")})
        r = self.report(w)
        self.assertFalse(r["ok"])
        self.assertEqual(r["decision"], "UNVERIFIABLE")


class HumanChecksTest(ReportBase):
    def _tamper(self, mutate):
        p = os.path.join(self.tmp, ".github/required-checks.json")
        doc = json.load(open(p, encoding="utf-8"))
        mutate(doc)
        json.dump(doc, open(p, "w", encoding="utf-8"))

    def test_missing_required_check_is_blocked(self):
        self._tamper(lambda d: d["checks"].pop())
        r = self.report(World())
        self.assertBlocked(r)
        self.assertFalse(r["checks"]["human_pr_required_checks"]["ok"])

    def test_renamed_check_is_blocked(self):
        def mutate(d):
            d["checks"][0]["name"] = "general-pr-verify-v2"
        self._tamper(mutate)
        r = self.report(World())
        self.assertBlocked(r)
        self.assertTrue(any("NAME_DRIFT" in x for x in
                            r["checks"]["human_pr_required_checks"]["reason_codes"]))

    def test_wrong_app_source_is_blocked(self):
        def mutate(d):
            d["checks"][0]["source"]["app_id"] = 1234
        self._tamper(mutate)
        r = self.report(World())
        self.assertBlocked(r)

    def test_missing_status_context_in_manifest_is_blocked(self):
        self._tamper(lambda d: d.update({"status_contexts": []}))
        r = self.report(World())
        self.assertBlocked(r)
        self.assertIn("MANIFEST_MISSING_ROUTE_A_STATUS_CONTEXT",
                      r["checks"]["human_pr_required_checks"]["reason_codes"])

    def test_continue_on_error_job_is_blocked(self):
        p = os.path.join(self.tmp, ".github/workflows/pr-verify.yml")
        text = open(p, encoding="utf-8").read().replace(
            "  general-pr-verify:", "  general-pr-verify:\n    continue-on-error: true", 1)
        open(p, "w", encoding="utf-8").write(text)
        r = self.report(World())
        self.assertBlocked(r)
        self.assertTrue(any("CONTINUE_ON_ERROR" in x for x in
                            r["checks"]["human_pr_required_checks"]["reason_codes"]))


class ActionsAndSwitchesTest(ReportBase):
    def test_actions_permissions_403_is_unverifiable(self):
        w = World(**{"/repos/o/r/actions/permissions/workflow": (403, "forbidden")})
        r = self.report(w)
        self.assertFalse(r["ok"])
        self.assertEqual(r["decision"], "UNVERIFIABLE")

    def test_write_default_permissions_is_blocked(self):
        w = World(**{"/repos/o/r/actions/permissions/workflow": (
            200, {"default_workflow_permissions": "write"})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("ACTIONS_DEFAULT_PERMISSIONS_NOT_READ",
                      r["checks"]["actions_permissions"]["reason_codes"])

    def test_switch_enabled_is_blocked_but_reported(self):
        w = World(**{"/repos/o/r/actions/variables?per_page=100": (
            200, {"variables": [{"name": "CANDIDATE_SYNC_ENABLED", "value": "true"}]})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("PRODUCTION_SWITCH_ENABLED:CANDIDATE_SYNC_ENABLED",
                      r["checks"]["production_switches"]["reason_codes"])
        self.assertEqual(r["checks"]["production_switches"]["evidence"]["switches"]
                         ["CANDIDATE_SYNC_ENABLED"], "true")

    def test_unknown_switch_value_fails_closed(self):
        for value in ("yes", "TRUE", "1", "on", "maybe"):
            w = World(**{"/repos/o/r/actions/variables?per_page=100": (
                200, {"variables": [{"name": "GATE_STATE_ENABLED", "value": value}]})})
            r = self.report(w)
            self.assertBlocked(r if value.lower() != "false" else World())

    def test_explicit_false_and_empty_are_off(self):
        for value in ("false", "FALSE", "", "0"):
            w = World(**{"/repos/o/r/actions/variables?per_page=100": (
                200, {"variables": [{"name": "GATE_STATE_ENABLED", "value": value}]})})
            r = self.report(w)
            self.assertTrue(r["checks"]["production_switches"]["ok"], value)

    def test_variables_unreadable_is_unverifiable(self):
        w = World(**{"/repos/o/r/actions/variables?per_page=100": (403, "forbidden")})
        r = self.report(w)
        self.assertFalse(r["ok"])
        self.assertEqual(r["decision"], "UNVERIFIABLE")


class GateStateTest(ReportBase):
    def test_absent_state_is_blocked_not_error(self):
        w = World(**{"/repos/o/r/git/ref/heads/state/gate": (404, {"message": "Not Found"})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("GATE_STATE_NOT_INITIALIZED", r["checks"]["gate_state"]["reason_codes"])

    def test_paused_state_is_blocked(self):
        doc = state_doc(phase="paused", check_status="in_progress", conclusion="none")
        w = World(**{"/repos/o/r/git/tags/t1": (200, {"message": json.dumps(doc)})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("GATE_STATE_BLOCKING_PHASE:paused", r["checks"]["gate_state"]["reason_codes"])

    def test_recovery_required_is_blocked(self):
        doc = state_doc(phase="recovery-required", check_status="failure", conclusion="failure")
        w = World(**{"/repos/o/r/git/tags/t1": (200, {"message": json.dumps(doc)})})
        r = self.report(w)
        self.assertBlocked(r)

    def test_corrupt_payload_is_blocked(self):
        w = World(**{"/repos/o/r/git/tags/t1": (200, {"message": "{not json"})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("GATE_STATE_PAYLOAD_NOT_JSON", r["checks"]["gate_state"]["reason_codes"])

    def test_digest_mismatch_is_blocked(self):
        doc = state_doc()
        doc["snapshot_digest"] = "sha256:" + "0" * 64
        w = World(**{"/repos/o/r/git/tags/t1": (200, {"message": json.dumps(doc)})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertTrue(any("snapshot_digest" in x
                            for x in r["checks"]["gate_state"]["reason_codes"]))

    def test_snapshot_absent_is_blocked(self):
        w = World(**{"/repos/o/r/git/matching-refs/tags/state/gate/gen-": (200, [])})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("GATE_SNAPSHOT_ABSENT", r["checks"]["snapshot_recovery"]["reason_codes"])

    def test_snapshot_generation_mismatch_is_blocked(self):
        other = state_doc(generation=1)
        w = World(**{"/repos/o/r/git/tags/t2": (200, {"message": json.dumps(other)})})
        r = self.report(w)
        self.assertBlocked(r)
        self.assertIn("SNAPSHOT_MISSING_FOR_CURRENT_GENERATION",
                      r["checks"]["snapshot_recovery"]["reason_codes"])


class TransportTest(ReportBase):
    def test_transport_error_yields_unverifiable_report(self):
        def boom(path):
            raise vp.TransportError("timeout")
        with mock.patch.object(vp, "_http_get", boom), \
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}), \
                mock.patch.object(vp, "build_report", side_effect=vp.TransportError("timeout")):
            rc = vp.main(["--repo", "o/r", "--json", os.path.join(self.tmp, "pf.json"),
                          "--root", self.tmp])
        self.assertEqual(rc, 3)
        doc = json.load(open(os.path.join(self.tmp, "pf.json"), encoding="utf-8"))
        self.assertEqual(doc["decision"], "UNVERIFIABLE")
        self.assertFalse(doc["writes_performed"])
        self.assertEqual(set(doc["checks"]), set(vp.CHECK_ORDER))

    def test_bad_repo_exits_2(self):
        self.assertEqual(vp.main(["--repo", "", "--root", self.tmp]), 2)

    def test_exit_codes_match_decision(self):
        cases = {"READY": 0, "BLOCKED": 1, "UNVERIFIABLE": 3}
        for decision, expected in cases.items():
            rep = {"schema_version": 1, "ok": decision == "READY", "decision": decision,
                   "repo": "o/r", "target_branch": "main",
                   "checks": {k: {"ok": decision == "READY", "reason_codes": [], "evidence": {}}
                              for k in vp.CHECK_ORDER},
                   "blocked_checks": [], "writes_performed": False}
            path = os.path.join(self.tmp, f"{decision}.json")
            with mock.patch.object(vp, "build_report", return_value=rep), \
                    mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
                rc = vp.main(["--repo", "o/r", "--json", path, "--root", self.tmp])
            self.assertEqual(rc, expected)
            self.assertTrue(os.path.getsize(path) > 0)


class ApprovalPolicyTest(ReportBase):
    """显式审批策略：缺失/未配置/允许 bot/版本不符都必须阻断，不能静默放行。"""

    def codes(self, report):
        return report["checks"]["approval_policy"]["reason_codes"]

    def test_missing_policy_file_blocks(self):
        os.remove(os.path.join(self.tmp, ".github", "approval-policy.json"))
        r = self.report(World())
        self.assertBlocked(r)
        self.assertIn("APPROVAL_POLICY_MISSING", self.codes(r))

    def test_empty_approvers_is_unconfigured_not_allowed(self):
        self.write_policy(approvers=[])
        r = self.report(World())
        self.assertBlocked(r)
        self.assertIn("APPROVAL_POLICY_UNCONFIGURED", self.codes(r))

    def test_bot_approvers_allowed_is_rejected(self):
        self.write_policy(approvers=["dependabot[bot]"], allow_bot_approvers=True)
        r = self.report(World())
        self.assertBlocked(r)
        self.assertTrue(any(c.startswith("APPROVAL_POLICY_APPROVER_INVALID") for c in self.codes(r))
                        or "APPROVAL_POLICY_ALLOWS_BOTS" in self.codes(r))

    def test_required_exceeding_approvers_rejected(self):
        self.write_policy(approvers=["octocat"], required_approvals=2)
        r = self.report(World())
        self.assertBlocked(r)
        self.assertTrue(any(c.startswith("APPROVAL_POLICY_REQUIRED_EXCEEDS_APPROVERS")
                            for c in self.codes(r)))

    def test_unknown_field_rejected(self):
        self.write_policy(extra_field=1)
        r = self.report(World())
        self.assertBlocked(r)
        self.assertTrue(any(c.startswith("APPROVAL_POLICY_UNKNOWN_FIELDS") for c in self.codes(r)))

    def test_invalid_json_rejected(self):
        with open(os.path.join(self.tmp, ".github", "approval-policy.json"), "w") as fh:
            fh.write("{not json")
        r = self.report(World())
        self.assertBlocked(r)
        self.assertIn("APPROVAL_POLICY_INVALID_JSON", self.codes(r))

    def test_valid_policy_ok(self):
        r = self.report(World())
        self.assertTrue(r["checks"]["approval_policy"]["ok"], self.codes(r))
        self.assertEqual(r["checks"]["approval_policy"]["evidence"]["policy"]["approvers"],
                         ["octocat"])


if __name__ == "__main__":
    unittest.main()
