#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main-push 只读校验 workflow 的结构与安全姿态测试。

背景：仓库 7 个既有 workflow 的 `on:` 都不含 `push`，因此 squash merge 后的 main 提交
没有任何内容校验记录（实测 merge SHA 上只有发布器自身的 publish-status）。
新增 `main-push-verify.yml` 后必须保证：

* 只由 `push` 到 main 触发，且不依赖任何 `pull_request` 专有字段；
* 权限恰为最小只读（contents: read），不得出现任何写权限；
* 不冒充必需检查名（`route-a/verify` / 三个 pr-verify 检查名）；
* 每个 checkout 都 `persist-credentials: false`；图标仓库检出使用已批准 pin；
* 不含任何合并/改 refs/push main 之类的 mutation 命令；
* 新增它之后，既有 workflow 策略 guard 仍然整体通过（不得放宽其它 workflow 的安全策略）。

这些断言都调用**生产 guard 函数**（`verify_workflow_policy`），而不是对源码做字符串匹配。
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_workflow_policy as pol  # noqa: E402

WF = ROOT / ".github" / "workflows" / "main-push-verify.yml"
PIN_FILE = ROOT / "scripts" / "config_contract" / "oasisic_revision.json"
REQUIRED_NAMES = {"general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"}
ICONS_REPO = "Hawaiine/Oasisic-Icons"


def approved_pin() -> str:
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))["revision"]


def wf_text() -> str:
    return WF.read_text(encoding="utf-8")


def wf_doc() -> dict:
    return yaml.safe_load(wf_text())


def triggers(doc: dict) -> dict:
    return doc.get(True) or doc.get("on") or {}


def checkout_steps(doc: dict) -> list[dict]:
    out = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if str(step.get("uses") or "").startswith("actions/checkout"):
                out.append(step)
    return out


class MainPushWorkflowStructureTest(unittest.TestCase):
    def test_workflow_exists_and_parses(self):
        self.assertTrue(WF.is_file(), f"缺少 {WF}")
        self.assertIsInstance(wf_doc(), dict)

    def test_trigger_is_push_to_main_only(self):
        trig = triggers(wf_doc())
        self.assertIn("push", trig, f"必须以 push 触发，实际 {trig}")
        self.assertNotIn("pull_request", trig, "不得由 pull_request 触发（那是 pr-verify 的职责）")
        self.assertNotIn("pull_request_target", trig, "pull_request_target 禁止")
        self.assertEqual(trig["push"].get("branches"), ["main"], f"只允许 main 分支，实际 {trig['push']}")

    def test_no_pull_request_specific_fields_used(self):
        text = wf_text()
        for field in ("github.event.pull_request", "pull_request.number", "pull_request.base",
                      "pull_request.head"):
            self.assertNotIn(field, text, f"push 事件下不存在 {field}")

    def test_permissions_are_read_only(self):
        doc = wf_doc()
        self.assertEqual(doc.get("permissions"), {"contents": "read"},
                         f"顶层权限必须恰为 contents: read，实际 {doc.get('permissions')!r}")
        for jid, job in (doc.get("jobs") or {}).items():
            self.assertIsNone(job.get("permissions"),
                              f"job '{jid}' 不得单独放宽/收紧权限（应与顶层一致）")

    def test_job_name_does_not_impersonate_required_checks(self):
        """断言作用于**解析后的 job id / job name**（注释里说明「不使用」是允许的）。"""
        doc = wf_doc()
        jobs = doc.get("jobs") or {}
        names = set(jobs) | {str(j.get("name")) for j in jobs.values()}
        self.assertFalse(names & REQUIRED_NAMES,
                         f"不得使用必需检查名（会与 pr-verify 的同名检查混淆）：{names & REQUIRED_NAMES}")
        self.assertNotIn("route-a/verify", names, "不得声明 route-a/verify 检查身份")
        for jid, job in jobs.items():
            for step in job.get("steps") or []:
                self.assertNotEqual(step.get("name"), "route-a/verify",
                                    f"job '{jid}' 的步骤不得冒用 route-a/verify 名称")

    def test_every_checkout_disables_persisted_credentials(self):
        steps = checkout_steps(wf_doc())
        self.assertTrue(steps, "工作流必须至少检出一次代码")
        for step in steps:
            self.assertIs(step.get("with", {}).get("persist-credentials"), False,
                          f"checkout 必须 persist-credentials: false（step={step.get('name')!r}）")

    def test_icon_checkout_uses_approved_pin(self):
        icons = [s for s in checkout_steps(wf_doc())
                 if s.get("with", {}).get("repository") == ICONS_REPO]
        self.assertTrue(icons, "应检出固定版本 Oasisic-Icons 以便校验图标基线")
        for step in icons:
            self.assertEqual(step["with"].get("ref"), approved_pin(),
                             "图标仓库必须使用已批准 pin（不得用分支/标签/浮动 ref）")


