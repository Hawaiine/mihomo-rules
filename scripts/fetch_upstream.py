"""
fetch_upstream.py — 只管抓取 + 完整性校验

三个上游各自独立拉取，任一失败只跳过该上游，不中断整体流程。

完整性守卫（2026-09-24 新增）：
- 抓取成功后按上游逐一校验**必备数据文件存在性 + 行数下限**；
- 与上次同步的**历史行数**对比，识别「行数骤降 / 文件清空」式静默截断；
- 记录上游 commit SHA 到 stats.json，便于追溯；
- 任一校验不通过 → 该上游 success=False（整体退出码 1，CI 会拦截并通知）。

历史教训：loyalsoldier 的 branch 曾被改成 master（数据文件全在 release），
由于当时「成功」判据只有 git 操作成功 + 文件数 > 0（master 也有 4 个文件），
故障静默持续约 2 个月无人发现。故本文件的存在性/行数守卫是**必需**的，
不要为了「跑通」而放宽阈值。
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple


# ── 上游仓库配置 ──────────────────────────────────────────────

UPSTREAM_REPOS = {
    "v2fly": {
        "url": "https://github.com/v2fly/domain-list-community.git",
        "branch": "master",
        "local_dir": "v2fly",
        "data_dir": "data",  # 仓库内数据目录
    },
    "loyalsoldier": {
        "url": "https://github.com/Loyalsoldier/clash-rules.git",
        # ⚠️ 数据文件全部在 release 分支；master 只有 LICENSE/README/.github
        # 改成 master 会导致「抓取成功但 0 数据」的静默故障（2026-07-23 事故）
        "branch": "release",
        "local_dir": "loyalsoldier",
        "data_dir": "",  # 文件在仓库根目录
    },
    "blackmatrix7": {
        "url": "https://github.com/blackmatrix7/ios_rule_script.git",
        "branch": "master",
        "local_dir": "blackmatrix7",
        "data_dir": "rule/Clash",  # 仓库内数据目录
    },
}

# 默认存储路径（相对于项目根目录）
DEFAULT_CACHE_DIR = "upstream"

# 历史行数 / SHA 留痕文件（位于 cache_dir 下，upstream/ 已在 .gitignore 中）
STATS_FILENAME = "stats.json"


# ── 完整性校验规则 ────────────────────────────────────────────
#
# loyalsoldier: 7 个基础数据文件的**最小行数**（实测 2026-09-22 release@be3f25f：
#   reject 190,748 / direct 111,169 / proxy 27,100 / cncidr 9,741 /
#   private 130 / lancidr 18 / applications 98）
#   阈值取实测值的 ~50%，既能拦住「分支错 / 文件缺失 / 被截断」，
#   又不会因上游正常增删而误报。
#
# v2fly / blackmatrix7: 数据目录文件数下限（实测 data/ 约 900+、
#   rule/Clash 约 300+，取保守下限）。

INTEGRITY_RULES: dict[str, dict] = {
    "loyalsoldier": {
        "files": {
            "reject.txt": 100_000,
            "direct.txt": 50_000,
            "proxy.txt": 10_000,
            "cncidr.txt": 1_000,
            "private.txt": 1,
            "lancidr.txt": 1,
            "applications.txt": 1,
        },
    },
    "v2fly": {"min_files": 500},
    "blackmatrix7": {"min_files": 100},
}

# 历史行数骤降判据（当前 < 历史 × 该比例 即判定异常）
TRUNCATION_RATIO = 0.5

# git fetch 超时：blackmatrix7 等大仓库浅拉在慢网络下可能耗时数分钟，
# 超时过短会被 kill 并留下 shallow.lock，导致后续重试全部失败
FETCH_TIMEOUT = 900
CLONE_TIMEOUT = 900


# ── 抓取结果类型 ──────────────────────────────────────────────

class FetchResult(NamedTuple):
    """单个上游的抓取结果"""
    upstream: str
    success: bool
    file_count: int  # 成功获取的文件数
    total_bytes: int
    error: str = ""
    elapsed: float = 0.0
    checks: tuple[str, ...] = ()  # 完整性校验问题列表（成功时为空）
    commit: str = ""  # 上游 commit SHA（留痕用）


# ── 工具函数 ──────────────────────────────────────────────────


def _retry_cmd(cmd: list[str], timeout: int = 60, cwd: str | None = None,
               max_retries: int = 3, base_delay: float = 2.0) -> tuple[int, str]:
    """运行命令，失败时指数退避重试，失败自动清理 git 操作残留。

    Args:
        cmd:         命令列表
        timeout:     单次超时秒数
        cwd:         工作目录
        max_retries: 最大重试次数（默认 3）
        base_delay:  基础延迟秒数（默认 2s，每次翻倍: 2, 4, 8）

    Returns:
        (exit_code, stdout)
    """
    last_code = -1
    last_out = ""
    for attempt in range(1, max_retries + 1):
        code, out = _run_cmd(cmd, timeout=timeout, cwd=cwd)
        if code == 0:
            return code, out
        last_code = code
        last_out = out
        if attempt < max_retries:
            delay = base_delay * (2 ** (attempt - 1))
            print(f"  ⚠️ 重试 {attempt}/{max_retries} ({delay:.0f}s) — {cmd[0]} {' '.join(cmd[1:3])}...")
            time.sleep(delay)
    return last_code, last_out


def _run_cmd(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> tuple[int, str]:
    """运行命令并返回 (exit_code, stdout)"""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, f"命令超时 ({timeout}s)"
    except FileNotFoundError as e:
        return -2, f"命令未找到: {e}"
    except Exception as e:
        return -3, str(e)


def _count_lines(path: str) -> int:
    """统计文件非空行数（截断检测口径，稳定且与内容编码无关）。"""
    n = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        return -1
    return n


def _count_files(data_dir: str) -> tuple[int, int]:
    """递归统计目录下文件数与总字节数。"""
    file_count = 0
    total_bytes = 0
    for root, _dirs, files in os.walk(data_dir):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_bytes += os.path.getsize(fp)
                file_count += 1
            except OSError:
                pass
    return file_count, total_bytes


def upstream_commit(local_dir: str) -> str:
    """取上游本地仓库当前 commit SHA（追溯用，失败返回空串）。"""
    code, out = _run_cmd(["git", "rev-parse", "HEAD"], timeout=15, cwd=local_dir)
    return out.strip() if code == 0 else ""


# git 操作锁文件：进程被 kill / 磁盘满时可能残留，导致后续所有 fetch 失败
GIT_LOCK_FILES = ("shallow.lock", "index.lock", "config.lock", "HEAD.lock")


def _clear_stale_locks(local_dir: str, min_age: float = 60.0) -> list[str]:
    """清理陈旧 git 锁文件（仅当超过 min_age 秒未被触碰）。

    上游仓库是长期复用的浅克隆，一旦有残留锁，之后每次 fetch 都会失败，
    且报错指向「另一个 git 进程在运行」——实际早已无进程持有。
    """
    removed: list[str] = []
    for lock in GIT_LOCK_FILES:
        p = os.path.join(local_dir, ".git", lock)
        try:
            if os.path.isfile(p) and (time.time() - os.path.getmtime(p)) > min_age:
                os.remove(p)
                removed.append(lock)
        except OSError:
            pass
    return removed


def _fetch_branch(name: str, local_dir: str, branch: str) -> tuple[int, str]:
    """浅拉指定分支并建立 origin/<branch> 引用（带重试 + 每次尝试前清理陈旧锁）。

    单分支浅克隆的 remote.origin.fetch 只覆盖原分支，直接
    `git fetch origin <branch>` 不会创建 origin/<branch> 引用，故显式指定 refspec。
    """
    spec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    last_code, last_out = -1, ""
    for attempt in range(1, 4):
        # 上一轮被超时 kill 时会留下 shallow.lock / index.lock，必须先清
        _clear_stale_locks(local_dir, min_age=5.0)
        last_code, last_out = _run_cmd(
            ["git", "fetch", "--depth", "1", "origin", spec],
            timeout=FETCH_TIMEOUT, cwd=local_dir,
        )
        if last_code == 0:
            return last_code, last_out
        if attempt < 3:
            delay = 2 * (2 ** (attempt - 1))
            print(f"  ⚠️ {name}: fetch 重试 {attempt}/3 ({delay}s)")
            time.sleep(delay)
    return last_code, last_out


# ── 单个上游抓取 ──────────────────────────────────────────────

def _ensure_upstream_repo(
    name: str,
    config: dict,
    cache_dir: str,
) -> tuple[str, str]:
    """
    确保上游仓库已克隆到本地缓存目录，并拉取最新代码。

    Args:
        name:     上游名称 (v2fly / loyalsoldier / blackmatrix7)
        config:   上游配置
        cache_dir: 缓存根目录

    Returns:
        (local_dir, data_dir) 本地路径和数据目录路径
    """
    local_dir = os.path.join(cache_dir, config["local_dir"])
    data_dir = os.path.join(local_dir, config["data_dir"]) if config.get("data_dir") else local_dir
    url = config["url"]
    branch = config.get("branch", "main")

    if os.path.isdir(local_dir):
        # 已存在：**强制对齐到目标分支**。
        # 只用 pull 是不够的——若本地仓库当初被 clone 成了错分支
        # （loyalsoldier 曾被 clone 成 master），pull 永远拿不到 release 的数据。
        locks = _clear_stale_locks(local_dir)
        if locks:
            print(f"  🧹 {name}: 清理陈旧 git 锁 {', '.join(locks)}")
        code, out = _fetch_branch(name, local_dir, branch)
        if code != 0:
            raise RuntimeError(f"{name}: git fetch origin {branch} 失败（重试后仍失败）: {out[:500]}")
        code2, out2 = _retry_cmd(["git", "checkout", "-B", branch, f"origin/{branch}"],
                                 timeout=120, cwd=local_dir)
        if code2 != 0:
            raise RuntimeError(f"{name}: 切换到 origin/{branch} 失败: {out2[:500]}")
    else:
        # 克隆（带重试 + 失败清理半成品目录）
        os.makedirs(os.path.dirname(local_dir), exist_ok=True)
        code, out = _retry_cmd(
            ["git", "clone", "--depth", "1", "--branch", branch, url, local_dir],
            timeout=CLONE_TIMEOUT,
        )
        if code != 0:
            # 清理半成品目录，避免下次误判为已存在
            if os.path.isdir(local_dir):
                shutil.rmtree(local_dir, ignore_errors=True)
            raise RuntimeError(f"克隆 {name} 失败（重试后仍失败）: {out[:500]}")

    return local_dir, data_dir


def _collect_counts(name: str, data_dir: str) -> dict[str, int]:
    """收集该上游用于截断对比的行数口径。

    - loyalsoldier：逐个必备文件的行数（key = 文件名）
    - v2fly / blackmatrix7：目录文件数（key = "__file_count__"）
    """
    rules = INTEGRITY_RULES.get(name, {})
    counts: dict[str, int] = {}
    if "files" in rules:
        for fn in rules["files"]:
            counts[fn] = _count_lines(os.path.join(data_dir, fn))
    else:
        file_count, _ = _count_files(data_dir)
        counts["__file_count__"] = file_count
    return counts


def verify_integrity(
    name: str,
    data_dir: str,
    historical: dict[str, int] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """按上游校验必备文件存在性 / 行数下限 / 是否相对历史骤降。

    Args:
        name:       上游名称
        data_dir:   数据目录
        historical: 历史行数（key = "{upstream}/{file}"），可为 None

    Returns:
        (problems, counts) —— problems 为空表示校验通过
    """
    problems: list[str] = []
    rules = INTEGRITY_RULES.get(name)
    if not rules:
        return problems, {}

    historical = historical or {}
    counts = _collect_counts(name, data_dir)

    if "files" in rules:
        for fn, min_lines in rules["files"].items():
            fp = os.path.join(data_dir, fn)
            if not os.path.isfile(fp):
                problems.append(f"必备文件缺失: {fn}")
                continue
            cur = counts.get(fn, -1)
            if cur < 0:
                problems.append(f"必备文件不可读: {fn}")
                continue
            if cur < min_lines:
                problems.append(f"{fn} 行数低于下限: {cur} < {min_lines}")
                continue
            ok, msg = check_truncation(fn, cur, historical,
                                       threshold=TRUNCATION_RATIO, key=f"{name}/{fn}")
            if not ok:
                problems.append(msg)
    else:
        min_files = rules.get("min_files", 0)
        cur = counts.get("__file_count__", 0)
        if cur < min_files:
            problems.append(f"数据目录文件数低于下限: {cur} < {min_files}")
        ok, msg = check_truncation("__file_count__", cur, historical,
                                   threshold=TRUNCATION_RATIO,
                                   key=f"{name}/__file_count__")
        if not ok:
            problems.append(msg)

    return problems, counts


def fetch_upstream(
    name: str,
    config: dict,
    cache_dir: str = DEFAULT_CACHE_DIR,
    historical: dict[str, int] | None = None,
) -> FetchResult:
    """
    抓取单个上游，返回结果（含完整性校验结论）。

    Args:
        name:      上游名称
        config:    上游配置
        cache_dir: 缓存根目录
        historical: 历史行数（key = "{upstream}/{file}"）

    Returns:
        FetchResult —— 完整性校验不通过时 success=False
    """
    start = time.time()
    try:
        local_dir, data_dir = _ensure_upstream_repo(name, config, cache_dir)

        # 扫描数据目录，获取文件列表和大小
        if not os.path.isdir(data_dir):
            return FetchResult(
                upstream=name,
                success=False,
                file_count=0,
                total_bytes=0,
                error=f"数据目录不存在: {data_dir}",
                elapsed=time.time() - start,
            )

        file_count, total_bytes = _count_files(data_dir)

        # 完整性守卫：必备文件 + 行数下限 + 相对历史骤降
        problems, _counts = verify_integrity(name, data_dir, historical)
        if problems:
            return FetchResult(
                upstream=name,
                success=False,
                file_count=file_count,
                total_bytes=total_bytes,
                error="完整性校验失败: " + "; ".join(problems),
                elapsed=time.time() - start,
                checks=tuple(problems),
                commit=upstream_commit(local_dir),
            )

        return FetchResult(
            upstream=name,
            success=True,
            file_count=file_count,
            total_bytes=total_bytes,
            elapsed=time.time() - start,
            commit=upstream_commit(local_dir),
        )

    except Exception as e:
        return FetchResult(
            upstream=name,
            success=False,
            file_count=0,
            total_bytes=0,
            error=str(e),
            elapsed=time.time() - start,
        )


def fetch_all(
    cache_dir: str = DEFAULT_CACHE_DIR,
    stats_file: str | None = None,
    write_stats: bool = True,
) -> dict[str, FetchResult]:
    """
    抓取所有三个上游，各自的失败不中断整体流程。

    校验通过的上游会把行数快照 + commit SHA 写入 stats.json；
    校验失败的上游**不更新**历史快照（避免把坏数据当成新基线）。

    Args:
        cache_dir:  缓存根目录
        stats_file: stats.json 路径（默认 <cache_dir>/stats.json）
        write_stats: 是否写回 stats.json

    Returns:
        dict: 上游名称 → FetchResult
    """
    stats_file = stats_file or os.path.join(cache_dir, STATS_FILENAME)
    store = load_stats(stats_file)
    historical_flat = _flatten_history(store)

    results: dict[str, FetchResult] = {}
    new_counts: dict[str, dict[str, int]] = dict(store.get("upstreams", {}))

    for name, config in UPSTREAM_REPOS.items():
        print(f"📥 正在拉取 {name}...")
        result = fetch_upstream(name, config, cache_dir, historical=historical_flat)
        results[name] = result
        if result.success:
            print(f"  ✅ {name}: {result.file_count} 文件, {result.total_bytes} 字节 ({result.elapsed:.1f}s)")
            if result.commit:
                print(f"     commit: {result.commit[:12]}")
            data_dir = os.path.join(cache_dir, config["local_dir"])
            if config.get("data_dir"):
                data_dir = os.path.join(data_dir, config["data_dir"])
            new_counts[name] = {
                "commit": result.commit,
                "file_count": result.file_count,
                "line_counts": _collect_counts(name, data_dir),
            }
        else:
            print(f"  ❌ {name}: {result.error}")
            for p in result.checks:
                print(f"     ⚠️ {p}")

    if write_stats:
        save_stats(stats_file, new_counts)

    return results


# ── 历史行数 / SHA 留痕（stats.json） ─────────────────────────

def load_stats(stats_file: str) -> dict:
    """加载 stats.json（不存在或损坏时返回空结构）。"""
    import json
    try:
        with open(stats_file) as f:
            data = json.load(f)
        if isinstance(data, dict) and "upstreams" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"upstreams": {}}


def save_stats(stats_file: str, upstreams: dict[str, dict]):
    """写回 stats.json（行数快照 + commit SHA + 时间戳）。"""
    import json
    from datetime import datetime, timezone
    os.makedirs(os.path.dirname(os.path.abspath(stats_file)), exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "upstreams": upstreams,
    }
    with open(stats_file, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)


def _flatten_history(store: dict) -> dict[str, int]:
    """把 stats.json 的嵌套结构摊平成 {“{upstream}/{file}”: 行数}。

    兼容早期只存「文件名 → 行数」的平铺格式。
    """
    flat: dict[str, int] = {}
    for name, info in (store.get("upstreams") or {}).items():
        if not isinstance(info, dict):
            continue
        for fn, n in (info.get("line_counts") or {}).items():
            flat[f"{name}/{fn}"] = n
    return flat


def load_historical_line_counts(stats_file: str) -> dict[str, int]:
    """
    加载上次同步成功的历史行数（摊平后的 {"upstream/file": 行数}）。

    Args:
        stats_file: stats.json 路径

    Returns:
        dict: "{upstream}/{file}" → 历史行数
    """
    return _flatten_history(load_stats(stats_file))


def save_historical_line_counts(
    stats_file: str,
    counts: dict[str, int],
):
    """
    保存历史行数（保持扁平键 {"upstream/file": 行数}）。

    Args:
        stats_file: stats.json 路径
        counts:     {"upstream/file" → 行数}
    """
    grouped: dict[str, dict[str, int]] = {}
    for key, n in counts.items():
        name, _, fn = key.partition("/")
        grouped.setdefault(name, {})[fn] = n
    upstreams = {name: {"commit": "", "file_count": 0, "line_counts": lc}
                 for name, lc in grouped.items()}
    save_stats(stats_file, upstreams)


def check_truncation(
    filepath: str,
    current_lines: int,
    historical_counts: dict[str, int],
    threshold: float = 0.5,
    key: str | None = None,
) -> tuple[bool, str]:
    """
    检查文件是否被截断。

    对比当前行数与历史行数：
    - 新行数 < 历史行数 × threshold → 判定异常
    - 新行数 == 0 → 判定异常

    Args:
        filepath:          文件路径（也用作历史键）
        current_lines:     当前行数
        historical_counts: 历史行数字典
        threshold:         阈值比例（默认 0.5）
        key:               历史键（默认用 filepath；调用方可传 "upstream/file"）

    Returns:
        (is_ok, message) 是否正常
    """
    if current_lines == 0:
        return False, f"文件为空: {filepath}"

    prev_lines = historical_counts.get(key if key is not None else filepath)
    if prev_lines is not None:
        if current_lines < prev_lines * threshold:
            return False, (
                f"文件行数骤降: {filepath} 当前 {current_lines} < 历史 {prev_lines} × {threshold}"
            )

    return True, ""


# ── 命令行入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cache_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CACHE_DIR
    results = fetch_all(cache_dir)
    all_ok = all(r.success for r in results.values())
    print(f"\n{'='*40}")
    if all_ok:
        print("✅ 全部上游拉取成功（完整性校验通过）")
        for name, r in results.items():
            if r.commit:
                print(f"  {name}: {r.commit[:12]}")
    else:
        print("⚠️ 部分上游拉取失败")
        for name, r in results.items():
            if not r.success:
                print(f"  ❌ {name}: {r.error}")
    sys.exit(0 if all_ok else 1)