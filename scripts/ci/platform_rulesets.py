#!/usr/bin/env python3
"""platform_rulesets.py — 目标分支 Ruleset 的严格、可单测判定（单一来源）。

为什么单独成模块：`evaluate_candidate_policy._platform_state()` 与生产前置检查器
`verify_production_prerequisites.py` 必须用**同一套**语义判断"main 是否真的要求了
`route-a/verify`"，否则两处口径漂移就会造成"检查器说就绪、运行时说未就绪"。

判定原则（全部 fail-closed）：

1. 只认 `target == "branch"` 且 `enforcement == "active"` 的 ruleset；
2. 必须证明它**真的覆盖目标分支**：`conditions.ref_name.include` 命中目标 ref，
   且 `exclude` 不命中；`~DEFAULT_BRANCH` 只有在 API 读到的默认分支等于目标分支时才算命中；
3. required status context 用**精确相等**比较，不做子串/前缀匹配；
4. context 的来源（`integration_id`）必须是 GitHub Actions（15368），缺字段 = 无法验证 = 不通过；
5. 任何一个 ruleset 详情读不到（403/404/非 200/结构异常）都视为不可证明 → 不通过。

本模块不写任何东西：只有 GET。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

GITHUB_ACTIONS_INTEGRATION_ID = 15368
DEFAULT_BRANCH_TOKEN = "~DEFAULT_BRANCH"
ALL_REFS_TOKEN = "~ALL"


class PlatformError(RuntimeError):
    """API 不可读/响应异常：调用方必须按"无法证明"处理，不得降级为通过。"""


# ───────────────────────────── 纯函数（可单测） ─────────────────────────────

def normalize_ref(branch: str) -> str:
    b = (branch or "").strip()
    if not b:
        raise ValueError("分支名不能为空")
    return b if b.startswith("refs/") else f"refs/heads/{b}"


def _glob_match(pattern: str, ref: str) -> bool:
    """GitHub ref 条件里的 `*` 通配（只支持单层 `*`，与平台语义一致）。"""
    if pattern == ref:
        return True
    if "*" not in pattern:
        return False
    head, _, tail = pattern.partition("*")
    if not ref.startswith(head):
        return False
    rest = ref[len(head):]
    if not tail:
        return "/" not in rest
    idx = rest.rfind(tail)
    if idx < 0:
        return False
    return "/" not in rest[:idx]


def pattern_matches(pattern: str, ref: str, default_ref: str) -> bool:
    """判断单个 include/exclude 模式是否命中目标 ref。"""
    if not isinstance(pattern, str) or not pattern.strip():
        return False
    p = pattern.strip()
    if p == ALL_REFS_TOKEN:
        return True
    if p == DEFAULT_BRANCH_TOKEN:
        return ref == default_ref
    if not p.startswith("refs/"):
        # 平台允许写分支短名；只接受与目标分支名完全相等的情形
        return p == ref.split("/", 2)[-1] and ref.startswith("refs/heads/")
    return _glob_match(p, ref)


def ref_conditions(doc: dict) -> tuple[list[str], list[str]] | None:
    """取出 (include, exclude)；结构异常返回 None（= 无法证明覆盖）。"""
    cond = doc.get("conditions")
    if not isinstance(cond, dict):
        return None
    ref_name = cond.get("ref_name")
    if not isinstance(ref_name, dict):
        return None
    inc = ref_name.get("include")
    exc = ref_name.get("exclude") or []
    if not isinstance(inc, list) or not inc:
        return None
    if not isinstance(exc, list):
        return None
    if not all(isinstance(x, str) for x in inc + exc):
        return None
    return inc, exc


def covers_ref(doc: dict, ref: str, default_ref: str) -> tuple[bool, list[str]]:
    """该 ruleset 是否真的覆盖目标 ref（target/enforcement/conditions 三者同时成立）。"""
    reasons: list[str] = []
    if doc.get("target") != "branch":
        reasons.append("RULESET_TARGET_NOT_BRANCH")
    if doc.get("enforcement") != "active":
        reasons.append("RULESET_NOT_ACTIVE")
    conds = ref_conditions(doc)
    if conds is None:
        reasons.append("RULESET_CONDITIONS_MISSING")
        return False, reasons
    inc, exc = conds
    if any(pattern_matches(p, ref, default_ref) for p in exc):
        reasons.append("RULESET_EXCLUDES_TARGET")
        return False, reasons
    if not any(pattern_matches(p, ref, default_ref) for p in inc):
        reasons.append("RULESET_NOT_COVERING_TARGET")
        return False, reasons
    return not reasons, reasons


def required_status_checks(doc: dict) -> tuple[list[dict], list[str]]:
    """取出 required_status_checks（保留 context + integration_id 原值）。"""
    checks: list[dict] = []
    problems: list[str] = []
    rules = doc.get("rules")
    if not isinstance(rules, list):
        return [], ["RULESET_RULES_MISSING"]
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters")
        if not isinstance(params, dict):
            problems.append("REQUIRED_STATUS_PARAMS_MISSING")
            continue
        items = params.get("required_status_checks")
        if not isinstance(items, list):
            problems.append("REQUIRED_STATUS_LIST_MISSING")
            continue
        for item in items:
            if not isinstance(item, dict):
                problems.append("REQUIRED_STATUS_ENTRY_MALFORMED")
                continue
            checks.append({"context": item.get("context"),
                           "integration_id": item.get("integration_id")})
    return checks, problems


def evaluate(docs: list[dict], *, repo: str, target_branch: str,
             required_contexts: list[str],
             expected_integration_id: int = GITHUB_ACTIONS_INTEGRATION_ID,
             default_branch: str | None = None) -> dict:
    """对已取回的 ruleset 详情做严格判定。

    `docs`：`[{"id": int, "http": int, "body": dict|str}]`，按列表 API 顺序。
    `default_branch`：仓库真实默认分支（未提供则 `~DEFAULT_BRANCH` 视为无法验证）。
    """
    ref = normalize_ref(target_branch)
    default_ref = normalize_ref(default_branch) if default_branch else ""
    evidence: dict = {
        "repo": repo, "target_ref": ref, "required_contexts": list(required_contexts),
        "expected_integration_id": expected_integration_id,
        "rulesets": [], "covering_rulesets": [], "found_contexts": [],
    }
    reasons: list[str] = []
    soft_reasons: list[str] = []   # 单个 ruleset 的局部不满足：只有整体不通过时才上浮

    if not docs:
        return {"ok": False, "reason_codes": ["NO_RULESET_FOUND"], "evidence": evidence}

    unreadable = False
    for entry in docs:
        rid = entry.get("id")
        http = entry.get("http")
        body = entry.get("body")
        item = {"id": rid, "http": http, "readable": http == 200 and isinstance(body, dict)}
        if not item["readable"]:
            unreadable = True
            item["error"] = body if isinstance(body, str) else "unreadable"
            evidence["rulesets"].append(item)
            continue
        assert isinstance(body, dict)
        item.update({"name": body.get("name"), "target": body.get("target"),
                     "enforcement": body.get("enforcement"),
                     "source_type": body.get("source_type"), "source": body.get("source"),
                     "conditions": (body.get("conditions") or {}).get("ref_name")})
        covers, why = covers_ref(body, ref, default_ref)
        item["covers_target"] = covers
        item["cover_reasons"] = why
        if not covers:
            # 局部原因先记在 soft，避免"另一个分支的 ruleset"污染整体判定
            soft_reasons.extend(why)
        checks, check_problems = required_status_checks(body)
        item["required_status_checks"] = checks
        if check_problems:
            item["problems"] = check_problems
        evidence["rulesets"].append(item)
        if covers:
            evidence["covering_rulesets"].append(rid)
            for c in checks:
                if isinstance(c.get("context"), str):
                    evidence["found_contexts"].append(c["context"])

    if unreadable:
        reasons.append("RULESET_UNREADABLE")

    covering = [r for r in evidence["rulesets"] if r.get("covers_target")]
    if not covering:
        # 区分"没有任何 ruleset"与"有但不覆盖 main"，便于报告定位
        reasons.append("RULESET_NOT_COVERING_TARGET")

    for ctx in required_contexts:
        matched = [c for r in covering for c in r.get("required_status_checks") or []
                   if c.get("context") == ctx]
        if not matched:
            reasons.append(f"REQUIRED_STATUS_CONTEXT_MISSING:{ctx}")
            continue
        if not any(c.get("integration_id") == expected_integration_id for c in matched):
            reasons.append(f"REQUIRED_STATUS_SOURCE_UNVERIFIED:{ctx}")

    # context 名"近似但不精确"绝不能通过：这里额外记录近似项，便于审计定位
    near = sorted({c for c in evidence["found_contexts"]
                   if c not in required_contexts
                   and any(c.replace("_", "/") == r or r in c for r in required_contexts)})
    if near:
        evidence["near_miss_contexts"] = near

    ok = not reasons
    if not ok:
        reasons.extend(soft_reasons)
    return {"ok": ok, "reason_codes": sorted(set(reasons)), "evidence": evidence}


# ───────────────────────────── 只读采集 ─────────────────────────────

def _http_get(path: str, token: str, *, api: str = "https://api.github.com") -> tuple[int, object]:
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "route-a-rulesets"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PlatformError(f"GitHub API 不可用: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlatformError(f"GitHub API 返回非 JSON: {exc}") from exc


def fetch_ruleset_docs(repo: str, token: str, *, call=None,
                       api: str = "https://api.github.com") -> dict:
    """只读取回仓库默认分支 + 全部 ruleset 详情（分页不完整即视为不可证明）。

    返回 {"status": "ok"|"unreadable", "reason_codes": [...], "default_branch": str|None,
          "docs": [...], "ruleset_count": int}。
    `call(path) -> (status, obj)` 可注入，便于离线测试。
    """
    get = call or (lambda path: _http_get(path, token, api=api))

    st, repo_doc = get(f"/repos/{repo}")
    default_branch = None
    if st == 200 and isinstance(repo_doc, dict) and isinstance(repo_doc.get("default_branch"), str):
        default_branch = repo_doc["default_branch"]

    listing: list = []
    page = 1
    while True:
        st, payload = get(f"/repos/{repo}/rulesets?per_page=100&page={page}")
        if st != 200 or not isinstance(payload, list):
            return {"status": "unreadable", "reason_codes": [f"RULESET_LIST_HTTP_{st}"],
                    "default_branch": default_branch, "docs": [], "ruleset_count": 0}
        listing.extend(payload)
        if len(payload) < 100:
            break
        page += 1
        if page > 10:
            return {"status": "unreadable", "reason_codes": ["RULESET_LIST_PAGINATION_UNPROVEN"],
                    "default_branch": default_branch, "docs": [], "ruleset_count": len(listing)}

    docs: list[dict] = []
    for item in listing:
        if not isinstance(item, dict) or not item.get("id"):
            docs.append({"id": None, "http": 0, "body": "malformed ruleset list entry"})
            continue
        rid = item["id"]
        st, body = get(f"/repos/{repo}/rulesets/{rid}")
        docs.append({"id": rid, "http": st, "body": body})

    return {"status": "ok", "reason_codes": [], "default_branch": default_branch,
            "docs": docs, "ruleset_count": len(listing)}


def fetch_and_evaluate(repo: str, token: str, *, target_branch: str = "main",
                       required_contexts: list[str] | None = None,
                       api: str = "https://api.github.com", call=None) -> dict:
    """只读读取 + 严格判定（供运行时判定器与生产前置检查器共用）。"""
    required = list(required_contexts or ["route-a/verify"])
    fetched = fetch_ruleset_docs(repo, token, call=call, api=api)
    if fetched["status"] != "ok":
        return {"ok": False, "reason_codes": fetched["reason_codes"],
                "evidence": {"repo": repo, "default_branch": fetched["default_branch"]}}
    out = evaluate(fetched["docs"], repo=repo, target_branch=target_branch,
                   required_contexts=required, default_branch=fetched["default_branch"])
    out["evidence"]["default_branch"] = fetched["default_branch"]
    out["evidence"]["ruleset_count"] = fetched["ruleset_count"]
    return out
