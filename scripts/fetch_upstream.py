"""
fetch_upstream.py — 只管抓取 + 完整性校验

三个上游各自独立拉取，任一失败只跳过该上游，不中断整体流程。
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
        "local_dir": "v2fly",
        "data_dir": "data",  # 仓库内数据目录
    },
    "loyalsoldier": {
        "url": "https://github.com/Loyalsoldier/clash-rules.git",
        "branch": "release",
        "local_dir": "loyalsoldier",
        "data_dir": "",  # 文件在仓库根目录
    },
    "blackmatrix7": {
        "url": "https://github.com/blackmatrix7/ios_rule_script.git",
        "local_dir": "blackmatrix7",
        "data_dir": "rule/Clash",  # 仓库内数据目录
    },
}

# 默认存储路径（相对于项目根目录）
DEFAULT_CACHE_DIR = "upstream"


# ── 抓取结果类型 ──────────────────────────────────────────────

class FetchResult(NamedTuple):
    """单个上游的抓取结果"""
    upstream: str
    success: bool
    file_count: int  # 成功获取的文件数
    total_bytes: int
    error: str = ""
    elapsed: float = 0.0


# ── 工具函数 ──────────────────────────────────────────────────

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

    if os.path.isdir(local_dir):
        # 已存在，拉取最新
        branch = config.get("branch", "main")
        # 先确保本地分支跟踪远程分支
        _run_cmd(["git", "branch", f"--set-upstream-to=origin/{branch}", branch], timeout=10, cwd=local_dir)
        code, out = _run_cmd(["git", "pull", "--ff-only"], timeout=60, cwd=local_dir)
        if code != 0:
            print(f"  ⚠️ {name}: git pull 失败 ({code})，尝试 fetch + reset")
            _run_cmd(["git", "fetch", "origin"], timeout=60, cwd=local_dir)
            _run_cmd(["git", "reset", "--hard", f"origin/{branch}"], timeout=60, cwd=local_dir)
    else:
        # 克隆
        branch = config.get("branch", "main")
        os.makedirs(os.path.dirname(local_dir), exist_ok=True)
        code, out = _run_cmd(
            ["git", "clone", "--depth", "1", "--branch", branch, url, local_dir],
            timeout=120,
        )
        if code != 0:
            raise RuntimeError(f"克隆 {name} 失败: {out[:500]}")

    return local_dir, data_dir


def fetch_upstream(
    name: str,
    config: dict,
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> FetchResult:
    """
    抓取单个上游，返回结果。

    Args:
        name:      上游名称
        config:    上游配置
        cache_dir: 缓存根目录

    Returns:
        FetchResult
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

        file_count = 0
        total_bytes = 0
        for root, dirs, files in os.walk(data_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_bytes += os.path.getsize(fp)
                    file_count += 1
                except OSError:
                    pass

        return FetchResult(
            upstream=name,
            success=True,
            file_count=file_count,
            total_bytes=total_bytes,
            elapsed=time.time() - start,
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


def fetch_all(cache_dir: str = DEFAULT_CACHE_DIR) -> dict[str, FetchResult]:
    """
    抓取所有三个上游，各自的失败不中断整体流程。

    Args:
        cache_dir: 缓存根目录

    Returns:
        dict: 上游名称 → FetchResult
    """
    results: dict[str, FetchResult] = {}

    for name, config in UPSTREAM_REPOS.items():
        print(f"📥 正在拉取 {name}...")
        result = fetch_upstream(name, config, cache_dir)
        results[name] = result
        if result.success:
            print(f"  ✅ {name}: {result.file_count} 文件, {result.total_bytes} 字节 ({result.elapsed:.1f}s)")
        else:
            print(f"  ❌ {name}: {result.error}")

    return results


# ── 历史行数对比（反截断） ────────────────────────────────────

def load_historical_line_counts(stats_file: str) -> dict[str, int]:
    """
    加载上次同步成功的历史行数。

    Args:
        stats_file: stats.json 路径

    Returns:
        dict: 文件名 → 历史行数
    """
    import json
    try:
        with open(stats_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_historical_line_counts(
    stats_file: str,
    counts: dict[str, int],
):
    """
    保存本次同步成功的历史行数。

    Args:
        stats_file: stats.json 路径
        counts:     文件名 → 行数
    """
    import json
    os.makedirs(os.path.dirname(stats_file), exist_ok=True)
    with open(stats_file, "w") as f:
        json.dump(counts, f, indent=2)


def check_truncation(
    filepath: str,
    current_lines: int,
    historical_counts: dict[str, int],
    threshold: float = 0.5,
) -> tuple[bool, str]:
    """
    检查文件是否被截断。

    对比当前行数与历史行数：
    - 新行数 < 历史行数 × threshold → 判定异常
    - 新行数 == 0 → 判定异常

    Args:
        filepath:          文件路径
        current_lines:     当前行数
        historical_counts: 历史行数字典
        threshold:         阈值比例（默认 0.5）

    Returns:
        (is_ok, message) 是否正常
    """
    if current_lines == 0:
        return False, f"文件为空: {filepath}"

    prev_lines = historical_counts.get(filepath)
    if prev_lines is not None:
        if current_lines < prev_lines * threshold:
            return False, (
                f"文件行数骤降: 当前 {current_lines} < 历史 {prev_lines} × {threshold}"
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
        print("✅ 全部上游拉取成功")
    else:
        print("⚠️ 部分上游拉取失败")
        for name, r in results.items():
            if not r.success:
                print(f"  ❌ {name}: {r.error}")
    sys.exit(0 if all_ok else 1)