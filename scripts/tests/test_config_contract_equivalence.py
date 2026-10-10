"""test_config_contract_equivalence.py — C1/C2 四份配置契约的永久回归测试。

C1 ``check_full_min_equivalence``：同平台 full 与 min 在去空行/去注释后必须逐行相等。
C2 ``check_platform_contract``：Android 与 Nikki 的结构差异必须完全等于已审查契约，
   且每个平台专属字段的**实际值**必须等于该平台期望值——路径白名单只说明「允许不同」，
   本表进一步约束「各自应该是多少」，改成第三个值同样必须失败。

覆盖：正样本（真实四份配置）、C1 负样本、C2 负样本、防误报样本、归一化器单元测试。
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verify_configs as vc  # noqa: E402

VARIANTS = {
    "android_full": "configs/Android/config.yaml",
    "android_min": "configs/Android/config.min.yaml",
    "nikki_full": "configs/Nikki/config.yaml",
    "nikki_min": "configs/Nikki/config.min.yaml",
}

ALIBABA_URL = ("https://raw.githubusercontent.com/Hawaiine/mihomo-rules/"
               "main/ruleset/Alibaba/Alibaba.yaml")


class ConfigContractTestCase(unittest.TestCase):
    """把真实四份配置复制到临时目录，变异后调用契约检查函数。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cfg-contract-")
        self.root = Path(self._tmp.name)
        self.configs = {}
        for variant, rel in VARIANTS.items():
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_ROOT / rel, dst)
            self.configs[variant] = str(dst)

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------- #

    def path(self, variant):
        return Path(self.configs[variant])

    def text(self, variant):
        return self.path(variant).read_text(encoding="utf-8")

    def edit(self, variant, old, new, count=1):
        text = self.text(variant)
        self.assertIn(old, text, f"{variant}: 未找到待替换文本 {old[:70]!r}")
        self.path(variant).write_text(text.replace(old, new, count), encoding="utf-8")

    def append(self, variant, snippet):
        """在临时配置末尾追加内容（顶层新键/注释/多行标量用）。"""
        path = self.path(variant)
        path.write_text(self.text(variant).rstrip("\n") + "\n" + snippet, encoding="utf-8")

    def problems(self):
        return vc.check_full_min_equivalence(self.configs) + vc.check_platform_contract(self.configs)

    def assert_contract_ok(self):
        self.assertEqual(self.problems(), [], "契约检查不应报错")

    def assert_contract_fails(self, needle=None):
        problems = self.problems()
        self.assertTrue(problems, "契约检查应当失败，但没有报错")
        if needle is not None:
            joined = "\n".join(problems)
            self.assertIn(needle, joined, f"失败信息未包含 {needle!r}:\n{joined}")


class FullMinEquivalenceTest(ConfigContractTestCase):
    """C1：同平台 full/min 整份等价。"""

    def test_real_configs_pass(self):
        self.assert_contract_ok()

    def test_min_extra_literal_domain_rule_fails(self):
        """旧检查只提取 RULE-SET/MATCH/GEOIP，看不见字面 DOMAIN 漂移。"""
        self.edit("android_min", "rules:\n", "rules:\n  - DOMAIN,drift-probe.example.com,🎯 全球直连\n")
        self.assert_contract_fails("android")

    def test_min_extra_ip_cidr_rule_fails(self):
        self.edit("nikki_min", "rules:\n", "rules:\n  - IP-CIDR,203.0.113.0/24,🎯 全球直连\n")
        self.assert_contract_fails("nikki")

    def test_min_provider_url_change_fails(self):
        self.edit("nikki_min", f'url: "{ALIBABA_URL}"',
                  'url: "https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main/ruleset/AlibabaX/AlibabaX.yaml"')
        self.assert_contract_fails("nikki")

    def test_min_provider_path_change_fails(self):
        self.edit("nikki_min", "path: ./ruleset/Alibaba.yaml", "path: ./ruleset/AlibabaX.yaml")
        self.assert_contract_fails("nikki")

    def test_full_dns_change_not_synced_to_min_fails(self):
        self.edit("android_full", "listen: 127.0.0.1:53", "listen: 127.0.0.1:5353")
        self.assert_contract_fails("android")

    def test_full_tun_change_not_synced_to_min_fails(self):
        self.edit("nikki_full", "  stack: mixed", "  stack: gvisor")
        self.assert_contract_fails("nikki")

    def test_full_proxy_group_rename_not_synced_to_min_fails(self):
        self.edit("nikki_full", '- name: "阿里巴巴"', '- name: "阿里巴巴-改名"')
        self.assert_contract_fails("nikki")

    def test_min_proxy_provider_change_not_synced_fails(self):
        self.edit("android_min", "path: ./providers/provider1.yaml", "path: ./providers/provider1-x.yaml")
        self.assert_contract_fails("android")

    def test_provider_removed_from_min_fails(self):
        self.edit("nikki_min", f'  Alibaba:\n    type: http\n    behavior: classical\n'
                               f'    url: "{ALIBABA_URL}"\n    interval: 86400\n'
                               f'    path: ./ruleset/Alibaba.yaml\n', "")
        self.assert_contract_fails("nikki")

    # -- 防误报 ---------------------------------------------------------- #

    def test_whole_line_comment_added_is_not_flagged(self):
        self.edit("android_full", "# Configuration: mihomo for Android",
                  "# Configuration: mihomo for Android\n# 仅注释变更，不应触发失败")
        self.edit("nikki_min", "rules:\n", "# 仅注释变更\nrules:\n")
        self.assert_contract_ok()

    def test_inline_comment_rewritten_is_not_flagged(self):
        self.edit("android_full", "# 开启 DNS 解析", "# 说明文字改写")
        self.edit("nikki_full", "# 开启 TUN", "# 另一段说明")
        self.assert_contract_ok()

    def test_blank_lines_removed_or_added_is_not_flagged(self):
        text = self.text("android_min")
        self.path("android_min").write_text(text.replace("rules:\n", "\nrules:\n\n", 1), encoding="utf-8")
        self.assert_contract_ok()


