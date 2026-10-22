#!/usr/bin/env python3
"""G1：验收工具的发布器 run 绑定必须优先使用 Check Run 的 external_id。

根因：GitHub 会忽略 API 创建的 Check Run 的 details_url，回读返回自动生成的
``/runs/<check_run_id>``，其中不含 ``/actions/runs/`` 标记，导致旧实现无法解析
发布器 run id，返回 EVIDENCE_INSUFFICIENT / PUBLISHER_RUN_UNBOUND。

修复方向：优先用 ``external_id = route-a-run:<run_id>`` 做绑定，details_url 作为
兼容与交叉校验来源。本文件先给出 RED，再验证 GREEN。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_route_a_acceptance as acc  # noqa: E402

HEAD = "a" * 40
MAIN = "b" * 40
REQUIRED = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]
PUB_RUN = "39713755722"
GH_AUTO_URL = "https://github.com/Hawaiine/mihomo-rules/runs/119611526535"


def cr(name, app=15368, conclusion="success", head=HEAD, details=None, external_id=None):
    out = {"name": name, "head_sha": head, "status": "completed", "conclusion": conclusion,
           "app": {"id": app, "slug": "github-actions"},
           "details_url": details if details is not None
           else "https://github.com/o/r/actions/runs/500"}
    if external_id is not None:
        out["external_id"] = external_id
    return out


def route_a_cr(**kw):
    return cr("route-a/verify", **kw)


def ev(route_cr=None, **over):
    route_a = route_cr if route_cr is not None else route_a_cr(external_id=f"route-a-run:{PUB_RUN}")
    d = {
        "pull": {"state": "open", "merged": False, "mergeable": True, "mergeable_state": "clean",
                 "head": {"sha": HEAD}, "base": {"ref": "main", "sha": MAIN}},
        "main": {"object": {"sha": MAIN}},
        "check_runs": {"check_runs": [cr("general-pr-verify"), cr("workflow-policy-guard"),
                                      cr("trusted-baseline-guard"), route_a]},
        "statuses": [{"context": "route-a/verify", "state": "success", "sha": HEAD,
                      "target_url": f"https://github.com/o/r/actions/runs/{PUB_RUN}",
                      "created_at": "2026-10-22T02:54:47Z",
                      "creator": {"login": "github-actions[bot]", "type": "Bot"}}],
        "combined": {"state": "success"},
        "publisher_run": {"id": int(PUB_RUN), "name": "General PR verification",
                          "path": ".github/workflows/pr-verify.yml", "event": "pull_request",
                          "head_sha": HEAD, "conclusion": "success",
                          "repository": {"full_name": "Hawaiine/mihomo-rules"}},
        "expected_head_sha": HEAD,
        "merge_state_status": "CLEAN",
    }
    for k, v in over.items():
        if k == "pull":
            d["pull"].update(v)
        elif k == "check_runs":
            d["check_runs"] = {"check_runs": v}
        elif k == "main_sha":
            d["main"] = {"object": {"sha": v}}
        else:
            d[k] = v
    return d


class G1Base(unittest.TestCase):
    def result(self, evidence, required=REQUIRED):
        return acc.classify(evidence, required)

    def verdict(self, evidence, required=REQUIRED):
        return self.result(evidence, required)["verdict"]

    def codes(self, evidence, required=REQUIRED):
        return [r["code"] for r in self.result(evidence, required).get("reasons") or []]

    def assertNotPass(self, evidence, required=REQUIRED):
        got = self.verdict(evidence, required)
        self.assertNotEqual(got, acc.PASS, f"不得判 PASS，实际 {got}")


class ParseExternalIdTest(G1Base):
    """external_id 的严格解析。"""

    def test_strict_prefix_and_digits(self):
        self.assertEqual(acc.parse_external_id(f"route-a-run:{PUB_RUN}"), PUB_RUN)

    def test_accepts_minimal_and_20_digit_ids(self):
        """协议允许正十进制 run id：最小 1 与 20 位无前导零均合法。"""
        self.assertEqual(acc.parse_external_id("route-a-run:1"), "1")
        self.assertEqual(acc.parse_external_id("route-a-run:123"), "123")
        twenty = "9" * 20
        self.assertEqual(acc.parse_external_id(f"route-a-run:{twenty}"), twenty)

    def test_reject_surrounding_whitespace(self):
        """协议第 3 条：前缀前后不得存在空白或其他字符（不得隐式 strip）。"""
        for bad in (" route-a-run:123", "route-a-run:123 ", " route-a-run:123 ",
                    "\troute-a-run:123", "route-a-run:123\t", "\nroute-a-run:123",
                    "route-a-run:123\n", "\r\nroute-a-run:123"):
            self.assertIsNone(acc.parse_external_id(bad), repr(bad))

    def test_reject_non_ascii_digits_without_crashing(self):
        """协议第 4 条：后缀只能含 ASCII 0-9；非 ASCII 数字必须拒绝且不得抛异常。"""
        for bad in ("route-a-run:١٢٣", "route-a-run:１２３", "route-a-run:१२३",
                    "route-a-run:²", "route-a-run:²³", "route-a-run:½"):
            try:
                got = acc.parse_external_id(bad)
            except Exception as exc:  # noqa: BLE001 - 崩溃本身即缺陷
                self.fail(f"{bad!r} 抛出 {type(exc).__name__}: {exc}；应返回 None")
            self.assertIsNone(got, repr(bad))

    def test_reject_missing(self):
        self.assertIsNone(acc.parse_external_id(None))
        self.assertIsNone(acc.parse_external_id(""))
        self.assertIsNone(acc.parse_external_id("   "))

    def test_reject_wrong_prefix(self):
        for bad in (f"check-run:{PUB_RUN}", f"run:{PUB_RUN}", f"route-a:{PUB_RUN}",
                    f"xroute-a-run:{PUB_RUN}", f"route-a-runx:{PUB_RUN}"):
            self.assertIsNone(acc.parse_external_id(bad), bad)

    def test_reject_empty_or_illegal_suffix(self):
        for bad in ("route-a-run:", "route-a-run:abc", "route-a-run:123abc",
                    "route-a-run:0", "route-a-run:-1", "route-a-run:12.5",
                    "route-a-run:123/456", "route-a-run:123?x=1", "route-a-run: 123",
                    "route-a-run:0123", "route-a-run:" + "9" * 30):
            self.assertIsNone(acc.parse_external_id(bad), bad)


class ResolvePublisherRunTest(G1Base):
    """external_id 优先，details_url 兼容，冲突不得静默选择。"""

    def test_external_id_wins_when_details_url_is_github_auto(self):
        rid, _src, conflict = acc.resolve_publisher_run_id(
            {"external_id": f"route-a-run:{PUB_RUN}", "details_url": GH_AUTO_URL})
        self.assertEqual(rid, PUB_RUN)
        self.assertFalse(conflict)

    def test_details_url_still_works_without_external_id(self):
        rid, _src, conflict = acc.resolve_publisher_run_id(
            {"details_url": "https://github.com/o/r/actions/runs/500"})
        self.assertEqual(rid, "500")
        self.assertFalse(conflict)

    def test_conflict_detected_without_silent_pick(self):
        rid, _src, conflict = acc.resolve_publisher_run_id(
            {"external_id": "route-a-run:500",
             "details_url": "https://github.com/o/r/actions/runs/999"})
        self.assertIsNone(rid)
        self.assertTrue(conflict)

    def test_agreeing_sources_not_conflict(self):
        rid, _src, conflict = acc.resolve_publisher_run_id(
            {"external_id": "route-a-run:500",
             "details_url": "https://github.com/o/r/actions/runs/500"})
        self.assertEqual(rid, "500")
        self.assertFalse(conflict)

    def test_github_auto_url_never_becomes_run_id(self):
        rid, _src, _c = acc.resolve_publisher_run_id({"details_url": GH_AUTO_URL})
        self.assertIsNone(rid)

    def test_malformed_external_id_present_blocks_details_url_fallback(self):
        """存在但畸形的 external_id → fail-closed，不回退 details_url。"""
        for bad in ("", " ", "route-a-run:", "route-a-run:0123", "route-a-run:²",
                    " route-a-run:500", "check-run:500"):
            rid, src, conflict = acc.resolve_publisher_run_id(
                {"external_id": bad, "details_url": "https://github.com/o/r/actions/runs/500"})
            self.assertIsNone(rid, repr(bad))
            self.assertEqual(src, "malformed_external_id", repr(bad))
            self.assertFalse(conflict, repr(bad))

    def test_absent_external_id_allows_legacy_details_url(self):
        """external_id 缺失（旧对象）→ 允许 details_url 兼容回退。"""
        for cr in ({"details_url": "https://github.com/o/r/actions/runs/500"},
                   {"external_id": None, "details_url": "https://github.com/o/r/actions/runs/500"}):
            rid, src, conflict = acc.resolve_publisher_run_id(cr)
            self.assertEqual(rid, "500")
            self.assertEqual(src, "details_url")
            self.assertFalse(conflict)

    def test_valid_external_id_with_unparseable_details_uses_external(self):
        rid, src, conflict = acc.resolve_publisher_run_id(
            {"external_id": "route-a-run:500", "details_url": GH_AUTO_URL})
        self.assertEqual(rid, "500")
        self.assertEqual(src, "external_id")
        self.assertFalse(conflict)

    def test_both_unparseable_returns_none(self):
        rid, src, conflict = acc.resolve_publisher_run_id(
            {"details_url": GH_AUTO_URL})
        self.assertIsNone(rid)
        self.assertIsNone(src)
        self.assertFalse(conflict)


class G1PositiveTest(G1Base):
    """正例：external_id 生效且全部身份条件满足才 PASS。"""

    def test_pass_with_external_id_and_github_auto_details_url(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}", details=GH_AUTO_URL))
        self.assertEqual(self.verdict(e), acc.PASS, self.result(e))
        self.assertEqual(self.result(e)["facts"]["publisher_run_id"], PUB_RUN)

    def test_pass_with_legacy_details_url(self):
        e = ev(route_cr=route_a_cr(details="https://github.com/o/r/actions/runs/500"))
        e["publisher_run"]["id"] = 500
        e["statuses"][0]["target_url"] = "https://github.com/o/r/actions/runs/500"
        self.assertEqual(self.verdict(e), acc.PASS, self.result(e))

    def test_external_id_bound_to_real_pr_verify_run(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}", details=GH_AUTO_URL))
        facts = self.result(e)["facts"]
        self.assertEqual(facts["publisher_run"]["id"], int(PUB_RUN))
        self.assertEqual(facts["publisher_run"]["event"], "pull_request")


class G1NegativeTest(G1Base):
    """负例：每条都必须拒绝通过，且原因明确。"""

    def test_malformed_external_id_never_crashes_or_passes(self):
        """畸形绑定在 classify() 层必须 fail-closed 返回证据不足，不得抛异常或通过。"""
        for bad in (" route-a-run:39713755722", "route-a-run:39713755722 ",
                    "route-a-run:²", "route-a-run:١٢٣", "route-a-run:0123", ""):
            e = ev(route_cr=route_a_cr(external_id=bad, details=GH_AUTO_URL))
            try:
                res = self.result(e)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"{bad!r} 在 classify() 抛出 {type(exc).__name__}: {exc}")
            self.assertNotEqual(res["verdict"], acc.PASS, bad)
            self.assertEqual(res["verdict"], acc.EVIDENCE_INSUFFICIENT, bad)
            self.assertIn("PUBLISHER_RUN_MALFORMED_EXTERNAL_ID", self.codes(e), bad)

    def test_malformed_external_id_does_not_fall_back_to_details_url(self):
        """畸形 external_id 存在时不得静默回退到合法的 details_url。"""
        e = ev(route_cr=route_a_cr(external_id=" route-a-run:39713755722",
                                   details="https://github.com/o/r/actions/runs/500"))
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_MALFORMED_EXTERNAL_ID", self.codes(e))

    def test_missing_external_id_and_unparseable_details(self):
        e = ev(route_cr=route_a_cr(details=GH_AUTO_URL))
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_UNBOUND", self.codes(e))

    def test_wrong_external_id_prefix(self):
        e = ev(route_cr=route_a_cr(external_id=f"check-run:{PUB_RUN}", details=GH_AUTO_URL))
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_MALFORMED_EXTERNAL_ID", self.codes(e))

    def test_empty_external_id_suffix(self):
        e = ev(route_cr=route_a_cr(external_id="route-a-run:", details=GH_AUTO_URL))
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_MALFORMED_EXTERNAL_ID", self.codes(e))

    def test_illegal_external_id_suffix(self):
        for bad in ("route-a-run:abc", "route-a-run:0", "route-a-run:123/456"):
            e = ev(route_cr=route_a_cr(external_id=bad, details=GH_AUTO_URL))
            self.assertNotPass(e)
            self.assertIn("PUBLISHER_RUN_MALFORMED_EXTERNAL_ID", self.codes(e), bad)

    def test_external_id_differs_from_publisher_run(self):
        e = ev(route_cr=route_a_cr(external_id="route-a-run:111", details=GH_AUTO_URL))
        e["publisher_run"] = {"id": 111, "event": "pull_request", "head_sha": HEAD,
                              "conclusion": "success", "path": ".github/workflows/pr-verify.yml"}
        # external_id 指向 111，但 evidence 里 publisher_run 是 111 —— 一致；
        # 真正的不一致在下面用不同 id 构造。
        e2 = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}", details=GH_AUTO_URL))
        e2["publisher_run"] = {"id": 111, "event": "pull_request", "head_sha": HEAD,
                               "conclusion": "success"}
        self.assertNotPass(e2)
        self.assertIn("PUBLISHER_RUN_MISMATCH", self.codes(e2))

    def test_conflicting_details_url_and_external_id(self):
        e = ev(route_cr=route_a_cr(
            external_id=f"route-a-run:{PUB_RUN}",
            details="https://github.com/o/r/actions/runs/999"))
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_BINDING_CONFLICT", self.codes(e))

    def test_check_run_head_sha_mismatch(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}",
                                   details=GH_AUTO_URL, head="c" * 40))
        self.assertNotPass(e)
        self.assertIn("CHECK_RUN_HEAD_SHA_MISMATCH", self.codes(e))

    def test_app_id_mismatch(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}",
                                   details=GH_AUTO_URL, app=99999))
        self.assertNotPass(e)
        self.assertIn("CHECK_RUN_APP_ID_MISMATCH", self.codes(e))

    def test_pr_closed_or_merged(self):
        for state, merged in (("closed", False), ("closed", True)):
            e = ev(pull={"state": state, "merged": merged})
            self.assertNotPass(e)
            self.assertIn("PR_NOT_OPEN", self.codes(e), f"{state}/{merged}")

    def test_pr_head_mismatch(self):
        e = ev(pull={"head": {"sha": "d" * 40}})
        self.assertNotPass(e)
        self.assertIn("HEAD_SHA_MISMATCH", self.codes(e))

    def test_status_not_success_or_wrong_sha(self):
        e = ev(statuses=[{"context": "route-a/verify", "state": "failure", "sha": HEAD,
                          "creator": {"login": "github-actions[bot]", "type": "Bot"}}])
        self.assertNotPass(e)

    def test_publisher_run_wrong_event(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}", details=GH_AUTO_URL))
        e["publisher_run"] = {"id": int(PUB_RUN), "event": "workflow_dispatch",
                              "head_sha": HEAD, "conclusion": "success"}
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_MISMATCH", self.codes(e))

    def test_publisher_run_wrong_head(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}", details=GH_AUTO_URL))
        e["publisher_run"] = {"id": int(PUB_RUN), "event": "pull_request",
                              "head_sha": "e" * 40, "conclusion": "success"}
        self.assertNotPass(e)
        self.assertIn("PUBLISHER_RUN_MISMATCH", self.codes(e))

    def test_check_run_not_success(self):
        e = ev(route_cr=route_a_cr(external_id=f"route-a-run:{PUB_RUN}",
                                   details=GH_AUTO_URL, conclusion="failure"))
        self.assertNotPass(e)
        self.assertIn("CHECK_RUN_NOT_SUCCESS", self.codes(e))


if __name__ == "__main__":
    unittest.main()
