"""基础集域名策略测试。

旧行为（已废弃，本文件按新口径断言）：
- 无 "+." 的有点域名曾一律写成 DOMAIN-SUFFIX
- Applications 曾按小写合并，删掉 Tailscale / Tailscale.exe
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.canonical import CanonicalRule
from lib.policy import is_allowed_bare_suffix, is_bare, is_single_char
import parse_loyalsoldier as ls
import verify_rulesets


class TestTypeDetection(unittest.TestCase):
    def test_dotted_without_plus_is_domain(self):
        self.assertEqual(ls.detect_rule_type("a.b.com"), "DOMAIN")

    def test_plus_prefix_is_suffix(self):
        self.assertEqual(ls.detect_rule_type("+.a.b.com"), "DOMAIN-SUFFIX")

    def test_google_suffix_unchanged(self):
        self.assertEqual(ls.detect_rule_type("+.google.com"), "DOMAIN-SUFFIX")
        self.assertEqual(ls.extract_value("+.google.com"), "google.com")


class TestExactCoveredBySuffix(unittest.TestCase):
    def test_same_value_keeps_suffix_only(self):
        rules = [
            CanonicalRule("DOMAIN", "foo.com", source="loyalsoldier"),
            CanonicalRule("DOMAIN-SUFFIX", "foo.com", source="loyalsoldier"),
        ]
        kept, dropped = ls.drop_exact_covered_by_suffix(rules)
        self.assertEqual([(r.rule_type, r.value) for r in kept], [("DOMAIN-SUFFIX", "foo.com")])
        self.assertEqual(dropped, 1)

    def test_different_values_both_kept(self):
        rules = [
            CanonicalRule("DOMAIN", "itunes.apple.com", source="loyalsoldier"),
            CanonicalRule("DOMAIN-SUFFIX", "hls.itunes.apple.com", source="loyalsoldier"),
        ]
        kept, dropped = ls.drop_exact_covered_by_suffix(rules)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 2)


class TestBareExactMode(unittest.TestCase):
    def test_drop_discards_and_logs(self):
        content = "payload:\n  - 'itunes.apple.com'\n  - '+.google.com'\n"
        buf = io.StringIO()
        with redirect_stdout(buf):
            rules = ls.parse_loyalsoldier_yaml(content, bare_exact="drop")
        types = {(r.rule_type, r.value) for r in rules}
        self.assertNotIn(("DOMAIN", "itunes.apple.com"), types)
        self.assertIn(("DOMAIN-SUFFIX", "google.com"), types)
        self.assertIn("itunes.apple.com", buf.getvalue())


class TestBareAllowlist(unittest.TestCase):
    def test_proxy_drops_brands_keeps_punycode(self):
        self.assertFalse(is_allowed_bare_suffix("Proxy", "microsoft"))
        self.assertFalse(is_allowed_bare_suffix("Proxy", "google"))
        self.assertTrue(is_allowed_bare_suffix("Proxy", "xn--cg4bki"))

    def test_direct_keeps_cn_drops_baidu(self):
        self.assertTrue(is_allowed_bare_suffix("Direct", "cn"))
        self.assertFalse(is_allowed_bare_suffix("Direct", "baidu"))

    def test_single_char_never_allowed(self):
        self.assertTrue(is_single_char("a"))
        for ruleset in ("Direct", "Proxy", "Private"):
            self.assertFalse(is_allowed_bare_suffix(ruleset, "a"))

    def test_private_is_not_on_the_table(self):
        self.assertTrue(is_bare("lan"))
        self.assertFalse(is_allowed_bare_suffix("Private", "lan"))


class TestPrivateAndBrandBare(unittest.TestCase):
    def _basic(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in (
                "reject.txt", "lancidr.txt", "private.txt", "direct.txt",
                "cncidr.txt", "proxy.txt", "applications.txt",
            ):
                (base / name).write_text(files.get(name, "payload:\n"), encoding="utf-8")
            return ls.parse_loyalsoldier_basic(str(base))

    def test_private_keeps_eight_bare_words(self):
        words = [
            "internal", "localdomain", "example", "invalid",
            "localhost", "test", "local", "lan",
        ]
        extra = "not-on-any-list"
        payload = "payload:\n" + "".join(f"  - '{w}'\n" for w in words + [extra])
        data = self._basic({"private.txt": payload})
        values = [r.value for r in data["Private"]["rules"]]
        for word in words:
            self.assertIn(word, values)
        self.assertIn(extra, values)

    def test_brand_files_drop_bare(self):
        content = "payload:\n  - 'icloudword'\n  - '+.icloud.com'\n  - 'exact.icloud.com'\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "icloud.txt"
            path.write_text(content, encoding="utf-8")
            (Path(tmp) / "telegramcidr.txt").write_text(
                "payload:\n  - 'telegramword'\n  - '1.1.1.1/32'\n", encoding="utf-8")
            icloud = ls.parse_loyalsoldier_brand("iCloud", tmp)
            telegram = ls.parse_loyalsoldier_brand("Telegram", tmp)
        self.assertEqual({(r.rule_type, r.value) for r in icloud}, {
            ("DOMAIN-SUFFIX", "icloud.com"),
            ("DOMAIN", "exact.icloud.com"),
        })
        self.assertTrue(all("." in r.value or r.rule_type.startswith("IP-") for r in telegram))


def _ruleset(root, brand, payload_lines, header_counts):
    folder = Path(root) / "ruleset" / brand
    folder.mkdir(parents=True)
    header = [
        "# ===========================================",
        f"# Rule Name: {brand}",
        "# Author: Hawaiine",
        "# Updated: 2026-09-24 00:00:00",
    ]
    for kind, count in header_counts.items():
        header.append(f"# {kind}: {count}")
    header += ["# ===========================================", "payload:"]
    body = "\n".join(f"  - {line}" for line in payload_lines)
    (folder / f"{brand}.yaml").write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")
    (folder / "README.md").write_text(
        f"# 📦 {brand} 规则集\n\n- **behavior**: classical\n- **策略组**: {brand}\n",
        encoding="utf-8",
    )


class TestVerifyBarePolicy(unittest.TestCase):
    def _check(self, brand, lines, counts):
        original = verify_rulesets.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _ruleset(tmp, brand, lines, counts)
            verify_rulesets.ROOT = Path(tmp)
            try:
                return verify_rulesets.check_brand(brand)
            finally:
                verify_rulesets.ROOT = original

    def test_direct_baidu_fails_cn_passes(self):
        ok, errors = self._check("Direct", ["DOMAIN-SUFFIX,baidu"], {"DOMAIN-SUFFIX": 1})
        self.assertFalse(ok)
        self.assertTrue(any("baidu" in e for e in errors))
        ok, errors = self._check("Direct", ["DOMAIN-SUFFIX,cn"], {"DOMAIN-SUFFIX": 1})
        self.assertTrue(ok, errors)

    def test_private_lan_passes(self):
        ok, errors = self._check("Private", ["DOMAIN-SUFFIX,lan"], {"DOMAIN-SUFFIX": 1})
        self.assertTrue(ok, errors)

    def test_single_char_fails_everywhere(self):
        for brand in ("Direct", "Proxy", "Private", "Netflix"):
            ok, errors = self._check(brand, ["DOMAIN-SUFFIX,a"], {"DOMAIN-SUFFIX": 1})
            self.assertFalse(ok, brand)
            self.assertTrue(any("单字符" in e for e in errors), errors)

    def test_applications_keeps_both_cases(self):
        ok, errors = self._check(
            "Applications",
            ["PROCESS-NAME,tailscale", "PROCESS-NAME,Tailscale"],
            {"PROCESS-NAME": 2},
        )
        self.assertTrue(ok, errors)

    def test_exact_duplicate_fails(self):
        for brand in ("Applications", "Netflix"):
            line = "PROCESS-NAME,tailscale" if brand == "Applications" else "DOMAIN-SUFFIX,netflix.com"
            kind = line.split(",", 1)[0]
            ok, errors = self._check(brand, [line, line], {kind: 2})
            self.assertFalse(ok, brand)
            self.assertTrue(any("完全重复" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
