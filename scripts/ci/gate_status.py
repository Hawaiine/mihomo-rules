#!/usr/bin/env python3
"""gate_status.py — Route A 门禁数据源适配：check-run（人工 PR）与 commit status（候选 PR）。

两种模式，强度一致（都要求：精确 head SHA、全部必需项 success、来源可信）：

* check-run 模式：head SHA 上存在 `checks[]` 声明的 check-run（人工 PR，由 pull_request 触发）。
* status 模式：head SHA 上不存在任何 check-run 时，改用 `status_contexts[]` 声明的
  commit status（候选 PR 由 GITHUB_TOKEN 创建，不会触发 pull_request，也无法产生 check-run）。

来源校验：commit status 的 `creator.login` 必须是 `github-actions[bot]`；字段缺失视为
来源不可验证（fail-closed）。
"""
from __future__ import annotations

import json
from pathlib import Path

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


def evaluate_statuses(statuses: list[dict], contexts: list[str], expected_head: str) -> tuple[str, dict, list[str]]:
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
        if item.get("sha") and item["sha"] != expected_head:
            per[ctx] = {"state": STATE_FAILED, "reason": "status 绑定到其它 SHA"}
            problems.append(f"{ctx}: 状态绑定到 {item['sha'][:12]}，不是预期 head {expected_head[:12]}")
            continue
        creator = ((item.get("creator") or {}).get("login") or "")
        if creator != TRUSTED_STATUS_CREATOR:
            per[ctx] = {"state": STATE_FAILED, "reason": f"来源不可验证：creator={creator!r}"}
            problems.append(f"{ctx}: 状态来源 {creator!r} 不在可信名单（{TRUSTED_STATUS_CREATOR}）")
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


def choose_mode(check_runs: list[dict], contexts: list[str]) -> str:
    """有 check-run 用 check-run 模式；否则（候选 PR）用 status 模式。"""
    if check_runs:
        return "check-run"
    return "status" if contexts else "check-run"
