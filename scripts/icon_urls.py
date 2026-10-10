#!/usr/bin/env python3
"""icon_urls.py — 生产图标 URL 的**当前**可达性检查。

职责边界（有意保持小而可测）：

1. :func:`production_icon_urls` —— 从**实际生产配置**中提取被引用的图标 URL；
2. :func:`policy_problems` —— 确认这些 URL 仍落在 manifest 声明的生产基址内；
3. :func:`probe` / :func:`check_urls` —— 用可注入的 ``fetcher`` 做 HTTP 探测与归类。

明确不解决的问题（不得在报告里写成已解决）：

* 只检查**生产配置实际引用**的图标 URL（去重），不扫描整个图标库；
* 检查的是**此刻**的可达性，**不是未来可用性保证**。生产 URL 指向 ``main``，
  该分支在检查之后仍可能移动，文件可能被删除或重命名，生产 URL 仍可能随后
  返回 404 —— 这个竞态无法由本检查消除；
* 「无法判定」（403 / 429 / 5xx / 超时 / 连接失败）**既不算通过，也不算不存在**，
  必须显式报告并导致失败，绝不假绿。

单元测试通过注入 ``fetcher`` 运行，不依赖公网状态。
"""
from pathlib import Path
import argparse
import concurrent.futures
import re
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent

USER_AGENT = 'mihomo-rules-icon-url-check/1.0 (+https://github.com/Hawaiine/mihomo-rules)'

# 归类：只有可信 404 才算「明确不存在」；其余非 2xx 一律「无法判定」
PRESENT = 'present'
MISSING = 'missing'
UNDETERMINED = 'undetermined'

PRESENT_STATUSES = frozenset({200, 206})

# 先 HEAD；HEAD 不能给出终态时用受控 ranged GET 兜底（只取首字节，不下载整文件）
PROBE_METHODS = (('HEAD', {}), ('GET', {'Range': 'bytes=0-0'}))

_ICON_LINE = re.compile(r'\s*icon:\s*"([^"]+)"')


# --------------------------------------------------------------------------- #
# URL 来源：读实际生成产物，不重写一套容易漂移的拼接规则
# --------------------------------------------------------------------------- #

def production_config_paths(root=None):
    """生产配置清单（四份）：``configs/*/config.yaml`` 与 ``configs/*/config.min.yaml``。

    返回结果整体排序，保证 URL 提取顺序稳定可复现。
    """
    base = (ROOT if root is None else Path(root)) / 'configs'
    return sorted(list(base.glob('*/config.yaml')) + list(base.glob('*/config.min.yaml')))


def production_icon_urls(root=None, config_paths=None):
    """从实际生产配置中提取被引用的图标 URL，去重排序。

    直接读生成产物，而不是再写一套 URL 拼接规则——否则检查的就不再是
    真正发布的那批 URL。
    """
    paths = production_config_paths(root=root) if config_paths is None else [Path(p) for p in config_paths]
    urls = set()
    for path in paths:
        for line in path.read_text(encoding='utf-8').splitlines():
            match = _ICON_LINE.match(line)
            if match:
                urls.add(match.group(1))
    return sorted(urls)


def policy_problems(urls, manifest=None):
    """URL 必须落在 manifest 声明的生产基址内（host / 仓库 / ref 均不可漂移）。"""
    import match_icons

    manifest = match_icons.load_manifest() if manifest is None else manifest
    base = match_icons.asset_url_base(manifest)
    return sorted(url for url in urls if not url.startswith(base + '/'))


# --------------------------------------------------------------------------- #
# HTTP 探测
# --------------------------------------------------------------------------- #

def http_fetch(url, method='HEAD', timeout=10.0, headers=None):
    """默认探测器：返回 ``(status, headers)``。

    唯一产生真实网络请求的地方。重定向由 urllib 自动跟随；
    超时 / 连接失败以异常抛出，由 :func:`probe` 归类为「无法判定」。
    """
    request = urllib.request.Request(url, method=method)
    request.add_header('User-Agent', USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {})


def classify(status, error=None):
    """把一次 HTTP 结果归类为 ``present`` / ``missing`` / ``undetermined``。

    只有可信 404 才是「明确不存在」。403 / 429 / 5xx / 超时 / 连接失败都
    **不得**被当作存在，也不得被当作不存在——它们是「无法判定」。
    """
    if error is not None:
        return UNDETERMINED
    if status in PRESENT_STATUSES:
        return PRESENT
    if status == 404:
        return MISSING
    return UNDETERMINED


