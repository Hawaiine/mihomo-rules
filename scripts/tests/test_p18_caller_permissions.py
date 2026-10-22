#!/usr/bin/env python3
"""P18 caller 权限覆盖 guard 的 RED/GREEN 与负例测试。

P18：reusable workflow 调用方的 permissions 必须覆盖被调用 workflow 每个 job 需要的权限。
本文件直接对 check_workflow_call_permissions() 做单元测试，隔离于其它 guard 规则。
"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts" / "ci"))
import verify_workflow_policy as pol  # noqa: E402


def caller_doc(perms: dict | str) -> dict:
    return {
        "name": "caller",
        "on": {"workflow_call": None},
        "permissions": {"contents": "read"},
        "jobs": {
            "sync": {
                "uses": "./.github/workflows/callee.yml",
                "permissions": perms,
            }
        },
    }


def callee_doc(job_perms: dict | str) -> dict:
    return {
        "name": "callee",
        "on": {"workflow_call": None},
        "permissions": {"contents": "read"},
        "jobs": {
            "verify": {
                "name": "verify",
                "runs-on": "ubuntu-latest",
                "permissions": job_perms,
                "steps": [{"run": "echo hi"}],
            }
        },
    }


class P18CallerPermissionTest(unittest.TestCase):
    def problems(self, caller_perms, callee_perms) -> list[str]:
        docs = {
            "caller.yml": caller_doc(caller_perms),
            "callee.yml": callee_doc(callee_perms),
        }
        return pol.check_workflow_call_permissions(docs)

    # ---- GREEN：调用方覆盖被调用方 ----
    def test_caller_covers_callee_exactly(self):
        probs = self.problems({"contents": "read", "checks": "write"},
                              {"contents": "read", "checks": "write"})
        self.assertEqual(probs, [], probs)

    def test_caller_write_covers_callee_read(self):
        """调用方 write 可覆盖被调用方 read（被调用方可内部降级）。"""
        probs = self.problems({"contents": "read", "checks": "write"},
                              {"contents": "read", "checks": "read"})
        self.assertEqual(probs, [], probs)

    def test_caller_superset_passes(self):
        """调用方权限多于被调用方需求不报错（无关高权限不解释为必要）。"""
        probs = self.problems(
            {"contents": "write", "checks": "write", "statuses": "write", "actions": "read"},
            {"contents": "read", "checks": "write"})
        self.assertEqual(probs, [], probs)

    # ---- RED：负例必须失败 ----
    def test_missing_required_write_permission_fails(self):
        """移除必要权限 → guard 必须失败。"""
        probs = self.problems({"contents": "read"}, {"contents": "read", "checks": "write"})
        self.assertTrue(any("checks" in p for p in probs), probs)
        self.assertTrue(any("write" in p for p in probs), probs)

    def test_caller_read_insufficient_for_callee_write_fails(self):
        """调用方 read 不满足被调用方 write。"""
        probs = self.problems({"contents": "read", "checks": "read"},
                              {"contents": "read", "checks": "write"})
        self.assertTrue(any("checks" in p and "write" in p for p in probs), probs)

    def test_missing_required_read_permission_fails(self):
        """缺少只读权限同样必须失败。"""
        probs = self.problems({"contents": "read"}, {"contents": "read", "actions": "read"})
        self.assertTrue(any("actions" in p for p in probs), probs)

    def test_unparseable_permissions_fail_closed(self):
        """权限声明无法解析 → fail-closed，不得给出 PASS。"""
        probs = self.problems({"contents": "read"}, "write-all")
        self.assertTrue(any("无法可靠解析" in p for p in probs), probs)

    def test_unparseable_caller_permissions_fail_closed(self):
        """调用方权限无法解析 → 不得给出 PASS。"""
        probs = self.problems("write-all", {"contents": "read", "checks": "write"})
        self.assertTrue(any("checks" in p for p in probs), probs)

    def test_no_call_relationship_no_problems(self):
        """不存在调用关系时不得误报。"""
        docs = {
            "a.yml": {"on": {"push": None}, "permissions": {"contents": "read"},
                      "jobs": {"x": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]}}},
        }
        self.assertEqual(pol.check_workflow_call_permissions(docs), [])

    def test_missing_callee_file_skipped(self):
        """被调用文件不在检查范围时跳过（不误报）。"""
        docs = {"caller.yml": caller_doc({"contents": "read"})}
        self.assertEqual(pol.check_workflow_call_permissions(docs), [])

    # ---- 真实仓库回归 ----
    def test_real_repo_passes(self):
        """真实仓库（daily-sync → candidate-pr-sync）必须通过 P18。"""
        real_root = Path(__file__).parents[2]
        probs, _ = pol.run(real_root)
        p18 = [p for p in probs if "调用" in p and "被调用 job" in p]
        self.assertEqual(p18, [], p18)


if __name__ == "__main__":
    unittest.main()
