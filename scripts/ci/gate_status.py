#!/usr/bin/env python3
"""gate_status.py — Route A 门禁数据源适配：check-run（人工 PR）与 commit status（候选 PR）。

两种模式，强度一致（都要求：精确 head SHA、全部必需项 success、来源可信）：

* check-run 模式：head SHA 上存在 `checks[]` 声明的 check-run（人工 PR，由 pull_request 触发）。
* status 模式：head SHA 上不存在任何 check-run 时，改用 `status_contexts[]` 声明的
  commit status（候选 PR 由 GITHUB_TOKEN 创建，不会触发 pull_request，也无法产生 check-run）。
  调用方给出 `expected_run_id` 时，status 的 `target_url` 必须指向该 run，否则判失败
  （防止同一 head 上旧 run 的 success 被复用）。

来源校验：commit status 的 `creator.login` 必须是 `github-actions[bot]`；字段缺失视为
来源不可验证（fail-closed）。
"""
from __future__ import annotations

import json
from pathlib import Path


import re
from urllib.parse import urlparse

_RUN_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/actions/runs/(\d+)(?:/.*)?$")

def parse_run_url(url: str) -> dict | None:
    """严格解析 GitHub Actions run URL 并提取 (owner, repo, run_id)。

    拒绝：非 github.com 主机、路径不含 /actions/runs/<id>、run_id 非纯数字、
    run_id 前导零、owner/repo 段为空、URL 含 query/fragment 混淆。
    返回 None 表示不可解析——调用方必须 fail-closed。
    """
    if not isinstance(url, str) or not url:
        return None
    m = _RUN_URL_RE.match(url)
    if not m:
        return None
    owner, repo, rid_str = m.group(1), m.group(2), m.group(3)
    # 拒绝 "01" 之类的前导零变体；拒绝非数字
    if rid_str != str(int(rid_str)):
        return None
    run_id = int(rid_str)
    if run_id < 1:
        return None
    return {"owner": owner, "repo": repo, "run_id": str(run_id)}


def validate_run_binding(target_url: str, expected_run_id: str, expected_repo: str = "") -> str:
    """检查 target_url 是否绑定到 expected_run_id。返回空字符串表示通过；返回非空为问题描述。

    严格口径：必须可解析、owner+repo 可验证（若给定 expected_repo）、run_id 精确匹配。
    前缀碰撞（1001 vs 10010）、非 github.com、畸形 URL 一律拒绝。
    """
    if not expected_run_id:
        return ""   # 调用方未指定，不做检查
    parsed = parse_run_url(target_url)
    if parsed is None:
        return f"status target_url 不可解析为合法 GitHub Actions run: {target_url!r}"
    if expected_repo:
        want_owner, _, want_repo = expected_repo.partition("/")
        if parsed["owner"] != want_owner or parsed["repo"] != want_repo:
            return (f"status target_url 指向 {parsed['owner']}/{parsed['repo']}"
                    f"，非预期仓库 {expected_repo}")
    if parsed["run_id"] != str(expected_run_id):
        return (f"status target_url run_id={parsed['run_id']}，非 {expected_run_id}"
                f"（拒绝前缀碰撞/模糊匹配）")
    return ""


TRUSTED_STATUS_CREATOR = "github-actions[bot]"
STATE_SUCCESS, STATE_FAILED, STATE_PENDING, STATE_MISSING = "success", "failed", "pending", "missing"


def load_status_contexts(manifest_path: str | Path) -> list[str]:
    try:
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for item in doc.get("status_contexts") or []:
        if isinstance(item, dict) and isinstance(item.get("context"), str) and item["context"]:
            out.append(item["context"])
    return out


def latest_statuses(statuses: list[dict]) -> dict[str, dict]:
    """同一 context 取最新一条（API 按时间倒序返回）。"""
    latest: dict[str, dict] = {}
    for item in statuses:
        ctx = item.get("context")
        if isinstance(ctx, str) and ctx not in latest:
            latest[ctx] = item
    return latest