class GuardIntegrationTest(unittest.TestCase):
    """把真实 workflow 目录复制到临时根目录后，直接调用生产 guard。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        root = Path(self._tmp)
        shutil.copytree(ROOT / ".github", root / ".github")
        for rel in ("scripts/ci", "scripts/config_contract"):
            shutil.copytree(ROOT / rel, root / rel)
        self.root = root

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def problems(self) -> list[str]:
        problems, _info = pol.run(self.root)
        return problems

    def test_real_tree_passes_and_new_workflow_is_inspected(self):
        problems, info = pol.run(self.root)
        self.assertEqual(problems, [], problems)
        self.assertIn("main-push-verify.yml", info["workflows_checked"],
                      "新 workflow 必须真的被 guard 检查（否则等于没被覆盖）")

    def _mutate(self, old: str, new: str) -> list[str]:
        path = self.root / ".github" / "workflows" / "main-push-verify.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"测试夹具失配：未找到 {old!r}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return self.problems()

    def test_write_permission_is_rejected(self):
        """P19：push 触发的 workflow 必须只读；顶层写权限必须被拒绝。"""
        problems = self._mutate("permissions:\n  contents: read",
                                "permissions:\n  contents: write")
        self.assertTrue(any("permissions" in p for p in problems), problems)
        self.assertTrue(any("P19" in p or "push 触发" in p for p in problems), problems)

    def test_job_level_write_permission_is_rejected(self):
        """P19：job 级写权限同样必须被拒绝（不能靠 job 覆盖绕过顶层只读）。"""
        problems = self._mutate(
            "    timeout-minutes: 30\n",
            "    timeout-minutes: 30\n    permissions:\n      contents: write\n")
        self.assertTrue(any("job 'main-push-verify'" in p and "写权限" in p for p in problems),
                        problems)

    def test_pull_request_target_is_rejected(self):
        problems = self._mutate("on:\n  push:\n    branches:\n      - main",
                                "on:\n  pull_request_target:")
        self.assertTrue(any("pull_request_target" in p for p in problems), problems)

    def test_persist_credentials_true_is_rejected(self):
        problems = self._mutate("          ref: ${{ github.sha }}\n          fetch-depth: 0\n"
                                "          persist-credentials: false",
                                "          ref: ${{ github.sha }}\n          fetch-depth: 0\n"
                                "          persist-credentials: true")
        self.assertTrue(any("persist-credentials" in p for p in problems), problems)

    def test_floating_icon_ref_is_rejected(self):
        problems = self._mutate(f"          ref: {approved_pin()}", "          ref: main")
        self.assertTrue(any("已批准 pin" in p for p in problems), problems)

    def test_direct_main_mutation_path_is_rejected(self):
        """P11：任何直接改写 main 的路径（含 git push main）必须被整体 guard 判定违规。"""
        problems = self._mutate(
            "      - name: 🧾 代码可编译性检查",
            "      - name: 偷改 main\n        run: git push origin HEAD:refs/heads/main\n"
            "      - name: 🧾 代码可编译性检查")
        self.assertTrue(any("main" in p for p in problems), problems)

    def test_merge_mutation_marker_is_rejected(self):
        """P15：直接发送 auto-merge / 合并 mutation 必须被判定违规。"""
        doc = wf_doc()
        mutated = copy.deepcopy(doc)
        mutated["jobs"]["main-push-verify"]["steps"].append(
            {"name": "合并", "run": "gh pr merge 36 --squash"})
        problems = pol.direct_mutation_problems(mutated, "main-push-verify.yml")
        self.assertTrue(problems, "直接合并路径必须被判定为 mutation 违规")

    def test_shadowing_protected_command_is_rejected(self):
        doc = wf_doc()
        mutated = copy.deepcopy(doc)
        mutated["jobs"]["main-push-verify"]["steps"].append(
            {"name": "遮蔽", "run": "python3() { true; }\nexport -f python3"})
        problems = pol.shadowing_problems(mutated, "main-push-verify.yml")
        self.assertTrue(problems, "遮蔽受保护命令必须被判定为违规")


if __name__ == "__main__":
    unittest.main()
