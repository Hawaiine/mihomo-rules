#!/usr/bin/env python3
"""icon_repo.py — Oasisic-Icons 检出路径解析的唯一实现。

`scripts/match_icons.py`（生产图标映射）与 `scripts/ci/verify_general_pr.py`
（PR 门禁）必须共用本模块：两处各自演化会悄悄分叉出不同的候选路径优先级，
让「本机能跑」与「门禁通过」不再等价。

职责边界（有意保持小而可测）：

1. :func:`icon_repo_candidates` —— 只做**路径选择**，不判断可用性；
2. :func:`icon_repo_problems` —— 只判断检出是否**可用**（目录存在、有 git
   元数据、固定 revision 可读）；
3. :func:`icon_repo_required_message` —— 只把失败原因组织成可操作的信息。

本模块不做 revision 决策、不读 manifest、不导入 match_icons / contract /
generate_config，因此没有循环依赖，也能被门禁在 PR head 树上单独加载。
"""
from pathlib import Path
import subprocess

ICON_REPO_ENV_VAR = 'MIHOMO_ICON_REPO'
ICON_REPO_DIRNAME = 'Oasisic-Icons'
DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def icon_repo_relative_path(root=None):
    """约定的仓库相对检出位置 ``<root>/Oasisic-Icons``（与 CI checkout 路径一致）。"""
    return (DEFAULT_ROOT if root is None else Path(root)) / ICON_REPO_DIRNAME


def icon_repo_candidates(environ=None, root=None):
    """按优先级返回 ``[(候选路径, 来源说明)]``。

    优先级：显式 ``MIHOMO_ICON_REPO`` → 仓库相对 ``<root>/Oasisic-Icons``。

    显式指定时**不再追加任何机器专属回退**——调用方给出的值必须被尊重，
    即使它无效，也要在 :func:`icon_repo_problems` 中显式报错，
    而不是被静默替换成另一台机器上的目录。
    """
    import os

    env = os.environ if environ is None else environ
    supplied = (env.get(ICON_REPO_ENV_VAR) or '').strip()
    if supplied:
        return [(Path(supplied), f'{ICON_REPO_ENV_VAR}={supplied}')]
    relative = icon_repo_relative_path(root)
    return [(relative, f'仓库相对路径 {relative}')]


def resolve_icon_repo(environ=None, root=None):
    """解析 Oasisic-Icons 检出位置（只做选择，不做可用性判断）。"""
    return icon_repo_candidates(environ=environ, root=root)[0][0]


def icon_repo_problems(repo, revision, runner=None):
    """检查检出是否可用于读取固定 revision，返回问题列表（空 = 可用）。

    只判断“路径存在”是不够的：必须同时确认它是可用的 git 工作树，
    且能读取调用方给出的固定 revision。
    """
    run = subprocess.run if runner is None else runner
    repo = Path(repo)
    if not repo.is_dir():
        return [f'路径不存在或不是目录: {repo}']
    if not (repo / '.git').exists():
        return [f'缺少 git 元数据 {repo / ".git"}（需要完整 clone；仅复制文件无法读取 pinned tree）']
    try:
        probe = run(
            ['git', '-C', str(repo), 'cat-file', '-e', f'{revision}^{{commit}}'],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f'无法执行 git 校验固定 revision {revision}: {exc}']
    if probe.returncode != 0:
        return [f'固定 revision 在检出中不可读: {revision}']
    return []


def icon_repo_required_message(revision, environ=None, root=None):
    """构造 fail-closed 错误信息：尝试过的路径 + 预期条件 + 解决方法。"""
    lines = ['无法定位可用的 Oasisic-Icons 检出（生产图标映射 fail-closed，不回退到任何机器专属目录）']
    for repo, origin in icon_repo_candidates(environ=environ, root=root):
        problems = icon_repo_problems(repo, revision)
        lines.append(f'  - 尝试 {origin} → {repo}：{"可用" if not problems else "; ".join(problems)}')
    lines.append(f'预期条件：完整 git clone，且能读取固定 revision {revision}'
                 '（scripts/config_contract/oasisic_revision.json）')
    lines.append('解决方法：将 Hawaiine/Oasisic-Icons clone 到 <仓库根>/Oasisic-Icons，'
                 f'或设置 {ICON_REPO_ENV_VAR} 指向其绝对路径')
    return '\n'.join(lines)
