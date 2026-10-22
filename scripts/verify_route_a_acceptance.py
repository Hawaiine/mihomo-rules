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
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONTEXT = "route-a/verify"
EXPECTED_APP_ID = 15368
BASE_BRANCH = "main"
DEFAULT_REQUIRED_PATH = ".github/required-checks.json"
MERGE_BOX_QUERY = """query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){pullRequest(number:$pr){
    mergeStateStatus mergeable headRefOid baseRefOid}}}"""

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
STATUSES_PER_PAGE = 100
STATUSES_MAX_PAGES = 10


class ApiError(RuntimeError):
    """API 不可用 / 权限不足 / 响应畸形——必须与"验收失败"区分。"""


class Client:
    def __init__(self, token: str, api: str = "https://api.github.com"):
        if not token:
            raise ApiError("缺少 GITHUB_TOKEN（只读 token 即可）")
        self.token, self.api = token, api.rstrip("/")

    def graphql(self, query: str, variables: dict):
        req = urllib.request.Request(
            f"{self.api}/graphql", data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": "route-a-acceptance",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                doc = json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise ApiError(f"POST /graphql → HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(f"GraphQL 失败: {exc}") from exc
        if doc.get("errors"):
            raise ApiError(f"GraphQL errors: {str(doc['errors'])[:200]}")
        return doc.get("data") or {}

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
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # OSError 覆盖 RemoteDisconnected / ConnectionReset；ValueError 覆盖畸形 JSON
            raise ApiError(f"GET {path} 失败: {type(exc).__name__}: {exc}") from exc


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


EXTERNAL_ID_PREFIX = "route-a-run:"
# run id 后缀只允许 ASCII 十进制数字。必须显式 re.ASCII：
# 默认 \d 会匹配全角/阿拉伯-印度数字等 Unicode 数字，滥用后 int() 可能抛 ValueError，
# 导致"畸形输入 → 崩溃"而不是 fail-closed。
_RUN_ID_RE = re.compile(r"[0-9]+\Z", re.ASCII)
# 本项目防御性约定（**非** GitHub 官方限制）：GitHub run id 远小于 20 位。
EXTERNAL_ID_MAX_DIGITS = 20


def parse_external_id(value) -> str | None:
    """从 Check Run 的 ``external_id`` 严格提取发布器 run id。

    合法形态：``route-a-run:<positive_decimal_run_id>``。

    协议要求（任一不满足即返回 ``None``，交由调用层 fail-closed）：
      1. 输入必须是 ``str``；
      2. 必须以精确前缀 ``route-a-run:`` 开头；
      3. 前缀前后**不得**存在空白或其它字符（不做隐式 ``strip()`` 清洗）；
      4. 后缀只能含 ASCII ``0``-``9``；
      5. 后缀非空；
      6. 数值 > 0；
      7. 不允许前导零；
      8. 不允许符号/小数点/空白/斜线/查询串/额外后缀；
      9. 后缀长度 ≤ ``EXTERNAL_ID_MAX_DIGITS``（20，本项目约定）。

    解析器只做拒绝、不做清洗：畸形输入一律 ``None``，绝不"修正后接受"。
    """
    if not isinstance(value, str):
        return None
    # 关键：不做 strip()。前导/尾随空白属于畸形输入，必须整体拒绝。
    if not value.startswith(EXTERNAL_ID_PREFIX):
        return None
    tail = value[len(EXTERNAL_ID_PREFIX):]
    if not tail or len(tail) > EXTERNAL_ID_MAX_DIGITS:
        return None
    if _RUN_ID_RE.fullmatch(tail) is None:
        return None
    if tail[0] == "0":  # 前导零（含全零形态）
        return None
    # 格式与长度已收敛为 ASCII 数字，此处的 int() 不会抛出。
    if int(tail, 10) <= 0:
        return None
    return tail


def resolve_publisher_run_id(check_run: dict) -> tuple[str | None, str | None, bool]:
    """解析发布该 Check Run 的 workflow run id。

    返回 ``(run_id, source, conflict)``：
      * ``run_id`` 为 ``None`` 表示无法确定（调用方必须按证据不足处理）。
      * ``source`` 为 ``"external_id"`` / ``"details_url"`` / ``"malformed_external_id"`` / ``None``。
      * ``conflict=True`` 表示两个来源都给出**可解析但不同**的 run id——
        不得静默选择其中一个，调用方必须判证据不足。

    优先级：``external_id``（发布器显式写入的对象级绑定）> ``details_url``（兼容）。

    兼容策略（明确记录）：
      * ``external_id`` **缺失**（key 不存在或为 ``None``）→ 旧对象形态，允许回退
        ``details_url``。这是双发发布器上线前的历史 Check Run 的合法形态。
      * ``external_id`` **存在但畸形**（空串/空白/前缀错误/非 ASCII 数字等）→
        ``source="malformed_external_id"`` 且 ``run_id=None``，**不回退** details_url。
        理由是畸形绑定说明对象已损坏或来源可疑；此时静默改用另一来源会掩盖问题。
        调用方据此 fail-closed。
      * GitHub 对 API 创建的 Check Run 会忽略 details_url，回读得到 ``/runs/<check_run_id>``；
        该形态不含 ``/actions/runs/`` 标记，``parse_run_id`` 返回 ``None``，
        因此不会被误当成 workflow run id。
    """
    if not isinstance(check_run, dict):
        return None, None, False
    raw_external = check_run.get("external_id")
    from_external = parse_external_id(raw_external)
    from_details = parse_run_id(check_run.get("details_url") or "")
    if from_external and from_details and from_external != from_details:
        return None, None, True
    if from_external:
        return from_external, "external_id", False
    if raw_external is not None:
        # 存在但不可解析：视为绑定损坏，fail-closed，不回退 details_url。
        return None, "malformed_external_id", False
    if from_details:
        return from_details, "details_url", False
    return None, None, False


def fetch_statuses(client: Client, repo: str, sha: str, *, per_page: int = STATUSES_PER_PAGE,
                   max_pages: int = STATUSES_MAX_PAGES) -> dict:
    """按页读取 commit statuses（文档：reverse chronological，第一页最新）并返回目标 context。

    ``complete=False`` 表示分页未读全（不可读/畸形/超上限）——调用方必须按**证据不足**处理，
    不得把未读到的 ``route-a/verify`` 当作"不存在"。
    """
    items: list = []
    pages_read = 0
    for page in range(1, max_pages + 1):
        try:
            got = client.get(f"/repos/{repo}/commits/{sha}/statuses?per_page={per_page}&page={page}")
        except ApiError as exc:
            return {"items": items, "complete": False, "pages_read": pages_read,
                    "latest_target": None, "error": f"第 {page} 页读取失败：{exc}"}
        if not isinstance(got, list) or any(not isinstance(i, dict) for i in got):
            return {"items": items, "complete": False, "pages_read": pages_read,
                    "latest_target": None, "error": f"第 {page} 页响应畸形"}
        pages_read += 1
        items.extend(got)
        target = next((i for i in got if isinstance(i, dict)
                       and is_route_a_context(i.get("context"))), None)
        if target is not None:
            if page > 1:                          # 跨页命中 ⇒ 复读第 1 页检测并发更新
                try:
                    first = client.get(
                        f"/repos/{repo}/commits/{sha}/statuses?per_page={per_page}&page=1")
                except ApiError as exc:
                    return {"items": items, "complete": False, "pages_read": pages_read,
                            "latest_target": None, "error": f"跨页一致性复读失败：{exc}"}
                if not isinstance(first, list):
                    return {"items": items, "complete": False, "pages_read": pages_read,
                            "latest_target": None, "error": "跨页一致性复读响应畸形"}
                newer = next((i for i in first if isinstance(i, dict)
                              and is_route_a_context(i.get("context"))), None)
                if newer is not None and newer.get("id") != target.get("id"):
                    return {"items": items, "complete": False, "pages_read": pages_read,
                            "latest_target": None,
                            "error": "并发更新：复读第 1 页发现更新的 route-a/verify 状态"}
            return {"items": items, "complete": True, "pages_read": pages_read,
                    "latest_target": target, "error": ""}
        if len(got) < per_page:
            return {"items": items, "complete": True, "pages_read": pages_read,
                    "latest_target": None, "error": ""}
    return {"items": items, "complete": False, "pages_read": pages_read, "latest_target": None,
            "error": f"达到分页上限 {max_pages} 页仍未找到 {CONTEXT}"}


def collect(client: Client, repo: str, pr: int, head_sha: str) -> dict:
    ev: dict = {"repo": repo, "pr": pr, "expected_head_sha": head_sha}
    ev["pull"] = client.get(f"/repos/{repo}/pulls/{pr}")
    ev["main"] = client.get(f"/repos/{repo}/git/ref/heads/{BASE_BRANCH}")
    ev["check_runs"] = client.get(f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100")
    paged = fetch_statuses(client, repo, head_sha)
    ev["statuses"] = paged["items"]
    ev["statuses_complete"] = paged["complete"]
    ev["statuses_pages_read"] = paged["pages_read"]
    if not paged["complete"]:
        ev["statuses_error"] = paged["error"]
    if paged["latest_target"] is not None:
        ev["route_a_status_latest"] = paged["latest_target"]
    ev["combined"] = client.get(f"/repos/{repo}/commits/{head_sha}/status")
    # 若存在 route-a/verify check run，按其 details_url 取回发布它的 run（只读）
    crs = (ev.get("check_runs") or {}).get("check_runs") or []
    ra = next((c for c in crs if isinstance(c, dict) and c.get("name") == CONTEXT), None)
    if ra is not None:
        rid, _src, conflict = resolve_publisher_run_id(ra)
        if conflict:
            ev["publisher_run_binding_conflict"] = True
        elif rid:
            try:
                ev["publisher_run"] = client.get(f"/repos/{repo}/actions/runs/{rid}")
            except ApiError as exc:
                ev["publisher_run"] = None
                ev["publisher_run_error"] = str(exc)
    # merge box 只在 GraphQL 暴露；失败不掩盖其它证据，但会被记成"不可读"
    owner, name = repo.split("/", 1)
    try:
        data = client.graphql(MERGE_BOX_QUERY, {"owner": owner, "name": name, "pr": pr})
        pr_node = ((data.get("repository") or {}).get("pullRequest") or {})
        ev["merge_state_status"] = pr_node.get("mergeStateStatus")
        ev["graphql_head_oid"] = pr_node.get("headRefOid")
        ev["graphql_base_oid"] = pr_node.get("baseRefOid")
    except (ApiError, ValueError) as exc:
        ev["merge_state_status"] = None
        ev["merge_box_error"] = str(exc)
    return ev


def _pick(items, name: str) -> dict | None:
    for it in items or []:
        if isinstance(it, dict) and it.get("name") == name:
            return it
    return None


def is_route_a_context(value) -> bool:
    """GitHub commit status context 不区分大小写，比较必须 casefold。"""
    return isinstance(value, str) and value.casefold() == CONTEXT.casefold()


def _pick_status(items) -> dict | None:
    """status 选择与发布器同一语义：先按 context（大小写不敏感）过滤，再取最新（第一项）。"""
    hits = [it for it in items or [] if isinstance(it, dict) and is_route_a_context(it.get("context"))]
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
        "merge_state_status": ev.get("merge_state_status"),
        "merge_box_error": ev.get("merge_box_error"),
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

    if ev.get("statuses_complete") is False:
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "STATUS_PAGINATION_INCOMPLETE",
                             "detail": (f"commit statuses 分页未读全（已读 {ev.get('statuses_pages_read')} 页）："
                                        f"{ev.get('statuses_error')}；未读到的 {CONTEXT} 不得当作不存在")}],
                "facts": facts}

    # 6) route-a/verify 的来源与状态
    ra_cr = _pick(cr_items, CONTEXT)
    ra_status = _pick_status(ev.get("statuses") or [])
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
        facts["app_identity_proof"] = "check_run.app.id"
        facts["evidence_level"] = "object_app_id"
        run_id, run_source, binding_conflict = resolve_publisher_run_id(ra_cr)
        facts["publisher_run_id"] = run_id
        facts["publisher_run_id_source"] = run_source
        if binding_conflict:
            return {"verdict": EVIDENCE_INSUFFICIENT,
                    "reasons": [{"code": "PUBLISHER_RUN_BINDING_CONFLICT",
                                 "detail": (f"check run 的 external_id={ra_cr.get('external_id')!r} 与 "
                                            f"details_url={ra_cr.get('details_url')!r} 指向不同的 run；"
                                            f"绑定证据互相矛盾，不得静默选择其一")}], "facts": facts}
        if run_source == "malformed_external_id":
            return {"verdict": EVIDENCE_INSUFFICIENT,
                    "reasons": [{"code": "PUBLISHER_RUN_MALFORMED_EXTERNAL_ID",
                                 "detail": (f"check run 的 external_id="
                                            f"{ra_cr.get('external_id')!r} 存在但不符合 "
                                            f"route-a-run:<ASCII 正整数> 协议；绑定已损坏，"
                                            f"不回退 details_url（避免掩盖来源异常）")}],
                    "facts": facts}
        if not run_id:
            return {"verdict": EVIDENCE_INSUFFICIENT,
                    "reasons": [{"code": "PUBLISHER_RUN_UNBOUND",
                                 "detail": ("check run 既无合法 external_id（route-a-run:<run_id>）"
                                            "也无可解析的 details_url，无法证明它由哪次 PR verify 发布")}],
                    "facts": facts}
        pub_run = ev.get("publisher_run") or {}
        facts["publisher_run"] = {k: pub_run.get(k) for k in
                                  ("id", "name", "path", "event", "head_sha", "conclusion")}
        if not pub_run:
            return {"verdict": EVIDENCE_INSUFFICIENT,
                    "reasons": [{"code": "PUBLISHER_RUN_UNREADABLE",
                                 "detail": f"无法读取 run {run_id}（API/权限/已删除）"}],
                    "facts": facts}
        if (pub_run.get("head_sha") != head or pub_run.get("event") != "pull_request"
                or pub_run.get("conclusion") != "success"):
            return {"verdict": SOURCE_MISMATCH,
                    "reasons": [{"code": "PUBLISHER_RUN_MISMATCH",
                                 "detail": (f"发布该 check run 的 run {run_id} "
                                            f"head={pub_run.get('head_sha')} "
                                            f"event={pub_run.get('event')} "
                                            f"conclusion={pub_run.get('conclusion')}，"
                                            f"与 PR head/事件/结论不符")}],
                    "facts": facts}
        if str(pub_run.get("id")) != str(run_id):
            return {"verdict": SOURCE_MISMATCH,
                    "reasons": [{"code": "PUBLISHER_RUN_MISMATCH",
                                 "detail": (f"check run 绑定的 run id={run_id}"
                                            f"（来源 {run_source}），但取回的 run 对象 id="
                                            f"{pub_run.get('id')}；对象与绑定不一致")}],
                    "facts": facts}
    elif ra_status is not None and ra_status.get("state") == "success":
        # 只有 status：对象本身没有 app.id。唯一能证明"平台接受了该来源"的证据是 merge box。
        if ev.get("merge_state_status") != "CLEAN":
            return {"verdict": EVIDENCE_INSUFFICIENT,
                    "reasons": [{"code": "STATUS_ONLY_NO_APP_ID",
                                 "detail": (f"仅见 commit status（creator={facts['route_a_status_creator']}）；"
                                            f"status 对象无 app.id，且 mergeStateStatus="
                                            f"{ev.get('merge_state_status')!r} 不能证明平台接受该来源。"
                                            f"需 check run 证据或 merge box=CLEAN。")}],
                    "facts": facts}
        facts["app_identity_proof"] = "platform_merge_box_clean (status 对象无 app.id)"
        facts["evidence_level"] = "platform_merge_box"
        facts["object_level_source_proof"] = False
    else:
        return {"verdict": PUBLISHER_NOT_RUN,
                "reasons": [{"code": "NO_ROUTE_A_CONTEXT",
                             "detail": f"{CONTEXT} 既无 check run 也无 success status"}],
                "facts": facts}

    # 7) merge box / 可合并状态（GraphQL 优先，REST 兜底）
    ms = (ev.get("merge_state_status") or pull.get("mergeable_state") or "").upper() or None
    facts["combined_state"] = (ev.get("combined") or {}).get("state")
    if ms in ("CLEAN", "UNSTABLE"):
        pass
    elif ms == "BEHIND":
        return {"verdict": BASELINE_DRIFT,
                "reasons": [{"code": "MERGE_STATE_BEHIND",
                             "detail": "merge state=BEHIND：需先同步最新 main"}], "facts": facts}
    elif ms == "BLOCKED":
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MERGE_STATE_BLOCKED_OTHER_RULES",
                             "detail": ("merge state=BLOCKED 而 route-a/verify 已 success；"
                                        "阻断来自其它规则或待审批，不是发布器失败。"
                                        "需 GitHub 页面 merge box 明细以定性。")}], "facts": facts}
    elif ms == "DIRTY":
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MERGE_CONFLICT", "detail": "merge state=DIRTY"}],
                "facts": facts}
    else:
        return {"verdict": EVIDENCE_INSUFFICIENT,
                "reasons": [{"code": "MERGEABLE_UNKNOWN",
                             "detail": f"merge state={ms!r}；GitHub 尚未算完或 GraphQL 不可读，"
                                       f"需页面证据"}], "facts": facts}

    return {"verdict": PASS,
            "reasons": [{"code": "REQUIRED_CONTEXT_ACCEPTED",
                         "detail": (f"{CONTEXT} 在 {head} 上满足必需项，三必需 check 均 success，"
                                    f"merge state={ms}（平台接受该 context）")}],
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
