#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-1：机器人 PR 更新路径必须**先验证作者、后写入**，拒绝路径零写入。

真实事故（2026-10-23）：`robot-pr.yml` 用 PR #33 的原分支派发时命中「同 head/base 只能
一个 open PR」，代码先 PATCH 了 PR #33 的标题/正文，之后才识别出作者是 `Hawaiine` 并失败。
结果：**他人的 PR 正文被覆盖**（事后用留存的原始 JSON 恢复）。

因此判定顺序必须是「读 PR → 分类作者 → 仅当作者确为本项目机器人时才写」，
所有拒绝路径的写请求次数必须为 0。本用例通过请求记录器验证**实际发出的写请求**，
不只断言返回值。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import robot_pr as rp  # noqa: E402

REPO = "Hawaiine/mihomo-rules"
HEAD = "b" * 40
BASE = "4" * 40
WRITE_METHODS = {"PATCH", "POST", "PUT", "DELETE"}


def pr_obj(*, number=33, author_id=152380613, author_type="User", login="Hawaiine",
           head=HEAD, head_ref="fix/x", base_ref="main", repo=REPO):
    return {"number": number, "state": "open", "merged": False,
            "user": {"id": author_id, "login": login, "type": author_type},
            "head": {"sha": head, "ref": head_ref},
            "base": {"ref": base_ref, "repo": {"full_name": repo}}}


class WriteLog:
    def __init__(self, existing_prs):
        self.existing = existing_prs
        self.calls = []

    def __call__(self, token, method, path, api, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith(f"/repos/{REPO}/git/ref/heads/"):
            return 200, {"object": {"sha": HEAD}}
        if method == "GET" and path.startswith(f"/repos/{REPO}/compare/"):
            return 200, {"files": [{"filename": "scripts/x.py"}]}
        if method == "GET" and path.startswith(f"/repos/{REPO}/pulls?state=open"):
            return 200, list(self.existing)
        if method == "PATCH" and path.startswith(f"/repos/{REPO}/pulls/"):
            return 200, pr_obj(number=33, author_id=rp.BOT_USER_ID,
                               author_type="Bot", login=rp.BOT_LOGIN)
        if method == "POST" and path == f"/repos/{REPO}/pulls":
            return 201, pr_obj(number=99, author_id=rp.BOT_USER_ID,
                               author_type="Bot", login=rp.BOT_LOGIN)
        if method == "GET" and path.startswith(f"/repos/{REPO}/pulls/"):
            return 200, pr_obj(number=99, author_id=rp.BOT_USER_ID,
                               author_type="Bot", login=rp.BOT_LOGIN)
        return 404, {"message": "not found"}

    @property
    def writes(self):
        return [c for c in self.calls if c[0] in WRITE_METHODS]


class WriteBeforeAuthorCheckTest(unittest.TestCase):
    def setUp(self):
        self._orig = rp._call

    def tearDown(self):
        rp._call = self._orig

    def run_with(self, existing):
        log = WriteLog(existing)
        rp._call = log
        out = rp.create_or_update(repo=REPO, source_branch="fix/x", base_ref="main",
                                  title="t", body="b", token="x",
                                  api="https://api.github.com")
        return out, log

    # —— 拒绝路径必须零写入 ——
    def test_existing_pr_authored_by_trusted_human_is_zero_write(self):
        out, log = self.run_with([pr_obj(number=33)])
        self.assertFalse(out["ok"])
        self.assertTrue(any(p.startswith("PR_EXISTS_OTHER_AUTHOR") for p in out["problems"]),
                        out["problems"])
        self.assertEqual(log.writes, [], f"拒绝路径不得写入，实际 {log.writes}")

    def test_existing_pr_authored_by_other_human_is_zero_write(self):
        out, log = self.run_with([pr_obj(number=41, author_id=999, login="someone")])
        self.assertFalse(out["ok"])
        self.assertEqual(log.writes, [])

    def test_existing_pr_authored_by_unexpected_bot_is_zero_write(self):
        out, log = self.run_with([pr_obj(number=42, author_id=100000, author_type="Bot",
                                         login="dependabot[bot]")])
        self.assertFalse(out["ok"])
        self.assertEqual(log.writes, [])

    def test_existing_pr_with_unreadable_author_is_zero_write(self):
        broken = pr_obj(number=43)
        broken["user"] = {"login": "ghost"}
        out, log = self.run_with([broken])
        self.assertFalse(out["ok"])
        self.assertEqual(log.writes, [])

    # —— 合法路径必须仍然工作 ——
    def test_robot_authored_existing_pr_is_updated(self):
        out, log = self.run_with([pr_obj(number=35, author_id=rp.BOT_USER_ID,
                                         author_type="Bot", login=rp.BOT_LOGIN)])
        self.assertTrue(out["ok"], out["problems"])
        self.assertEqual(out["action"], "update")
        self.assertEqual([c[0] for c in log.writes], ["PATCH"])

    def test_no_existing_pr_creates(self):
        out, log = self.run_with([])
        self.assertTrue(out["ok"], out["problems"])
        self.assertEqual(out["action"], "create")
        self.assertEqual([c[0] for c in log.writes], ["POST"])

    # —— 失败结果必须显式说明「未写入」 ——
    def test_rejection_reports_write_attempted_false(self):
        out, _ = self.run_with([pr_obj(number=33)])
        self.assertIs(out.get("write_attempted"), False)
        self.assertEqual(out.get("action"), "none")

    def test_rejection_preserves_existing_pr_number(self):
        out, _ = self.run_with([pr_obj(number=33)])
        self.assertEqual(out.get("existing_pr"), 33)

    # —— 成功路径回读校验仍需保住 ——
    def test_rejection_does_not_mask_readback_rules_for_create(self):
        """创建后作者不是机器人时仍必须失败（回读校验不得因 R-1 改动被弱化）。"""
        log = WriteLog([])
        original = log.__call__

        def patched(token, method, path, api, body=None):
            status, obj = original(token, method, path, api, body)
            if method == "GET" and path == f"/repos/{REPO}/pulls/99":
                return 200, pr_obj(number=99)          # 回读成人类作者
            return status, obj

        rp._call = patched
        out = rp.create_or_update(repo=REPO, source_branch="fix/x", base_ref="main",
                                  title="t", body="b", token="x",
                                  api="https://api.github.com")
        self.assertFalse(out["ok"])
        self.assertTrue(any(p.startswith("PR_AUTHOR_NOT_BOT") for p in out["problems"]))


if __name__ == "__main__":
    unittest.main()