class MultiLineScalarEquivalenceTest(ConfigContractTestCase):
    """C1 必须感知 YAML 多行标量：块标量 / 跨行引号标量里的 ``#`` 是正文。

    这些用例在**临时配置文件**上直接驱动 ``check_full_min_equivalence``，
    而不是只做字符串单元测试——只有这样才能证明整条 C1 路径不会漏检。
    """

    BLOCK = "x-audit-probe: |\n  first\n  {marker}\n  last\n"
    QUOTED = 'x-audit-probe: "first\n  {marker}\n  last"\n'

    def assert_c1_ok(self):
        self.assertEqual(vc.check_full_min_equivalence(self.configs), [], "C1 不应报错")

    def assert_c1_fails(self, needle=None):
        problems = vc.check_full_min_equivalence(self.configs)
        self.assertTrue(problems, "C1 应当失败但没有报错")
        if needle is not None:
            self.assertIn(needle, "\n".join(problems))

    # -- 块标量 ---------------------------------------------------------- #

    def test_identical_block_scalar_bodies_pass(self):
        self.append("android_full", self.BLOCK.format(marker="# SAME"))
        self.append("android_min", self.BLOCK.format(marker="# SAME"))
        self.assert_c1_ok()

    def test_block_scalar_body_difference_fails(self):
        """块标量正文里的 # 行是内容：FULL_ONLY / MIN_ONLY 不同必须失败。"""
        self.append("android_full", self.BLOCK.format(marker="# FULL_ONLY"))
        self.append("android_min", self.BLOCK.format(marker="# MIN_ONLY"))
        self.assert_c1_fails("android")

    def test_block_scalar_body_difference_in_nikki_fails(self):
        self.append("nikki_full", self.BLOCK.format(marker="# FULL_ONLY"))
        self.append("nikki_min", self.BLOCK.format(marker="# MIN_ONLY"))
        self.assert_c1_fails("nikki")

    def test_block_scalar_body_difference_not_hidden_by_comment_stripping(self):
        """回归：修复前归一化器会把这两行都当注释删掉，从而误判相等。"""
        full_text = self.BLOCK.format(marker="# FULL_ONLY")
        min_text = self.BLOCK.format(marker="# MIN_ONLY")
        self.assertNotEqual(vc.normalize_config_lines(full_text),
                            vc.normalize_config_lines(min_text))
        self.assertIn("  # FULL_ONLY", vc.normalize_config_lines(full_text))

    def test_block_scalar_indented_under_sequence_item(self):
        """序列项下的块标量同样不得把正文当注释。"""
        snippet = "- name: probe\n  value: |\n    line1\n    {marker}\n    line2\n"
        self.append("android_full", snippet.format(marker="# FULL_ONLY"))
        self.append("android_min", snippet.format(marker="# MIN_ONLY"))
        self.assert_c1_fails("android")

    def test_folded_block_scalar_body_difference_fails(self):
        snippet = "x-folded-probe: >\n  alpha\n  {marker}\n  beta\n"
        self.append("nikki_full", snippet.format(marker="# FULL_ONLY"))
        self.append("nikki_min", snippet.format(marker="# MIN_ONLY"))
        self.assert_c1_fails("nikki")

    # -- 跨行引号标量 ---------------------------------------------------- #

    def test_quoted_scalar_body_difference_fails(self):
        self.append("android_full", self.QUOTED.format(marker="# FULL_ONLY"))
        self.append("android_min", self.QUOTED.format(marker="# MIN_ONLY"))
        self.assert_c1_fails("android")

    def test_identical_quoted_scalar_bodies_pass(self):
        self.append("nikki_full", self.QUOTED.format(marker="# SAME"))
        self.append("nikki_min", self.QUOTED.format(marker="# SAME"))
        self.assert_c1_ok()

    def test_quoted_scalar_body_hash_is_not_stripped(self):
        lines = vc.normalize_config_lines(self.QUOTED.format(marker="# KEEP_ME"))
        self.assertTrue(any("# KEEP_ME" in line for line in lines), lines)

    # -- 不得回归 -------------------------------------------------------- #

    def test_plain_scalar_apostrophe_does_not_open_a_multiline_scalar(self):
        """普通标量里的撇号不能把后续行误当成标量正文。"""
        text = "note: don't\nkey: 1\n# comment\n"
        self.assertEqual(vc.normalize_config_lines(text), ["note: don't", "key: 1"])

    def test_mapping_key_order_change_between_full_and_min_fails(self):
        """结构投影必须保留 mapping 键顺序：仅调换键顺序也要失败。"""
        self.edit("android_min", "\nport: 7891\n", "\n")
        self.edit("android_min", "\nsocks-port: 7892\n", "\nsocks-port: 7892\nport: 7891\n")
        self.assert_c1_fails("android")

    def test_real_configs_still_pass(self):
        self.assert_c1_ok()

    def test_comment_and_blank_only_differences_still_pass(self):
        self.append("android_full", "# 仅注释\n\n")
        self.append("android_min", "# 另一段注释\n")
        self.assert_c1_ok()