def evaluate_statuses(statuses: list[dict], contexts: list[str], expected_head: str,
                      expected_run_id: str = "") -> tuple[str, dict, list[str]]:
    """返回 (overall_state, per_context, problems)。

    overall_state: success（全部 success）/ failed（任一终局失败或来源不可信）/ pending（尚未出现）。
    """
    problems: list[str] = []
    latest = latest_statuses(statuses)
    per: dict[str, dict] = {}
    if not contexts:
        return STATE_MISSING, per, ["manifest 未声明 status_contexts：无法使用 status 模式"]
    for ctx in contexts:
        item = latest.get(ctx)
        if item is None:
            per[ctx] = {"state": STATE_MISSING}
            continue
        state = item.get("state")
        # 严格 SHA 校验：字段缺失同样拒绝（禁止"缺失即跳过"的绕过）
        sha = item.get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            per[ctx] = {"state": STATE_FAILED, "reason": "status 缺少 sha 或格式非法"}
            problems.append(f"{ctx}: status 缺少合法 sha，拒绝采信")
            continue
        if sha != expected_head:
            per[ctx] = {"state": STATE_FAILED, "reason": "status 绑定到其它 SHA"}
            problems.append(f"{ctx}: 状态绑定到 {sha[:12]}，不是预期 head {expected_head[:12]}")
            continue
        creator_obj = item.get("creator") or {}
        creator = creator_obj.get("login") or ""
        if creator != TRUSTED_STATUS_CREATOR:
            per[ctx] = {"state": STATE_FAILED, "reason": f"来源不可验证：creator={creator!r}"}
            problems.append(f"{ctx}: 状态来源 {creator!r} 不在可信名单（{TRUSTED_STATUS_CREATOR}）")
            continue
        if creator_obj.get("type") != "Bot":
            per[ctx] = {"state": STATE_FAILED, "reason": f"来源类型不可信：type={creator_obj.get('type')!r}"}
            problems.append(f"{ctx}: 状态来源类型 {creator_obj.get('type')!r} 不是 Bot（拒绝仅凭 login 采信）")
            continue
        if expected_run_id:
            # 抗重放：同一 head 上**别的 run** 留下的 success 不能当作本次结果
            # 严格解析 URL（拒绝前缀碰撞如 1001 vs 10010、非 github.com、畸形 URL）
            target = item.get("target_url") or ""
            err = validate_run_binding(target, expected_run_id)
            if err:
                per[ctx] = {"state": STATE_FAILED, "reason": err}
                problems.append(f"{ctx}: {err}")
                continue
        if state == "success":
            per[ctx] = {"state": STATE_SUCCESS, "target_url": item.get("target_url", "")}
        elif state in ("failure", "error"):
            per[ctx] = {"state": STATE_FAILED, "state_raw": state}
            problems.append(f"{ctx}: 状态为 {state}（严格口径仅接受 success）")
        else:
            per[ctx] = {"state": STATE_PENDING, "state_raw": state}
    states = [v["state"] for v in per.values()]
    if any(s == STATE_FAILED for s in states):
        return STATE_FAILED, per, problems
    if all(s == STATE_SUCCESS for s in states):
        return STATE_SUCCESS, per, []
    missing = [k for k, v in per.items() if v["state"] == STATE_MISSING]
    if missing:
        problems.append(f"缺少状态：{', '.join(missing)}")
    return STATE_PENDING, per, problems


BOT_LOGIN = "github-actions[bot]"
CHECK_RUN_MODE, STATUS_MODE = "check-run", "status"


class GateModeError(RuntimeError):
    """无法确定预期门禁模式：一律 fail-closed（绝不为兼容而自动降级）。"""


def expected_mode(pr_obj: dict) -> str:
    """按 PR 作者确定**唯一**预期模式（P0-3：不做运行期降级）。

    * 作者是 `github-actions[bot]` → 该 PR 由 GITHUB_TOKEN 创建，不可能有 check-run，
      预期 status 模式。
    * 其它作者 → 人工 PR，必须有 check-run；缺失即失败，不退回 status。
    * 作者字段缺失/未知 → 直接失败。
    """
    user = (pr_obj or {}).get("user") or {}
    login = user.get("login")
    if not isinstance(login, str) or not login:
        raise GateModeError("PR 作者不可确定：无法固定预期门禁模式")
    utype = user.get("type")
    if login == BOT_LOGIN:
        if utype != "Bot":
            raise GateModeError(f"作者 {login!r} 的 type={utype!r}，非 Bot（拒绝仅凭 login 判定）")
        return STATUS_MODE
    if utype != "User":
        raise GateModeError(f"作者 {login!r} 的 type={utype!r}，既非 Bot 也非 User，无法确定模式")
    return CHECK_RUN_MODE
