"""生产图标 URL 可达性检查（scripts/icon_urls.py）的单元测试。

原则：
* 默认不依赖公网——分类 / 探测 / 去重 / 重试全部通过注入 ``fetcher`` 驱动；
* 另有一组用**本地 HTTP 服务器**验证真实 fetcher（重定向、Range、状态码）；
* URL 清单必须来自仓库实际生产配置，而不是测试另写一套模板。
"""
from pathlib import Path
import http.server
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import icon_urls  # noqa: E402
import match_icons  # noqa: E402


class StubFetcher:
    """按 ``{(url, method): status|Exception}`` 应答的可注入 fetcher。

    未登记的请求直接抛错，避免测试悄悄放过意料外的探测。
    """

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, url, method='HEAD', timeout=10.0, headers=None):
        self.calls.append((url, method, bool(headers)))
        value = self.mapping.get((url, method))
        if value is None:
            raise AssertionError(f'未预期的请求: {url} {method}')
        if isinstance(value, Exception):
            raise value
        return value, {}


class SequenceFetcher:
    """按给定状态序列依次应答（用于验证重试后成功）。"""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def __call__(self, url, method='HEAD', timeout=10.0, headers=None):
        self.calls.append((url, method, bool(headers)))
        return self.statuses.pop(0), {}


def make_fetcher(mapping):
    return StubFetcher(mapping)