class PlainScalarApostropheTest(ConfigContractTestCase):
    """plain scalar 里的撇号不得吞掉其后的行内注释（C1 误报修复）。

    ``_scan_line()`` 遇到撇号会先当作单引号开启；当它并不处于合法 quoted scalar
    起始位置（``note: don't`` 的撇号）时，必须**整行重扫**为 plain scalar。
    只把引号状态清零而不重扫，会把 ``# comment`` 留在内容里，使 full/min 仅注释
    文字不同、YAML 值完全相同时被 C1 误判为语义差异。
    """

    SAMPLE = "note: don't # {marker} comment\n"

    # -- 归一化器 -------------------------------------------------------- #

    def test_comment_after_plain_scalar_apostrophe_is_ignored(self):
        self.assertEqual(vc.normalize_config_lines("note: don't # full comment"),
                         ["note: don't"])
        self.assertEqual(vc.normalize_config_lines("note: don't # full comment"),
                         vc.normalize_config_lines("note: don't # min comment"))

    def test_plain_scalar_apostrophe_does_not_open_multiline_state(self):
        self.assertEqual(vc.normalize_config_lines("note: don't\nnext: 1\n"),
                         ["note: don't", "next: 1"])

    def test_plain_scalar_matches_yaml_semantics_for_embedded_quote(self):
        """与 YAML 真值对齐：plain scalar 里的 `` #`` 同样起始注释。"""
        import yaml

        sample = 'note: don\'t use "a #b"'
        self.assertEqual(yaml.safe_load(sample)["note"], 'don\'t use "a')
        self.assertEqual(vc.normalize_config_lines(sample), ['note: don\'t use "a'])

    def test_quoted_hash_is_still_preserved(self):
        self.assertEqual(vc.strip_inline_comment("key: 'a#b'  # note"), "key: 'a#b'")
        self.assertEqual(vc.strip_inline_comment('key: "a#b"  # note'), 'key: "a#b"')
        self.assertEqual(vc.strip_inline_comment("key: 'it''s # here'  # note"),
                         "key: 'it''s # here'")

    def test_plain_scalar_inline_comment_difference_is_ignored(self):
        self.assertEqual(vc.normalize_config_lines("note: plain # full"),
                         vc.normalize_config_lines("note: plain # min"))

    def test_block_scalar_indicator_order_variants_are_recognised(self):
        """YAML 允许缩进指示符与 chomping 指示符任意次序（|2- 与 |-2 等价）。"""
        for marker in ("|", "|-", "|+", ">", ">-", "|2", "|-2", "|2-", ">2-"):
            with self.subTest(marker=marker):
                lines = vc.normalize_config_lines(f"x: {marker}\n  a\n  # KEEP\n")
                self.assertIn("  # KEEP", lines, f"{marker} 未被识别为块标量")

    # -- 整条 C1 路径 ---------------------------------------------------- #

    def test_comment_only_difference_after_apostrophe_passes_c1(self):
        """注入四份临时配置：仅注释文字不同，C1 不得失败。"""
        for platform in ("android", "nikki"):
            with self.subTest(platform=platform):
                self.append(f"{platform}_full", self.SAMPLE.format(marker="full"))
                self.append(f"{platform}_min", self.SAMPLE.format(marker="min"))
                self.assertEqual(vc.check_full_min_equivalence(self.configs), [],
                                 "仅注释文字不同不得让 C1 失败")

    def test_block_scalar_body_difference_still_fails_after_fix(self):
        for platform in ("android", "nikki"):
            with self.subTest(platform=platform):
                self.append(f"{platform}_full", "x-probe: |\n  a\n  # FULL_ONLY\n")
                self.append(f"{platform}_min", "x-probe: |\n  a\n  # MIN_ONLY\n")
                self.assertNotEqual(vc.check_full_min_equivalence(self.configs), [])

    def test_multiline_quoted_body_difference_still_fails_after_fix(self):
        self.append("android_full", 'x-probe: "a\n  # FULL_ONLY\n  b"\n')
        self.append("android_min", 'x-probe: "a\n  # MIN_ONLY\n  b"\n')
        self.assertNotEqual(vc.check_full_min_equivalence(self.configs), [])


