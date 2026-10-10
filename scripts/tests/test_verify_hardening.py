"""
test_verify_hardening.py — 校验强化后的不变量测试

覆盖三处历史盲区，确保它们不会退化：
1. canonical.dedup_rules：同 TYPE+VALUE 不同 param 不静默丢弃（保留带参、顺序无关、
   多 param 歧义上报）。
2. verify_rulesets：解析不了的行 / 不支持的规则类型 / 同值多 param 必须 FAIL，
   不允许「不计数 → 恰好数字对上 → PASS」。
3. verify_configs：check_full_comment_order 条数不一致必须 FAIL（zip 截断不得掩盖）、
   emoji 前缀组不得带 icon、出站目标引号检查不依赖具体组名白名单。
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import CanonicalRule, dedup_rules, sort_rules
from commit_writer import dedup_exact
import verify_rulesets
import verify_configs


def _r(value, rtype="IP-CIDR", param=""):
    return CanonicalRule(rule_type=rtype, value=value, param=param)


class TestDedupRulesParam(unittest.TestCase):
    """canonical.dedup_rules 的 param 语义"""

    def test_param_variant_wins_over_plain(self):
        """同 TYPE+VALUE：带 param 的保留，无参的丢弃"""
        kept, dropped, conflicts = dedup_rules([
            _r("1.2.3.0/24"),
            _r("1.2.3.0/24", param="no-resolve"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].param, "no-resolve")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(conflicts, [])

    def test_order_independent(self):
        """输入顺序颠倒，结果一致（旧实现是「保留首次出现」，顺序相关）"""
        a = dedup_rules([_r("1.2.3.0/24"), _r("1.2.3.0/24", param="no-resolve")])[0]
        b = dedup_rules([_r("1.2.3.0/24", param="no-resolve"), _r("1.2.3.0/24")])[0]
        self.assertEqual([(r.rule_type, r.value, r.param) for r in a],
                         [(r.rule_type, r.value, r.param) for r in b])
        self.assertEqual(a[0].param, "no-resolve")

    def test_identical_param_deduped(self):
        """同 TYPE+VALUE+PARAM 完全重复 → 只留一条"""
        kept, dropped, _ = dedup_rules([
            _r("1.2.3.0/24", param="no-resolve"),
            _r("1.2.3.0/24", param="no-resolve"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_multiple_distinct_params_reported_not_dropped(self):
        """多个不同 param 属歧义：全部保留并上报冲突，不自行裁决"""
        kept, _dropped, conflicts = dedup_rules([
            _r("1.2.3.0/24", param="no-resolve"),
            _r("1.2.3.0/24", param="src"),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][1], ["no-resolve", "src"])

    def test_dedup_exact_still_returns_sorted(self):
        """dedup_exact 仍返回排序结果且不含重复"""
        out = dedup_exact([_r("9.9.9.0/24", "IP-CIDR", "no-resolve"), _r("9.9.9.0/24")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out, sort_rules(out))
        self.assertEqual(out[0].param, "no-resolve")


class TestVerifyRulesetsHardening(unittest.TestCase):
    """verify_rulesets 必须对无法解析/不支持/多 param 的行 FAIL"""

    def _write(self, base, payload_lines, header_counts=None):
        brand = Path(base) / "ruleset" / "HardBrand"
        brand.mkdir(parents=True)
        counts = header_counts or {}
        header = "".join(f"# {t}: {counts.get(t, 0)}\n" for t in (
            "DOMAIN-KEYWORD", "DOMAIN-REGEX", "DOMAIN", "DOMAIN-SUFFIX",
            "IP-CIDR", "IP-CIDR6", "IP-ASN", "PROCESS-NAME"))
        (brand / "HardBrand.yaml").write_text(
            "# ===========================================\n"
            "# Rule Name: HardBrand\n"
            "# Author: Hawaiine\n"
            "# Updated: 2026-09-26 00:00:00\n"
            + header +
            "# ===========================================\n"
            "payload:\n" + payload_lines,
            encoding="utf-8",
        )
        (brand / "README.md").write_text(
            "# 📦 HardBrand 规则集\n\n## 📊 统计\n\n"
            "- **behavior**: classical\n- **策略组**: HardBrand\n",
            encoding="utf-8",
        )

    def _check(self, payload_lines, header_counts=None):
        orig = verify_rulesets.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, payload_lines, header_counts)
            verify_rulesets.ROOT = Path(tmp)
            try:
                return verify_rulesets.check_brand("HardBrand")
            finally:
                verify_rulesets.ROOT = orig

    def test_invalid_payload_line_fails(self):
        """无法解析的 payload 行 → INVALID_PAYLOAD_LINE"""
        ok, errors = self._check("  - 这不是一条规则\n")
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("INVALID_PAYLOAD_LINE" in e for e in errors), errors)

    def test_unsupported_rule_type_fails(self):
        """8 种类型之外的规则类型 → UNSUPPORTED_RULE_TYPE"""
        ok, errors = self._check("  - GEOIP,CN\n")
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("UNSUPPORTED_RULE_TYPE" in e for e in errors), errors)

    def test_param_ambiguity_fails(self):
        """同 TYPE+VALUE 两个不同 param → 歧义 FAIL"""
        ok, errors = self._check(
            "  - IP-CIDR,1.2.3.0/24,no-resolve\n"
            "  - IP-CIDR,1.2.3.0/24,src\n",
            {"IP-CIDR": 2},
        )
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("多 param 歧义" in e for e in errors), errors)

    def test_plain_plus_param_variant_fails_as_redundant(self):
        """一条带 param + 一条不带 → 冗余重复，必须 FAIL（写入路径会归一为带参版本）"""
        ok, errors = self._check(
            "  - IP-CIDR,1.2.3.0/24\n"
            "  - IP-CIDR,1.2.3.0/24,no-resolve\n",
            {"IP-CIDR": 2},
        )
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("带参/无参重复" in e for e in errors), errors)


class TestVerifyConfigsHardening(unittest.TestCase):
    """verify_configs 的条数断言 / emoji icon / 引号检查"""

    def _make_icon_repo(self, root):
        repo = Path(root) / "Oasisic-Icons"
        (repo / "icons" / "Category").mkdir(parents=True)
        (repo / "icons" / "Category" / "icon.png").write_bytes(b"png")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "icons"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
        return repo

    @staticmethod
    def _head(repo):
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

    def test_fixed_revision_is_used_and_no_floating_ref_fallback(self):
        """基准必须是固定 revision：即使 origin/main、main、HEAD 都存在也不得采用。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._make_icon_repo(root)
            head = self._head(repo)
            with patch.object(verify_configs, "ROOT", root), patch.dict("os.environ", {}, clear=True):
                paths, source = verify_configs.load_icon_reference(revision=head)
            self.assertEqual(paths, {"Category/icon.png"})
            self.assertEqual(source, f"{repo}@{head}")
            for floating in ("origin/main", "main", "HEAD"):
                self.assertNotIn(floating, source)

    def test_unreadable_pinned_revision_fails_without_falling_back_to_head(self):
        """仓库 HEAD 可读，但固定 revision 不可读 → 必须失败（旧实现回退 HEAD 通过）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_icon_repo(root)
            with patch.object(verify_configs, "ROOT", root), patch.dict("os.environ", {}, clear=True):
                paths, source = verify_configs.load_icon_reference(revision="a" * 40)
            self.assertIsNone(paths)
            self.assertIn("a" * 40, source)
            self.assertIn("不可读", source)

    def test_explicit_environment_override_has_priority(self):
        """MIHOMO_ICON_REPO 优先于仓库相对位置，且不再追加任何回退。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            self._make_icon_repo(root)
            override_root = Path(tmp) / "override-root"
            override_repo = self._make_icon_repo(override_root)
            head = self._head(override_repo)
            with patch.object(verify_configs, "ROOT", root), patch.dict(
                "os.environ", {"MIHOMO_ICON_REPO": str(override_repo)}, clear=True
            ):
                paths, source = verify_configs.load_icon_reference(revision=head)
            self.assertEqual(paths, {"Category/icon.png"})
            self.assertEqual(source, f"{override_repo}@{head}")

    def test_missing_repo_fails_closed_instead_of_skipping(self):
        """仓库缺失 → 基准不可用 → 图标存在性检查必须 FAIL（旧实现软跳过为 PASS）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(verify_configs, "ROOT", Path(tmp)), patch.dict("os.environ", {}, clear=True):
                paths, source = verify_configs.load_icon_reference()
            self.assertIsNone(paths)
            self.assertIn("无法读取固定 revision", source)
            self.assertFalse(verify_configs.check_icons_exist([], "x", (paths, source)))

    def test_worktree_only_checkout_is_not_scanned(self):
        """只有文件、没有 git 元数据 → 必须失败（旧实现扫描工作区并「找到」图标）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Oasisic-Icons" / "icons" / "Category").mkdir(parents=True)
            (root / "Oasisic-Icons" / "icons" / "Category" / "icon.png").write_bytes(b"png")
            with patch.object(verify_configs, "ROOT", root), patch.dict("os.environ", {}, clear=True):
                paths, source = verify_configs.load_icon_reference()
            self.assertIsNone(paths)
            self.assertIn("git 元数据", source)

    def test_missing_manifest_revision_fails_closed(self):
        """manifest 取不到 revision → 必须失败，不得跳过检查。"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(verify_configs, "ROOT", Path(tmp)), patch.dict("os.environ", {}, clear=True), \
                 patch.object(verify_configs, "_manifest_revision", return_value=None):
                paths, source = verify_configs.load_icon_reference()
            self.assertIsNone(paths)
            self.assertIn("缺少固定 Oasisic revision", source)
            self.assertFalse(verify_configs.check_icons_exist([], "x", (paths, source)))

    def test_missing_icon_path_fails_and_existing_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._make_icon_repo(root)
            head = self._head(repo)
            with patch.object(verify_configs, "ROOT", root), patch.dict("os.environ", {}, clear=True):
                ref = verify_configs.load_icon_reference(revision=head)
            url = ("https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons"
                   "/main/icons/Category/icon.png")
            self.assertTrue(verify_configs.check_icons_exist([f'      icon: "{url}"'], "v", ref))
            missing = url.replace("icon.png", "nope.png")
            self.assertFalse(verify_configs.check_icons_exist([f'      icon: "{missing}"'], "v", ref))

    def test_icons_dir_without_png_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = Path(root) / "Oasisic-Icons"
            (repo / "icons").mkdir(parents=True)
            (repo / "icons" / "README.md").write_text("no png here")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
            head = self._head(repo)
            with patch.object(verify_configs, "ROOT", root), patch.dict("os.environ", {}, clear=True):
                paths, source = verify_configs.load_icon_reference(revision=head)
            self.assertIsNone(paths)
            self.assertIn("没有任何 png", source)

    def test_git_failure_is_reported_not_swallowed(self):
        """git 命令失败必须如实上报为不可用，而不是静默继续。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_icon_repo(root)
            with patch.object(verify_configs, "ROOT", root), patch.dict("os.environ", {}, clear=True), \
                 patch.object(verify_configs, "icon_repo") as fake_icon_repo:
                fake_icon_repo.resolve_icon_repo.return_value = str(root / "Oasisic-Icons")
                fake_icon_repo.icon_repo_problems.return_value = ["无法执行 git 校验固定 revision: boom"]
                paths, source = verify_configs.load_icon_reference(revision="a" * 40)
            self.assertIsNone(paths)
            self.assertIn("无法执行 git 校验固定 revision", source)
            self.assertFalse(verify_configs.check_icons_exist([], "x", (paths, source)))

    def test_revision_format_is_validated_before_any_git_call(self):
        """main / HEAD / 缩写 SHA / 空值 / 非十六进制 → 必须在触碰 Git 之前失败。"""
        bad = ['main', 'HEAD', 'master', 'develop', 'f0f3bc2',
               'f0f3bc2a44616885682ee5f0e5921540b964e2d8x', 'z' * 40,
               '1234567890abcdef1234567890abcdef1234567', '', '   ']
        for revision in bad:
            with self.subTest(revision=revision):
                touched = []
                with tempfile.TemporaryDirectory() as tmp, \
                     patch.object(verify_configs.icon_repo, 'resolve_icon_repo',
                                  side_effect=lambda **kw: touched.append('resolve') or 'x'), \
                     patch.object(verify_configs.icon_repo, 'icon_repo_problems',
                                  side_effect=lambda *a: touched.append('git') or []):
                    paths, source = verify_configs.load_icon_reference(
                        root=tmp, environ={}, revision=revision)
                self.assertIsNone(paths)
                self.assertIn('格式不合法', source)
                self.assertEqual(touched, [], '格式非法时不得触碰 Git')
                self.assertFalse(verify_configs.check_icons_exist([], 'x', (paths, source)))

    def test_full_sha_reaches_the_git_object_check(self):
        """完整 40 位 SHA 必须继续进入 Git 对象检查（格式合法 ≠ 一定可用）。"""
        touched = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(verify_configs.icon_repo, 'resolve_icon_repo',
                          side_effect=lambda **kw: touched.append('resolve') or 'x'), \
             patch.object(verify_configs.icon_repo, 'icon_repo_problems',
                          side_effect=lambda *a: touched.append('git')
                          or ['固定 revision 在检出中不可读: x']):
            paths, source = verify_configs.load_icon_reference(
                root=tmp, environ={}, revision='A' * 40)
        self.assertIsNone(paths)
        self.assertEqual(touched, ['resolve', 'git'])
        self.assertIn('无法读取固定 revision', source)
        self.assertNotIn('格式不合法', source)

    def test_manifest_revision_format_is_validated(self):
        """manifest 里写 main/HEAD/缩写 SHA → 按格式失败，不被本地 Git 解析掉。"""
        for bad in ('main', 'HEAD', 'f0f3bc2', '', 'z' * 40):
            with self.subTest(revision=bad):
                with tempfile.TemporaryDirectory() as tmp, \
                     patch.object(verify_configs, 'ROOT', Path(tmp)), \
                     patch.dict('os.environ', {}, clear=True), \
                     patch.object(verify_configs, '_manifest_revision', return_value=bad), \
                     patch.object(verify_configs.icon_repo, 'resolve_icon_repo',
                                  side_effect=AssertionError('格式非法时不得触碰 Git')):
                    paths, source = verify_configs.load_icon_reference()
                self.assertIsNone(paths)
                self.assertIn('格式不合法', source)
                self.assertFalse(verify_configs.check_icons_exist([], 'x', (paths, source)))

    def test_pinned_icon_revision_only_accepts_full_sha(self):
        for bad in ('main', 'HEAD', 'f0f3bc2', '', 'z' * 40, None, 12345, '   '):
            with self.subTest(revision=bad):
                with patch.object(verify_configs, '_manifest_revision', return_value=bad):
                    self.assertIsNone(verify_configs.pinned_icon_revision())
        with patch.object(verify_configs, '_manifest_revision', return_value='A' * 40):
            self.assertEqual(verify_configs.pinned_icon_revision(), 'A' * 40)

    def test_real_manifest_revision_is_a_full_sha(self):
        revision = verify_configs.pinned_icon_revision()
        self.assertIsNotNone(revision, '真实 manifest 的 revision 必须是完整 40 位 SHA')
        self.assertRegex(revision or '', r'\A[0-9a-f]{40}\Z')

    def test_no_hardcoded_approved_sha_in_verify_configs(self):
        """校验基准只来自 manifest：不得在 verify_configs.py 里硬编码批准 SHA。"""
        revision = verify_configs.pinned_icon_revision()
        self.assertIsNotNone(revision)
        source = Path(verify_configs.__file__).read_text(encoding='utf-8')
        self.assertNotIn(revision, source)
        self.assertNotIn('APPROVED_OASIC_REPOSITORY', source)

    def test_manifest_revision_is_read_through_the_shared_manifest(self):
        """不得复制 manifest 解析逻辑：仍走 match_icons.load_manifest()。"""
        with patch.object(verify_configs.match_icons, 'load_manifest',
                          return_value={'revision': 'B' * 40}) as loader:
            self.assertEqual(verify_configs.pinned_icon_revision(), 'B' * 40)
        loader.assert_called_once()

    def test_comment_order_length_mismatch_fails(self):
        """注释 RULE-SET 少于品牌组 → 必须 FAIL（旧实现 zip 截断后 PASS）"""
        lines = [
            'proxy-groups:\n',
            '  - name: "Netflix"\n',
            '  - name: "Spotify"\n',
            'rules:\n',
            '                                                    # - RULE-SET,Netflix,Netflix\n',
        ]
        orig = verify_configs.SYSTEM_GROUPS
        verify_configs.SYSTEM_GROUPS = []
        try:
            ok = verify_configs.check_full_comment_order(lines, 'x_full')
        finally:
            verify_configs.SYSTEM_GROUPS = orig
        self.assertFalse(ok, "注释行数少于品牌组数时必须 FAIL")

    def test_comment_order_base_comment_ignored(self):
        """基础集注释（Applications）不计入品牌注释段"""
        lines = [
            'rules:\n',
            '                                                    # - RULE-SET,Applications,🎯 全球直连\n',
            '                                                    # - RULE-SET,Netflix,Netflix\n',
        ]
        orig = verify_configs.SYSTEM_GROUPS
        verify_configs.SYSTEM_GROUPS = []
        try:
            ok = verify_configs.check_full_comment_order(lines, 'x_full')
        finally:
            verify_configs.SYSTEM_GROUPS = orig
        self.assertFalse(ok, "只有基础集注释时不应被当作品牌注释段通过")

    def test_emoji_group_with_icon_fails(self):
        """emoji 前缀组带 icon → FAIL"""
        lines = [
            '  - name: "🤖 General AI"\n',
            '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/AI/GeneralAI/GeneralAI.png"\n',
        ]
        self.assertFalse(verify_configs.check_emoji_groups_have_no_icon(lines, 'x'))

    def test_non_emoji_group_with_icon_passes(self):
        lines = [
            '  - name: "Netflix"\n',
            '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Media/Netflix/Netflix.png"\n',
        ]
        self.assertTrue(verify_configs.check_emoji_groups_have_no_icon(lines, 'x'))

    def test_quoted_strategy_any_group_detected(self):
        """任意被引号包住的出站目标都算违规，不依赖具体组名"""
        bad = ['rules:\n', '  - RULE-SET,Direct,"♻️ 自动选择"\n']
        good = ['rules:\n', '  - RULE-SET,Direct,♻️ 自动选择\n']
        self.assertFalse(verify_configs.check_rules_no_quoted_strategy(bad, 'x'))
        self.assertTrue(verify_configs.check_rules_no_quoted_strategy(good, 'x'))


if __name__ == '__main__':
    unittest.main()
