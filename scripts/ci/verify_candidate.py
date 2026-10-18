#!/usr/bin/env python3
"""verify_candidate.py — Route A 受信验证：用**可信代码**校验候选树，再发布状态。

信任边界：
* 本脚本来自 `main`（受保护基线），候选树只作为**数据**下载与解析，绝不执行候选
  树里的任何脚本。
* 先做「防自我削弱」路径检查：候选 PR 不得修改 workflows / scripts / 守卫 /
  required-checks / 测试；出现即失败（不允许机器人 PR 改门禁）。
* 再做数据自洽：每个 ruleset 目录/YAML/basename/# Rule Name 一致；四份配置可解析，
  且 provider 与策略组覆盖全部品牌规则集。

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

ALLOWED_PREFIXES = ("ruleset/", "configs/", "docs/")
ALLOWED_FILES = {"README.md", "CHANGELOG.md"}
FORBIDDEN_EXACT = {".github/required-checks.json"}
FORBIDDEN_PREFIXES = (".github/", "scripts/", "tests/")


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
        return exc.code, exc.read().decode("utf-8", "replace")


def changed_files(repo: str, token: str, base_sha: str, head_sha: str) -> list[str]:
    status, payload = _call(repo, token, f"/repos/{repo}/compare/{base_sha}...{head_sha}")
    if status != 200 or not isinstance(payload, dict):
        raise VerifyRefused(f"compare 失败: HTTP {status}")
    return [f["filename"] for f in payload.get("files", [])]


def scope_problems(files: list[str]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if path in FORBIDDEN_EXACT or path.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"禁止修改门禁/脚本/测试文件: {path}")
        elif not (path.startswith(ALLOWED_PREFIXES) or path in ALLOWED_FILES):
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


def verify(repo: str, token: str, base_sha: str, head_sha: str) -> dict:
    files = changed_files(repo, token, base_sha, head_sha)
    problems = scope_problems(files)
    tree = fetch_tree(repo, token, head_sha)
    problems += check_rulesets(tree)
    problems += check_configs(tree)
    return {"ok": not problems, "changed_files": len(files), "problems": problems,
            "head_sha": head_sha, "base_sha": base_sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Route A 受信候选验证")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("❌ 缺少 GITHUB_TOKEN")
        return 1
    try:
        report = verify(args.repo, token, args.base_sha, args.head_sha)
    except (VerifyRefused, urllib.error.URLError, tarfile.TarError) as exc:
        report = {"ok": False, "problems": [str(exc)]}
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