class NormalizerUnitTest(unittest.TestCase):
    """归一化器必须识别引号与转义，不得粗暴按 '#' 截断。"""

    def test_plain_inline_comment_is_stripped(self):
        self.assertEqual(vc.strip_inline_comment("key: value  # note"), "key: value")

    def test_hash_without_preceding_space_is_not_a_comment(self):
        self.assertEqual(vc.strip_inline_comment("key: value#nothash"), "key: value#nothash")

    def test_double_quoted_hash_is_preserved(self):
        self.assertEqual(vc.strip_inline_comment('key: "a#b"  # note'), 'key: "a#b"')

    def test_single_quoted_hash_is_preserved(self):
        self.assertEqual(vc.strip_inline_comment("key: 'a#b'  # note"), "key: 'a#b'")

    def test_escaped_double_quote_inside_string(self):
        self.assertEqual(vc.strip_inline_comment('key: "a\\"#b"  # note'), 'key: "a\\"#b"')

    def test_doubled_single_quote_escape(self):
        self.assertEqual(vc.strip_inline_comment("key: 'it''s # here'  # note"), "key: 'it''s # here'")

    def test_whole_line_comment_becomes_empty(self):
        self.assertEqual(vc.strip_inline_comment("   # whole line"), "")

    def test_normalize_drops_blank_and_comment_lines(self):
        text = "a: 1\n\n# comment\nb: 2  # inline\n"
        self.assertEqual(vc.normalize_config_lines(text), ["a: 1", "b: 2"])


