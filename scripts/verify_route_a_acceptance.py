#!/usr/bin/env python3
"""Route A 功能验收（**只读**）。

判定 ``route-a/verify`` 是否真的能在真实 PR 上满足 ``protect-main`` Ruleset 的
required status check，并把"为什么没通过"拆成互斥类别，而不是笼统的"没绿"。

只读保证：本脚本只发 GET；不创建 check run、不发布 status、不改 Ruleset、
不改任何仓库设置。可以用无写权限的 token 运行。

用法::

    python3 scripts/verify_route_a_acceptance.py --repo Hawaiine/mihomo-rules \
        --pr 31 --head-sha <40 位 SHA> [--json out.json] [--expect-app-id 15368]

退出码：0 = FUNCTIONAL_ACCEPTANCE_PASS；1 = 其它判定；3 = API/权限错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONTEXT = "route-a/verify"
EXPECTED_APP_ID = 15368
BASE_BRANCH = "main"
DEFAULT_REQUIRED_PATH = ".github/required-checks.json"

# 判定分类（互斥，按优先级求值）
PASS = "FUNCTIONAL_ACCEPTANCE_PASS"
PUBLISHER_NOT_RUN = "PUBLISHER_NOT_RUN"
BASELINE_DRIFT = "BASELINE_DRIFT"
SHA_MISMATCH = "SHA_MISMATCH"
SOURCE_MISMATCH = "SOURCE_MISMATCH"
REQUIRED_CONTEXT_UNSATISFIED = "REQUIRED_CONTEXT_UNSATISFIED"
EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
API_OR_PERMISSION_ERROR = "API_OR_PERMISSION_ERROR"

VERDICTS = (PASS, PUBLISHER_NOT_RUN, BASELINE_DRIFT, SHA_MISMATCH, SOURCE_MISMATCH,
            REQUIRED_CONTEXT_UNSATISFIED, EVIDENCE_INSUFFICIENT, API_OR_PERMISSION_ERROR)

CLEAN_MERGE_STATES = {"clean", "unstable"}


class ApiError(RuntimeError):
    """API 不可用 / 权限不足 / 响应畸形——必须与"验收失败"区分。"""


class Client:
    def __init__(self, token: str, api: str = "https://api.github.com"):
        if not token:
            raise ApiError("缺少 GITHUB_TOKEN（只读 token 即可）")
        self.token, self.api = token, api.rstrip("/")

    def get(self, path: str):
        req = urllib.request.Request(
            f"{self.api}{path}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "route-a-acceptance"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise ApiError(f"GET {path} → HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(f"GET {path} 失败: {exc}") from exc


def load_required(path: str) -> list[str]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    names = [c["name"] for c in doc.get("checks") or []
             if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"]]
    if not names:
        raise ValueError(f"{path} 未登记任何必需 check")
    return names


def parse_run_id(url: str) -> str | None:
    """从 details_url/target_url 严格提取 run id（拒绝畸形与前缀碰撞）。"""
    if not isinstance(url, str):
        return None
    marker = "/actions/runs/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if not tail.isdigit() or tail != str(int(tail)) or int(tail) < 1:
        return None
    return tail


def collect(client: Client, repo: str, pr: int, head_sha: str) -> dict:
    ev: dict = {"repo": repo, "pr": pr, "expected_head_sha": head_sha}
    ev["pull"] = client.get(f"/repos/{repo}/pulls/{pr}")
    ev["main"] = client.get(f"/repos/{repo}/git/ref/heads/{BASE_BRANCH}")
    ev["check_runs"] = client.get(f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100")
    ev["statuses"] = client.get(f"/repos/{repo}/commits/{head_sha}/statuses?per_page=100")
    ev["combined"] = client.get(f"/repos/{repo}/commits/{head_sha}/status")
    return ev


def _pick(items, name: str) -> dict | None:
    for it in items or []:
        if isinstance(it, dict) and it.get("name") == name:
            return it
    return None


def _pick_status(items, context: str) -> dict | None:
    hits = [it for it in items or [] if isinstance(it, dict) and it.get("context") == context]
    return hits[0] if hits else None


def classify(ev: dict, required: list[str], expect_app_id: int = EXPECTED_APP_ID) -> dict:
    """纯函数：输入已采集证据，输出判定。不打网络。"""
    reasons: list[dict] = []
    pull = ev.get("pull") or {}
    main_tip = ((ev.get("main") or {}).get("object") or {}).get("sha")
    head = (pull.get("head") or {}).get("sha")
    base_ref = (pull.get("base") or {}).get("ref")
    base_sha = (pull.get("base") or {}).get("sha")

    facts = {
        "pr_state": pull.get("state"), "pr_merged": pull.get("merged"),
        "pr_head_sha": head, "pr_base_ref": base_ref, "pr_base_sha": base_sha,
        "main_tip": main_tip, "expected_head_sha": ev.get("expected_head_sha"),
        "mergeable": pull.get("mergeable"), "mergeable_state": pull.get("mergeable_state"),
    }
    if not isinstance(main_tip, str) or not main_tip:
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MAIN_TIP_UNREADABLE",
                             "detail": "无法读取默认分支 tip，基线关系不可判定"}],
                "facts": facts}

    # 1) PR 必须 open
    if pull.get("state") != "open" or pull.get("merged"):
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "PR_NOT_OPEN",
                             "detail": f"PR state={pull.get('state')} merged={pull.get('merged')}，"
                                       f"验收对象已失效"}], "facts": facts}

    # 2) head SHA 精确匹配
    if head != ev.get("expected_head_sha"):
        return {"verdict": SHA_MISMATCH,
                "reasons": [{"code": "HEAD_SHA_MISMATCH",
                             "detail": f"PR head={head} 与指定 {ev.get('expected_head_sha')} 不一致"}],
                "facts": facts}

    # 3) base 分支
    if base_ref != BASE_BRANCH:
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "BASE_BRANCH_NOT_MAIN", "detail": f"base ref={base_ref}"}],
                "facts": facts}

    # 4) 基线漂移（发布器 verify_pr 要求 base SHA == main tip）
    if base_sha != main_tip:
        return {"verdict": BASELINE_DRIFT,
                "reasons": [{"code": "BASE_SHA_BEHIND_MAIN_TIP",
                             "detail": (f"PR base={base_sha} 但 main tip={main_tip}；发布器会 "
                                        f"Refused(BASELINE_DRIFT)，不是 App 来源不兼容。"
                                        f"处置：把 PR rebase/merge 到最新 main 后等新 run。")}],
                "facts": facts}

    # 5) 三个必需 check-run 必须在同一 SHA 上 success 且来自 Actions App
    cr_items = (ev.get("check_runs") or {}).get("check_runs") or []
    missing, failed, wrong_app = [], [], []
    for name in required:
        cr = _pick(cr_items, name)
        if cr is None:
            missing.append(name)
        elif cr.get("conclusion") != "success" or cr.get("status") != "completed":
            failed.append(f"{name}:{cr.get('status')}/{cr.get('conclusion')}")
        elif ((cr.get("app") or {}).get("id")) != expect_app_id:
            wrong_app.append(f"{name}:{(cr.get('app') or {}).get('id')}")
    if missing or failed or wrong_app:
        return {"verdict": REQUIRED_CONTEXT_UNSATISFIED,
                "reasons": [{"code": "REQUIRED_CHECKS_NOT_SATISFIED",
                             "detail": f"missing={missing} failed={failed} wrong_app={wrong_app}"}],
                "facts": facts}

    # 6) route-a/verify 的来源与状态
    ra_cr = _pick(cr_items, CONTEXT)
    ra_status = _pick_status((ev.get("statuses") or []), CONTEXT)
    facts["route_a_check_run"] = ({k: ra_cr.get(k) for k in
                                   ("id", "name", "head_sha", "status", "conclusion",
                                    "external_id", "details_url")} if ra_cr else None)
    facts["route_a_check_run_app_id"] = ((ra_cr.get("app") or {}).get("id")) if ra_cr else None
    facts["route_a_status"] = ({k: ra_status.get(k) for k in
                                ("context", "state", "target_url", "created_at")}
                               if ra_status else None)
    facts["route_a_status_creator"] = (ra_status or {}).get("creator")
    facts["note_status_creator_is_not_app_id"] = True

    if ra_cr is not None:
        app_id = (ra_cr.get("app") or {}).get("id")
        if app_id != expect_app_id:
            return {"verdict": SOURCE_MISMATCH,
                    "reasons": [{"code": "CHECK_RUN_APP_ID_MISMATCH",
                                 "detail": f"{CONTEXT} check run app.id={app_id}，期望 {expect_app_id}"}],
                    "facts": facts}
        if ra_cr.get("head_sha") != head:
            return {"verdict": SHA_MISMATCH,
                    "reasons": [{"code": "CHECK_RUN_HEAD_SHA_MISMATCH",
                                 "detail": f"check run head_sha={ra_cr.get('head_sha')} != {head}"}],
                    "facts": facts}
        if ra_cr.get("status") != "completed" or ra_cr.get("conclusion") != "success":
            return {"verdict": REQUIRED_CONTEXT_UNSATISFIED,
                    "reasons": [{"code": "CHECK_RUN_NOT_SUCCESS",
                                 "detail": f"{ra_cr.get('status')}/{ra_cr.get('conclusion')}"}],
                    "facts": facts}
        run_id = parse_run_id(ra_cr.get("details_url") or "")
        facts["publisher_run_id"] = run_id
        if not run_id:
            return {"verdict": EVIDENCE_INSUFFICIENT,
                    "reasons": [{"code": "PUBLISHER_RUN_UNBOUND",
                                 "detail": "check run details_url 无法解析出 run id，"
                                           "无法证明它由哪次 PR verify 发布"}], "facts": facts}
    elif ra_status is not None and ra_status.get("state") == "success":
        # 只有 status：creator=github-actions[bot] 不能证明 App 归属（status 无 app 字段）
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "STATUS_ONLY_NO_APP_ID",
                             "detail": (f"仅见 commit status（creator={facts['route_a_status_creator']}）；"
                                        f"status 对象没有 app.id，无法证明来源=15368。"
                                        f"需 check run 证据或 GitHub merge box 页面证据。")}],
                "facts": facts}
    else:
        return {"verdict": PUBLISHER_NOT_RUN,
                "reasons": [{"code": "NO_ROUTE_A_CONTEXT",
                             "detail": f"{CONTEXT} 既无 check run 也无 success status"}],
                "facts": facts}

    # 7) merge box / 可合并状态
    ms = pull.get("mergeable_state")
    facts["combined_state"] = (ev.get("combined") or {}).get("state")
    if ms in CLEAN_MERGE_STATES:
        pass
    elif ms == "behind":
        return {"verdict": BASELINE_DRIFT,
                "reasons": [{"code": "MERGE_STATE_BEHIND",
                             "detail": "mergeable_state=behind：需先同步最新 main"}], "facts": facts}
    elif ms == "blocked":
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MERGE_STATE_BLOCKED_OTHER_RULES",
                             "detail": ("mergeable_state=blocked，而 route-a/verify 已 success；"
                                        "阻断来自其它规则或待审批，不是发布器失败。"
                                        "需 GitHub 页面 merge box 明细以定性。")}], "facts": facts}
    elif ms == "dirty":
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MERGE_CONFLICT", "detail": "mergeable_state=dirty"}],
                "facts": facts}
    else:
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MERGEABLE_UNKNOWN",
                             "detail": f"mergeable_state={ms!r}；GitHub 尚未算完，重试或看页面"}],
                "facts": facts}

    return {"verdict": PASS,
            "reasons": [{"code": "REQUIRED_CONTEXT_ACCEPTED",
                         "detail": (f"{CONTEXT} 由 app {expect_app_id} 发布于 {head}，"
                                    f"三必需 check 均 success，mergeable_state={ms}")}],
            "facts": facts}


def run(*, repo: str, pr: int, head_sha: str, token: str, api: str,
        required_path: str, expect_app_id: int) -> dict:
    client = Client(token, api)
    required = load_required(required_path)
    try:
        ev = collect(client, repo, pr, head_sha)
    except ApiError as exc:
        return {"verdict": API_OR_PERMISSION_ERROR,
                "reasons": [{"code": "API_ERROR", "detail": str(exc)}],
                "facts": {"repo": repo, "pr": pr, "expected_head_sha": head_sha},
                "read_only": True,
                "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    out = classify(ev, required, expect_app_id)
    out["facts"]["required_checks"] = required
    out["read_only"] = True
    out["checked_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Route A 功能验收（只读）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--required-checks", default=DEFAULT_REQUIRED_PATH)
    ap.add_argument("--expect-app-id", type=int, default=EXPECTED_APP_ID)
    ap.add_argument("--api", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)
    try:
        out = run(repo=args.repo, pr=args.pr, head_sha=args.head_sha,
                  token=os.environ.get("GITHUB_TOKEN", ""), api=args.api,
                  required_path=args.required_checks, expect_app_id=args.expect_app_id)
    except (ValueError, OSError) as exc:
        out = {"verdict": API_OR_PERMISSION_ERROR,
               "reasons": [{"code": "LOCAL_INPUT_ERROR", "detail": str(exc)}], "facts": {}}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(out, args.json, ensure_ascii=False, indent=1)
        args.json.close()
    return 0 if out["verdict"] == PASS else (3 if out["verdict"] == API_OR_PERMISSION_ERROR else 1)


if __name__ == "__main__":
    sys.exit(main())