def probe(url, fetcher, timeout=10.0, retries=3, backoff=1.0, sleeper=time.sleep):
    """探测单个 URL，返回 ``(kind, attempts)``。

    可判定终态（present / 可信 404）立即返回；其余按 ``retries`` 上限指数退避
    重试。重试耗尽仍无法判定 -> ``undetermined``（调用方必须视为失败）。
    """
    attempts = []
    for attempt in range(retries + 1):
        for method, headers in PROBE_METHODS:
            try:
                status, _ = fetcher(url, method, timeout, headers)
                error = None
            except Exception as exc:  # noqa: BLE001 —— 归类后如实上报，不吞不猜
                status, error = None, f'{type(exc).__name__}: {exc}'
            shown = status if status is not None else error
            attempts.append(f'{method}{" +Range" if headers else ""} -> {shown}')
            kind = classify(status, error)
            if kind in (PRESENT, MISSING):
                return kind, attempts
        if attempt < retries:
            sleeper(backoff * (2 ** attempt))
    return UNDETERMINED, attempts


def check_urls(urls, fetcher=None, timeout=10.0, retries=3, backoff=1.0,
               workers=8, sleeper=None):
    """并发探测（去重后）URL 列表，返回 ``{url: (kind, attempts)}``。"""
    fetcher = http_fetch if fetcher is None else fetcher
    sleeper = time.sleep if sleeper is None else sleeper
    unique = sorted(set(urls))
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(probe, url, fetcher, timeout, retries, backoff, sleeper): url
            for url in unique
        }
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as exc:  # noqa: BLE001 —— 并发层异常同样不得静默
                results[url] = (UNDETERMINED, [f'内部错误: {type(exc).__name__}: {exc}'])
    return results


def summarize(results):
    """``{kind: 数量}``。"""
    counts = {PRESENT: 0, MISSING: 0, UNDETERMINED: 0}
    for kind, _ in results.values():
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='检查生产图标 URL 当前是否可达（不改变生产 URL 策略）')
    parser.add_argument('--root', default=None, help='仓库根（默认按脚本位置推导）')
    parser.add_argument('--timeout', type=float, default=10.0, help='单次请求超时（秒）')
    parser.add_argument('--retries', type=int, default=3, help='无法判定时的重试次数上限')
    parser.add_argument('--backoff', type=float, default=1.0, help='退避基数（秒），指数增长')
    parser.add_argument('--workers', type=int, default=8, help='并发上限')
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else ROOT
    urls = production_icon_urls(root=root)
    print('=' * 60)
    print(f'  生产配置引用的图标 URL（去重）: {len(urls)}')
    if not urls:
        print('  FAIL: 没有解析到任何生产图标 URL（配置缺失或格式变化）')
        return 1

    problems = policy_problems(urls)
    if problems:
        print('  FAIL: 以下 URL 偏离 manifest 声明的生产基址（host/仓库/ref 漂移）:')
        for url in problems[:10]:
            print(f'    {url}')
        return 1
    import match_icons
    print(f'  生产基址: {match_icons.asset_url_base(match_icons.load_manifest())}')
    print(f'  探测方式: HEAD → ranged GET 兜底；timeout={args.timeout}s '
          f'retries={args.retries} workers={args.workers}')

    results = check_urls(urls, timeout=args.timeout, retries=args.retries,
                         backoff=args.backoff, workers=args.workers)
    counts = summarize(results)

    for kind, label in ((MISSING, '明确不存在（可信 404）'), (UNDETERMINED, '无法判定')):
        bad = sorted(url for url, (k, _) in results.items() if k == kind)
        if bad:
            print(f'\n  [{kind}] {label}: {len(bad)} 个')
            for url in bad:
                print(f'    {url}')
                for attempt in results[url][1][-2:]:
                    print(f'        {attempt}')

    print('\n--- 结果 ---')
    print(f'  可确认存在: {counts[PRESENT]}')
    print(f'  明确不存在: {counts[MISSING]}')
    print(f'  无法判定  : {counts[UNDETERMINED]}')
    print('\n  说明：这是**检查时刻**的可达性，不是未来可用性保证。生产 URL 指向 main，')
    print('        该分支在检查之后仍可能移动或删除文件，生产 URL 仍可能随后返回 404。')

    if counts[MISSING] or counts[UNDETERMINED]:
        print('\n  FAIL: 存在「明确不存在」或「无法判定」的 URL —— 无法确认即失败，不得假绿。')
        return 1
    print('\n  PASS: 全部生产图标 URL 当前可确认存在。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