class UrlSourceTest(unittest.TestCase):
    """URL 必须来自实际生产配置，且落在 manifest 声明的生产基址内。"""

    def test_production_config_paths_cover_the_four_configs(self):
        names = [p.relative_to(ROOT).as_posix() for p in icon_urls.production_config_paths()]
        self.assertEqual(names, [
            'configs/Android/config.min.yaml',
            'configs/Android/config.yaml',
            'configs/Nikki/config.min.yaml',
            'configs/Nikki/config.yaml',
        ])

    def test_real_configs_yield_urls_on_the_manifest_base(self):
        urls = icon_urls.production_icon_urls()
        self.assertTrue(urls, '生产配置里应当引用图标 URL')
        self.assertEqual(len(urls), len(set(urls)), 'URL 必须已去重')
        self.assertEqual(urls, sorted(urls), 'URL 必须有序（结果可复现）')
        base = match_icons.asset_url_base(match_icons.load_manifest())
        self.assertEqual(icon_urls.policy_problems(urls), [])
        for url in urls:
            self.assertTrue(url.startswith(base + '/'), url)

    def test_urls_are_read_from_the_configs_not_a_hardcoded_template(self):
        """换一份配置，URL 清单随之改变——证明检查的是生成产物本身。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'configs' / 'X').mkdir(parents=True)
            (root / 'configs' / 'X' / 'config.yaml').write_text(
                'proxy-groups:\n  - name: "A"\n'
                '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/A/a.png"\n',
                encoding='utf-8')
            (root / 'configs' / 'X' / 'config.min.yaml').write_text(
                'proxy-groups:\n  - name: "A"\n'
                '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/A/a.png"\n'
                '  - name: "B"\n'
                '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/B/b.png"\n',
                encoding='utf-8')
            urls = icon_urls.production_icon_urls(root=root)
        self.assertEqual(urls, [
            'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/A/a.png',
            'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/B/b.png',
        ])

    def test_policy_problems_flag_off_base_urls(self):
        manifest = match_icons.load_manifest()
        good = match_icons.asset_url_base(manifest) + '/A/a.png'
        bad_host = 'https://evil.example.com/Hawaiine/Oasisic-Icons/main/icons/A/a.png'
        bad_ref = 'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/dev/icons/A/a.png'
        bad_repo = 'https://raw.githubusercontent.com/Evil/Oasisic-Icons/main/icons/A/a.png'
        self.assertEqual(icon_urls.policy_problems([good], manifest), [])
        self.assertEqual(icon_urls.policy_problems([good, bad_host, bad_ref, bad_repo], manifest),
                         sorted([bad_host, bad_ref, bad_repo]))

    def test_production_url_ref_is_still_main(self):
        """本阶段不得把生产 URL 从 main 改成 SHA。"""
        manifest = match_icons.load_manifest()
        self.assertEqual(manifest['asset_url_mode'], 'branch-main')
        self.assertEqual(manifest['production_url_ref'], 'main')
        for url in icon_urls.production_icon_urls():
            self.assertIn('/main/icons/', url)
            self.assertNotIn(manifest['revision'], url)


class ClassifyTest(unittest.TestCase):
    def test_2xx_is_present(self):
        self.assertEqual(icon_urls.classify(200), icon_urls.PRESENT)
        self.assertEqual(icon_urls.classify(206), icon_urls.PRESENT)

    def test_trusted_404_is_missing(self):
        self.assertEqual(icon_urls.classify(404), icon_urls.MISSING)

    def test_403_429_and_5xx_are_undetermined_never_present(self):
        for status in (400, 401, 403, 405, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(icon_urls.classify(status), icon_urls.UNDETERMINED)

    def test_errors_are_undetermined(self):
        for exc in (TimeoutError('t'), ConnectionResetError('r'), OSError('o')):
            with self.subTest(exc=type(exc).__name__):
                kind = icon_urls.classify(None, f'{type(exc).__name__}: {exc}')
                self.assertEqual(kind, icon_urls.UNDETERMINED)

    def test_undetermined_is_not_reported_as_present(self):
        self.assertNotEqual(icon_urls.classify(429), icon_urls.PRESENT)
        self.assertNotEqual(icon_urls.classify(None, 'boom'), icon_urls.PRESENT)


class ProbeTest(unittest.TestCase):
    def test_present_on_head_stops_immediately(self):
        fetcher = make_fetcher({('u', 'HEAD'): 200})
        kind, attempts = icon_urls.probe('u', fetcher, retries=3, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.PRESENT)
        self.assertEqual(attempts, ['HEAD -> 200'])
        self.assertEqual(len(fetcher.calls), 1)

    def test_missing_on_head_stops_immediately(self):
        fetcher = make_fetcher({('u', 'HEAD'): 404})
        kind, attempts = icon_urls.probe('u', fetcher, retries=3, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.MISSING)
        self.assertEqual(len(fetcher.calls), 1)

    def test_head_403_falls_back_to_ranged_get(self):
        fetcher = make_fetcher({('u', 'HEAD'): 403, ('u', 'GET'): 200})
        kind, attempts = icon_urls.probe('u', fetcher, retries=0, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.PRESENT)
        self.assertEqual([c[1] for c in fetcher.calls], ['HEAD', 'GET'])
        self.assertTrue(fetcher.calls[1][2], 'GET 兜底必须带 Range 头')

    def test_429_then_200_is_present_after_retry(self):
        # 首次 HEAD 429 → GET 429 → 重试 HEAD 200
        fetcher = SequenceFetcher([429, 429, 200])
        kind, _ = icon_urls.probe('u', fetcher, retries=3, backoff=0, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.PRESENT)
        self.assertEqual(len(fetcher.calls), 3)

    def test_retries_are_bounded(self):
        fetcher = make_fetcher({('u', 'HEAD'): 429, ('u', 'GET'): 429})
        kind, attempts = icon_urls.probe('u', fetcher, retries=2, backoff=0, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.UNDETERMINED)
        self.assertEqual(len(attempts), 6, '2 次重试 → 3 轮 × (HEAD + GET)')
        self.assertEqual(len(fetcher.calls), 6)

    def test_backoff_is_applied_between_attempts(self):
        fetcher = make_fetcher({('u', 'HEAD'): 503, ('u', 'GET'): 503})
        slept = []
        icon_urls.probe('u', fetcher, retries=2, backoff=1.0, sleeper=slept.append)
        self.assertEqual(slept, [1.0, 2.0])

    def test_timeout_is_undetermined(self):
        fetcher = make_fetcher({('u', 'HEAD'): TimeoutError('timed out'),
                                ('u', 'GET'): TimeoutError('timed out')})
        kind, attempts = icon_urls.probe('u', fetcher, retries=0, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.UNDETERMINED)
        self.assertTrue(any('TimeoutError' in a for a in attempts))

    def test_connection_failure_is_undetermined(self):
        fetcher = make_fetcher({('u', 'HEAD'): OSError('network unreachable'),
                                ('u', 'GET'): OSError('network unreachable')})
        kind, _ = icon_urls.probe('u', fetcher, retries=0, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.UNDETERMINED)

    def test_timeout_argument_is_forwarded(self):
        seen = []

        def fetcher(url, method='HEAD', timeout=10.0, headers=None):
            seen.append(timeout)
            return 200, {}

        icon_urls.probe('u', fetcher, timeout=3.5, retries=0, sleeper=lambda s: None)
        self.assertEqual(seen, [3.5])


class CheckUrlsTest(unittest.TestCase):
    def test_duplicate_urls_are_probed_once(self):
        fetcher = make_fetcher({('a', 'HEAD'): 200, ('b', 'HEAD'): 200})
        results = icon_urls.check_urls(['a', 'a', 'b', 'b'], fetcher=fetcher, retries=0,
                                       sleeper=lambda s: None)
        self.assertEqual(sorted(results), ['a', 'b'])
        self.assertEqual(len(fetcher.calls), 2)

    def test_summary_counts(self):
        fetcher = make_fetcher({('a', 'HEAD'): 200, ('b', 'HEAD'): 404,
                                ('c', 'HEAD'): 500, ('c', 'GET'): 500})
        results = icon_urls.check_urls(['a', 'b', 'c'], fetcher=fetcher, retries=0, workers=3,
                                       sleeper=lambda s: None)
        self.assertEqual(icon_urls.summarize(results),
                         {icon_urls.PRESENT: 1, icon_urls.MISSING: 1, icon_urls.UNDETERMINED: 1})

    def test_internal_error_is_undetermined_not_silently_dropped(self):
        def boom(url, method='HEAD', timeout=10.0, headers=None):
            raise RuntimeError('内部炸了')
        results = icon_urls.check_urls(['a'], fetcher=boom, retries=0, sleeper=lambda s: None)
        self.assertEqual(results['a'][0], icon_urls.UNDETERMINED)


class MainExitCodeTest(unittest.TestCase):
    """CLI 退出码：只有「全部可确认存在」才是 0。"""

    def _run(self, results, urls=('u1',), policy=()):
        with patch.object(icon_urls, 'production_icon_urls', return_value=list(urls)), \
             patch.object(icon_urls, 'policy_problems', return_value=list(policy)), \
             patch.object(icon_urls, 'check_urls', return_value=results):
            return icon_urls.main([])

    def test_all_present_exits_zero(self):
        self.assertEqual(self._run({'u1': (icon_urls.PRESENT, ['HEAD -> 200'])}), 0)

    def test_missing_exits_nonzero(self):
        self.assertEqual(self._run({'u1': (icon_urls.MISSING, ['HEAD -> 404'])}), 1)

    def test_undetermined_exits_nonzero(self):
        self.assertEqual(self._run({'u1': (icon_urls.UNDETERMINED, ['HEAD -> 429'])}), 1)

    def test_mixed_present_and_undetermined_exits_nonzero(self):
        self.assertEqual(self._run({
            'u1': (icon_urls.PRESENT, []),
            'u2': (icon_urls.UNDETERMINED, ['HEAD -> 503']),
        }, urls=('u1', 'u2')), 1)

    def test_no_urls_exits_nonzero(self):
        self.assertEqual(self._run({}, urls=()), 1)

    def test_policy_violation_exits_nonzero(self):
        self.assertEqual(self._run({}, urls=('u1',), policy=('u1',)), 1)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self._respond()

    def do_GET(self):
        self._respond()

    def _respond(self):
        path = self.path.split('?')[0]
        if path == '/ok.png':
            body = b'png'
            self.send_response(200)
        elif path == '/redirect.png':
            self.send_response(302)
            self.send_header('Location', '/ok.png')
            self.end_headers()
            return
        elif path == '/missing.png':
            body = b''
            self.send_response(404)
        elif path == '/rate.png':
            body = b''
            self.send_response(429)
        elif path == '/boom.png':
            body = b''
            self.send_response(500)
        else:
            body = b''
            self.send_response(404)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command == 'GET':
            self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - 与基类签名保持一致
        pass


class LocalServerFetcherTest(unittest.TestCase):
    """用本地 HTTP 服务器验证真实 fetcher —— 不依赖公网状态。"""

    @classmethod
    def setUpClass(cls):
        cls.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), _Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def test_http_fetch_reports_statuses(self):
        for path, want in (('/ok.png', 200), ('/missing.png', 404),
                           ('/rate.png', 429), ('/boom.png', 500)):
            with self.subTest(path=path):
                status, _ = icon_urls.http_fetch(self.url(path), 'HEAD', timeout=5.0)
                self.assertEqual(status, want)

    def test_redirect_is_followed(self):
        status, _ = icon_urls.http_fetch(self.url('/redirect.png'), 'HEAD', timeout=5.0)
        self.assertEqual(status, 200)

    def test_ranged_get_works(self):
        status, headers = icon_urls.http_fetch(self.url('/ok.png'), 'GET', timeout=5.0,
                                               headers={'Range': 'bytes=0-0'})
        self.assertEqual(status, 200)
        self.assertTrue(headers)

    def test_end_to_end_classification_via_probe(self):
        cases = {'/ok.png': icon_urls.PRESENT,
                 '/redirect.png': icon_urls.PRESENT,
                 '/missing.png': icon_urls.MISSING,
                 '/rate.png': icon_urls.UNDETERMINED,
                 '/boom.png': icon_urls.UNDETERMINED}
        for path, want in cases.items():
            with self.subTest(path=path):
                kind, _ = icon_urls.probe(self.url(path), icon_urls.http_fetch,
                                          timeout=5.0, retries=0, sleeper=lambda s: None)
                self.assertEqual(kind, want)

    def test_connection_refused_is_undetermined(self):
        # 关闭后的端口：连接失败必须归类为「无法判定」，不得当作不存在
        kind, _ = icon_urls.probe('http://127.0.0.1:1/ok.png', icon_urls.http_fetch,
                                  timeout=2.0, retries=0, sleeper=lambda s: None)
        self.assertEqual(kind, icon_urls.UNDETERMINED)


if __name__ == '__main__':
    unittest.main()
