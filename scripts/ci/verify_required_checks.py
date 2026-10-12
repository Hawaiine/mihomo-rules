#!/usr/bin/env python3
"""required check ↔ PR head SHA 绑定验证器（只读）。

自动合并前必须证明：所有必需检查确实在 **PR 当前 head SHA** 上完成且成功，
且 PR 未发生未经验证的漂移（目标分支仍是 main、head SHA 未变）。
任何无法判定的情况一律 fail-closed（退出码非 0），不允许「假设通过」。

用法（CI 内由 candidate-pr-sync 调用，也可本地对公开仓库只读运行）：
  GITHUB_TOKEN=*** python3 scripts/ci/verify_required_checks.py \
      --repo Hawaiine/mihomo-rules --pr 123 [--head-sha <sha>] \
      [--required-checks .github/required-checks.json] [--json out.json]

退出码：0 = 全部满足；1 = 未满足或无法判定。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.github.com"
DEFAULT_REQUIRED_PATH = ".github/required-checks.json"
SUCCESS = {"success", "neutral", "skipped"}


class VerificationError(Exception):
    """无法判定 → fail-closed。"""


def _token(cli_token: str) -> str:
    token = cli_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        raise VerificationError("no token available (set GITHUB_TOKEN)")
    return token


def _get(path: str, token: str) -> Any:
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mihomo-rules-required-checks",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raise VerificationError(f"GET {path} -> HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VerificationError(f"GET {path} -> {exc}") from exc


def required_names(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read required checks manifest {path}: {exc}") from exc
    names = data.get("required")
    if not isinstance(names, list) or not names:
        raise VerificationError(f"{path}: 'required' must be a non-empty list")
    return list(names)


def check_runs(repo: str, sha: str, token: str) -> list[dict]:
    runs: list[dict] = []
    page = 1
    while True:
        payload = _get(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}", token)
        if not isinstance(payload, dict):
            raise VerificationError(f"unexpected check-runs payload for {sha}")
        runs.extend(payload.get("check_runs") or [])
        if len(runs) >= int(payload.get("total_count") or 0) or not payload.get("check_runs"):
            return runs
        page += 1
        if page > 20:
            raise VerificationError(f"check-runs pagination overflow for {sha}")


def verify(repo: str, pr_number: int, expected_head: str, names: list[str], token: str) -> dict:
    pull = _get(f"/repos/{repo}/pulls/{pr_number}", token)
    if not isinstance(pull, dict):
        raise VerificationError(f"cannot read PR #{pr_number}")
    state = pull.get("state")
    merged = pull.get("merged")
    base_ref = ((pull.get("base") or {}).get("ref")) or ""
    head_sha = ((pull.get("head") or {}).get("sha")) or ""
    problems: list[str] = []
    if state != "open":
        problems.append(f"PR #{pr_number} state is {state!r}, expected 'open'")
    if merged:
        problems.append(f"PR #{pr_number} is already merged")
    if base_ref != "main":
        problems.append(f"PR #{pr_number} base is {base_ref!r}, expected 'main'")
    if expected_head and head_sha != expected_head:
        problems.append(f"head SHA drifted: PR head={head_sha} expected={expected_head}")
    if not head_sha:
        raise VerificationError("PR head SHA unavailable")

    runs = check_runs(repo, head_sha, token)
    by_name: dict[str, list[dict]] = {}
    for run in runs:
        by_name.setdefault(str(run.get("name")), []).append(run)
    per_check: list[dict] = []
    for name in names:
        candidates = by_name.get(name, [])
        on_sha = [r for r in candidates if (r.get("head_sha") or head_sha) == head_sha]
        ok = bool(on_sha) and all(r.get("status") == "completed" for r in on_sha) and \
            all((r.get("conclusion") in SUCCESS) for r in on_sha)
        if not on_sha:
            problems.append(f"required check {name!r} has no check run on head SHA {head_sha[:12]}")
        elif not ok:
            detail = ", ".join(f"{r.get('status')}/{r.get('conclusion')}" for r in on_sha)
            problems.append(f"required check {name!r} not successful on head SHA: {detail}")
        per_check.append({
            "name": name,
            "runs_on_head_sha": len(on_sha),
            "conclusions": [r.get("conclusion") for r in on_sha],
            "satisfied": ok,
        })
    return {
        "ok": not problems,
        "repo": repo,
        "pr_number": pr_number,
        "state": state,
        "base_ref": base_ref,
        "head_sha": head_sha,
        "expected_head_sha": expected_head or None,
        "required": names,
        "checks": per_check,
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="required check ↔ PR head SHA 绑定验证（只读）")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--pr", type=int, required=True, help="PR 编号")
    ap.add_argument("--head-sha", default="", help="期望的 PR head SHA（漂移即失败）")
    ap.add_argument("--required-checks", default=DEFAULT_REQUIRED_PATH)
    ap.add_argument("--json", default="")
    ap.add_argument("--token", default="", help="令牌（默认读 GITHUB_TOKEN；不落盘、不打印）")
    args = ap.parse_args(argv)
    try:
        names = required_names(Path(args.required_checks))
        result = verify(args.repo, args.pr, args.head_sha, names, _token(args.token))
    except VerificationError as exc:
        result = {"ok": False, "problems": [f"fail-closed: {exc}"], "required": []}
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result["ok"]:
        print(f"✅ 必需检查全部在 head SHA {result['head_sha'][:12]} 上完成且成功：{', '.join(result['required'])}")
        return 0
    print("❌ required check ↔ head SHA 绑定验证失败：")
    for problem in result.get("problems", []):
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
