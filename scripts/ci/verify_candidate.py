#!/usr/bin/env python3
"""verify_candidate.py — Route A 受信验证：用**可信代码**校验候选树，再发布状态。

信任边界：
* 本脚本来自 `main`（受保护基线），候选树只作为**数据**下载与解析，绝不执行候选
  树里的任何脚本。
* 先做「防自我削弱」路径检查：候选 PR 不得修改 workflows / scripts / 守卫 /
  required-checks / 测试 / 治理文档；出现即失败（不允许机器人 PR 改门禁）。
* 再做数据自洽：每个 ruleset 目录/YAML/basename/# Rule Name 一致；四份配置可解析，
  且 provider 与策略组覆盖全部品牌规则集。

完整性（本版修正）：
* 变更文件清单来自 **PR files 分页 API**（`--pr`），逐页读全并核对
  `pull_request.changed_files` 计数；分页短读、重复路径、计数不符、PR head 漂移
  一律拒绝（不再使用会截断的 compare `files`）。
* 允许范围与 `classify_change.py` **共用同一份政策源**（`is_data_path` /
  `is_governance_path`），不再保留第二套 allowlist。

退出码：0 通过；1 失败（原因写入 --json）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_change as cc  # noqa: E402  （单一政策源：数据 vs 治理路径）

FORBIDDEN_EXACT = {".github/required-checks.json"}
MAX_PAGES = 50
PER_PAGE = 100


class VerifyRefused(RuntimeError):
    pass


def _call(repo: str, token: str, path: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "route-a-verify"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]


def _iter_pr_files(repo: str, token: str, pr: int):
    for page in range(1, MAX_PAGES + 1):
        status, payload = _call(
            repo, token,
            f"/repos/{repo}/pulls/{pr}/files?per_page={PER_PAGE}&page={page}")
        if status != 200 or not isinstance(payload, list):
            raise VerifyRefused(f"PR files 读取失败（page {page}）: HTTP {status}")
        yield payload
        if len(payload) < PER_PAGE:
            return
    raise VerifyRefused(f"PR files 分页超过 {MAX_PAGES} 页，无法证明完整性")


def changed_files(repo: str, token: str, pr: int, head_sha: str) -> list[str]:
    """完整读取 PR 变更文件（分页 + 计数对账 + head SHA 绑定）。"""
    return [item["filename"] for item in pr_files(repo, token, pr, head_sha)]


def pr_files(repo: str, token: str, pr: int, head_sha: str) -> list[dict]:
    """完整读取 PR 文件条目（含 status / previous_filename），供分类器使用。"""
    status, pr_obj = _call(repo, token, f"/repos/{repo}/pulls/{pr}")
    if status != 200 or not isinstance(pr_obj, dict):
        raise VerifyRefused(f"读取 PR #{pr} 失败: HTTP {status}")
    pr_head = ((pr_obj.get("head") or {}).get("sha"))
    if pr_head != head_sha:
        raise VerifyRefused(f"PR head 已漂移：{pr_head} != {head_sha}")
    declared = pr_obj.get("changed_files")
    if not isinstance(declared, int) or declared < 0:
        raise VerifyRefused("PR 响应缺少 changed_files 计数，无法证明 diff 完整")

    items: list[dict] = []
    for page in _iter_pr_files(repo, token, pr):
        for item in page:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                raise VerifyRefused("PR files 响应结构异常（缺少 filename）")
            items.append(item)
    names = [i["filename"] for i in items]
    if len(names) != len(set(names)):
        raise VerifyRefused("PR files 出现重复路径：无法证明清单完整")
    if len(names) != declared:
        raise VerifyRefused(f"变更文件数不一致：读取 {len(names)}，声明 {declared}（diff 不完整）")
    if not names:
        raise VerifyRefused("变更文件为空：拒绝在空 diff 上发布成功")
    return items


def scope_problems(files: list[str]) -> list[str]:
    """与 classify_change 一致的路径政策：治理面一律拒绝，只放行数据面。"""
    problems: list[str] = []
    for raw in files:
        try:
            path = cc.normalize_path(raw)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if path in FORBIDDEN_EXACT or cc.is_governance_path(path):
            problems.append(f"禁止修改门禁/脚本/测试/治理文件: {path}")
        elif not cc.is_data_path(path):
            problems.append(f"超出自动同步允许范围: {path}")
    return problems


def fetch_tree(repo: str, token: str, sha: str) -> dict[str, bytes]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/tarball/{sha}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "route-a-verify"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name.split("/", 1)[-1]
            handle = tar.extractfile(member)
            if handle is not None:
                out[name] = handle.read()
    if not out:
        raise VerifyRefused("候选树为空")
    return out


def check_rulesets(tree: dict[str, bytes]) -> list[str]:
    problems: list[str] = []
    dirs = {p.split("/")[1] for p in tree if p.startswith("ruleset/") and p.count("/") >= 2}
    for brand in sorted(dirs):
        yaml_path = f"ruleset/{brand}/{brand}.yaml"
        if yaml_path not in tree:
            problems.append(f"{brand}: 缺少 {yaml_path}")
            continue
        text = tree[yaml_path].decode("utf-8", "replace")
        if "payload:" not in text:
            problems.append(f"{brand}: {yaml_path} 缺少 payload")
        header = [ln for ln in text.splitlines()[:6] if ln.startswith("# Rule Name:")]
        if not header:
            problems.append(f"{brand}: {yaml_path} 缺少 # Rule Name")
    return problems


def check_configs(tree: dict[str, bytes]) -> list[str]:
    import yaml

    problems: list[str] = []
    expected = {p.split("/")[1] for p in tree if p.startswith("ruleset/") and p.count("/") >= 2}
    for label in ("configs/Android/config.yaml", "configs/Android/config.min.yaml",
                  "configs/Nikki/config.yaml", "configs/Nikki/config.min.yaml"):
        if label not in tree:
            problems.append(f"缺少 {label}")
            continue
        try:
            doc = yaml.safe_load(tree[label].decode("utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{label} 解析失败: {exc}")
            continue
        providers = set((doc.get("rule-providers") or {}).keys())
        groups = {g.get("name") for g in (doc.get("proxy-groups") or [])}
        missing = sorted(expected - providers)
        if missing:
            problems.append(f"{label}: 缺少 rule-provider {missing[:5]}（共 {len(missing)}）")
        if not groups:
            problems.append(f"{label}: 没有 proxy-groups")
    return problems


def verify(repo: str, token: str, base_sha: str, head_sha: str, pr: int) -> dict:
    files = changed_files(repo, token, pr, head_sha)
    problems = scope_problems(files)
    tree = fetch_tree(repo, token, head_sha)
    problems += check_rulesets(tree)
    problems += check_configs(tree)
    return {"ok": not problems, "changed_files": len(files), "problems": problems,
            "head_sha": head_sha, "base_sha": base_sha, "pr": pr}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Route A 受信候选验证")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("❌ 缺少 GITHUB_TOKEN")
        return 1
    try:
        report = verify(args.repo, token, args.base_sha, args.head_sha, args.pr)
    except (VerifyRefused, urllib.error.URLError, tarfile.TarError) as exc:
        report = {"ok": False, "problems": [str(exc)]}
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