class PlatformContractTest(ConfigContractTestCase):
    """C2：Android/Nikki 平台差异契约（路径 + 期望值双重约束）。"""

    def test_real_configs_pass(self):
        self.assert_contract_ok()

    # -- 值必须正确：白名单路径也不许改成第三个值 ------------------------ #

    def test_port_changed_to_third_value_fails(self):
        """port 本就是允许不同的路径；改成第三个值必须失败。"""
        self.edit("android_full", "port: 7891", "port: 9999")
        self.assert_contract_fails("port")

    def test_socks_port_changed_to_third_value_fails(self):
        self.edit("nikki_full", "socks-port: 1080", "socks-port: 1081")
        self.assert_contract_fails("socks-port")

    def test_find_process_mode_changed_to_wrong_value_fails(self):
        # 注意：Nikki 配置里的说明注释也含 "find-process-mode: off" 字样，
        # 必须用换行锚定到真正的键行，否则只会改到注释。
        self.edit("nikki_full", "\nfind-process-mode: off\n", "\nfind-process-mode: strict\n")
        self.assert_contract_fails("find-process-mode")

    def test_tun_enable_changed_to_wrong_value_fails(self):
        self.edit("nikki_full", "  enable: true", "  enable: false")
        self.assert_contract_fails("tun.enable")

    def test_dns_listen_changed_to_wrong_value_fails(self):
        self.edit("nikki_full", "listen: 0.0.0.0:1053", "listen: 0.0.0.0:1054")
        self.assert_contract_fails("dns.listen")

    def test_external_controller_changed_to_third_value_fails(self):
        self.edit("android_full", "external-controller: 127.0.0.1:9090",
                  "external-controller: 127.0.0.1:9091")
        self.assert_contract_fails("external-controller")

    def test_tun_device_changed_to_wrong_value_fails(self):
        self.edit("nikki_full", "  device: nikki", "  device: utun")
        self.assert_contract_fails("tun.device")

    # -- 路径必须已批准：未批准的差异一律失败 ---------------------------- #

    def test_brand_group_added_only_in_android_fails(self):
        self.edit("android_full", '  - name: "阿里巴巴"\n',
                  '  - name: "额外品牌组"\n    type: select\n    proxies:\n      - "🎯 全球直连"\n  - name: "阿里巴巴"\n')
        self.assert_contract_fails("proxy-groups")

    def test_brand_group_renamed_fails(self):
        self.edit("nikki_full", '- name: "阿里巴巴"', '- name: "Alibaba_OLD"')
        self.assert_contract_fails()

    def test_rule_provider_added_only_in_android_fails(self):
        self.edit("android_full", "  Alibaba:\n    type: http",
                  "  ZZTestBrand:\n    type: http\n    behavior: classical\n"
                  f'    url: "https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main/ruleset/ZZTestBrand/ZZTestBrand.yaml"\n'
                  "    interval: 86400\n    path: ./ruleset/ZZTestBrand.yaml\n  Alibaba:\n    type: http")
        self.assert_contract_fails()

    def test_rule_provider_removed_from_nikki_fails(self):
        self.edit("nikki_full", f'  Alibaba:\n    type: http\n    behavior: classical\n'
                                f'    url: "{ALIBABA_URL}"\n    interval: 86400\n'
                                f'    path: ./ruleset/Alibaba.yaml\n', "")
        self.assert_contract_fails()

    def test_proxy_provider_changed_only_in_android_fails(self):
        self.edit("android_full", "path: ./providers/provider1.yaml", "path: ./providers/provider1-x.yaml")
        self.assert_contract_fails("proxy-providers")

    def test_top_level_key_order_change_fails(self):
        self.edit("android_full", "port: 7891\n", "")
        self.edit("android_full", "socks-port: 7892\n", "socks-port: 7892\nport: 7891\n")
        self.assert_contract_fails()

    # -- rules 段：只允许精确的 Applications 差异 ------------------------ #

    def test_extra_rule_in_android_fails(self):
        self.edit("android_full", "rules:\n", "rules:\n  - DOMAIN,extra.example.com,🎯 全球直连\n")
        self.assert_contract_fails("rules")

    def test_extra_rule_in_nikki_fails(self):
        self.edit("nikki_full", "rules:\n", "rules:\n  - DOMAIN,extra.example.com,🎯 全球直连\n")
        self.assert_contract_fails("rules")

    def test_rule_order_change_fails(self):
        text = self.text("android_full")
        first, second = "- RULE-SET,DirectDNS,🇨🇳 直连DNS", "- RULE-SET,ProxyDNS,🌍 代理DNS"
        self.assertIn(first, text)
        self.edit("android_full", f"  {first}\n  {second}\n", f"  {second}\n  {first}\n")
        self.assert_contract_fails("rules")

    def test_applications_rule_removed_from_android_fails(self):
        self.edit("android_full", "  - RULE-SET,Applications,🎯 全球直连\n", "")
        self.assert_contract_fails("Applications")

    def test_applications_rule_added_to_nikki_fails(self):
        self.edit("nikki_full", "rules:\n", "rules:\n  - RULE-SET,Applications,🎯 全球直连\n")
        self.assert_contract_fails("Applications")

    # -- 防误报 ---------------------------------------------------------- #

    def test_comment_only_change_is_not_flagged(self):
        self.edit("android_full", "# TUN 虚拟网卡 (Android 端关闭, 使用 VPN 模式)",
                  "# TUN 虚拟网卡（说明改写）")
        self.edit("nikki_full", "# 开启 TUN", "# 开启 TUN（说明改写）")
        self.assert_contract_ok()


if __name__ == "__main__":
    unittest.main()
