#!/usr/bin/env python3
"""publish_run_status.py — P0-1 受信状态发布器（人工 PR 路径）。

运行在**默认分支**上下文（`workflow_run` 指向的 workflow 由 main 版本定义），因此执行
逻辑可信；候选内容只作为 API 数据被检查，绝不检出、绝不解包、绝不执行。

发布前逐项核实（任一不符即拒绝，不发布成功状态）：

1. 该 run 属于本仓库、事件为 `pull_request`、名称匹配、状态 completed+success；
2. 该 run 的全部 job 名称覆盖必需检查集合且全部 success（skipped 视为不通过）；
3. PR 仍 open、base 为 `main`、base SHA 等于当前 `refs/heads/main`、head SHA 等于目标 SHA；
4. 发布后回读状态与 PR head；head 已变化则整轮判失败。

exit: 0 成功；1 拒绝/失败（原因见 stderr 与 --json）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_status  # noqa: E402
from gate_status import parse_run_url  # noqa: E402

CONTEXT = "route-a/verify"
EXPECTED_RUN_NAME = "General PR verification"
EXPECTED_RUN_PATH = ".github/workflows/pr-verify.yml"
TRUSTED_BOT = "github-actions[bot]"
TRUSTED_CREATOR_TYPE = "Bot"
# GitHub Actions App。Check Run 返回对象带 ``app.id``，可据此**直接**证明发布来源；
# commit status 对象没有 app 字段，只能校验 creator，因此不再作为唯一证据来源。
EXPECTED_APP_ID = 15368
EXTERNAL_ID_PREFIX = "route-a-run:"
PUBLISH_MODES = ("status", "check-run", "dual")


class Refused(RuntimeError):
    pass


class Client:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        if not token:
            raise Refused("缺少 GITHUB_TOKEN")
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, path: str) -> tuple[int, object]:
        return _request(self, "GET", path)

    def patch(self, path: str, body: dict) -> tuple[int, object]:
        return _request(self, "PATCH", path, body)

    def post(self, path: str, body: dict) -> tuple[int, object]:
        return _request(self, "POST", path, body)


def _request(client: Client, method: str, path: str, body: dict | None = None):
    """统一 HTTP 传输：HTTP 状态码与**传输异常**分别处理。

    * ``HTTPError`` → 返回真实状态码（4xx/5xx 仍由调用方按业务语义判定）；
    * ``URLError`` / ``TimeoutError`` / ``OSError``（含 ``RemoteDisconnected``、
      ``ConnectionResetError``、``BrokenPipeError``）→ 规范化为 ``Refused``。
      写操作（POST/PATCH/PUT/DELETE）的连接中断**不能**被当作"远端没有写入"：
      远端可能已经接受 mutation，调用方必须按"可能已写入"处理。
    """
    req = urllib.request.Request(
        f"{client.api}{path}", data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {client.token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                 "User-Agent": "route-a-run-status"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        hint = ("；该请求为写操作，远端可能已经接受写入" if method in ("POST", "PATCH", "PUT", "DELETE")
                else "")
        raise Refused(f"GitHub API {method} {path} 传输失败{hint}: "
                      f"{type(exc).__name__}: {exc}") from exc


def verify_run(client: Client, run_id: str, head_sha: str, required: set[str],
               pr: int | None = None) -> dict:
    status, run = client.call(f"/repos/{client.repo}/actions/runs/{run_id}")
    if status != 200 or not isinstance(run, dict):
        raise Refused(f"读取 workflow run 失败: HTTP {status}")
    if (run.get("repository") or {}).get("full_name") != client.repo:
        raise Refused("run 不属于本仓库")
    if (run.get("head_repository") or {}).get("full_name") != client.repo:
        raise Refused("run 的 head_repository 不属于本仓库（可能来自 fork）")
    if run.get("path") != EXPECTED_RUN_PATH:
        raise Refused(f"run path 为 {run.get('path')!r}，非 {EXPECTED_RUN_PATH!r}"
                      f"（同名 workflow 不能冒充）")
    prs = run.get("pull_requests")
    if isinstance(prs, list) and prs:
        numbers = {p.get("number") for p in prs if isinstance(p, dict)}
        if pr is not None and pr not in numbers:
            raise Refused(f"run 关联的 PR {sorted(numbers)} 不包含目标 PR #{pr}")
    if run.get("event") != "pull_request":
        raise Refused(f"run 事件为 {run.get('event')!r}，非 pull_request（拒绝采信）")
    if run.get("name") != EXPECTED_RUN_NAME:
        raise Refused(f"run 名称为 {run.get('name')!r}，非 {EXPECTED_RUN_NAME!r}")
    if run.get("head_sha") != head_sha:
        raise Refused("run head_sha 与目标 SHA 不一致")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise Refused(f"run 状态为 {run.get('status')}/{run.get('conclusion')}，非 completed/success")
    status, jobs = client.call(f"/repos/{client.repo}/actions/runs/{run_id}/jobs?per_page=100")
    if status != 200 or not isinstance(jobs, dict):
        raise Refused(f"读取 run jobs 失败: HTTP {status}")
    names, bad = set(), []
    for job in jobs.get("jobs") or []:
        name, concl = job.get("name"), job.get("conclusion")
        names.add(name)
        if concl != "success":
            bad.append(f"{name}={concl}")
    missing = required - names
    if missing:
        raise Refused(f"该 run 未包含必需检查 job：{sorted(missing)}")
    if bad:
        raise Refused(f"存在未成功/被跳过的 job：{bad}")
    return {"run_id": run_id, "head_sha": head_sha, "jobs": sorted(names)}


def resolve_pr(client: Client, head_sha: str, base_branch: str = "main") -> int:
    """由 head SHA 反查 PR（workflow_run 事件不携带 PR 编号）。必须唯一命中。"""
    status, items = client.call(f"/repos/{client.repo}/commits/{head_sha}/pulls")
    if status != 200 or not isinstance(items, list):
        raise Refused(f"反查 PR 失败: HTTP {status}")
    cands = [p for p in items if p.get("state") == "open"
             and (p.get("base") or {}).get("ref") == base_branch]
    if len(cands) != 1:
        raise Refused(f"head {head_sha[:12]} 对应 {len(cands)} 个 open PR（要求恰好 1 个）")
    return int(cands[0]["number"])


def verify_pr(client: Client, pr: int, head_sha: str, base_branch: str = "main") -> dict:
    status, pr_obj = client.call(f"/repos/{client.repo}/pulls/{pr}")
    if status != 200 or not isinstance(pr_obj, dict):
        raise Refused(f"读取 PR 失败: HTTP {status}")
    if pr_obj.get("state") != "open":
        raise Refused(f"PR 状态为 {pr_obj.get('state')!r}，非 open")
    if (pr_obj.get("base") or {}).get("ref") != base_branch:
        raise Refused("PR base 分支不是 main")
    if (pr_obj.get("head") or {}).get("sha") != head_sha:
        raise Refused("PR head SHA 与目标 SHA 不一致（head 漂移）")
    status, ref = client.call(f"/repos/{client.repo}/git/ref/heads/{base_branch}")
    if status != 200 or not isinstance(ref, dict):
        raise Refused(f"读取 {base_branch} 失败: HTTP {status}")
    base_sha = (ref.get("object") or {}).get("sha")
    if (pr_obj.get("base") or {}).get("sha") != base_sha:
        raise Refused("PR base SHA 与当前基线不一致（需要 rebase）")
    return {"pr": pr, "base_sha": base_sha, "author": (pr_obj.get("user") or {}).get("login")}


def publish(client: Client, sha: str, run_id: str, description: str, target_url: str) -> dict:
    status, body = client.post(f"/repos/{client.repo}/statuses/{sha}", {
        "state": "success", "context": CONTEXT, "description": description[:140],
        "target_url": target_url})
    if status not in (200, 201):
        raise Refused(f"发布状态失败: HTTP {status} {body}")
    return {"posted": True, "sha": sha, "context": CONTEXT}


def _creator_problem(obj: dict) -> str:
    """发布者身份必须 login + type 双向成立，且不能只看 context 字符串。"""
    creator = obj.get("creator")
    if not isinstance(creator, dict):
        return "status 缺少 creator 对象（来源不可验证）"
    login, utype = creator.get("login"), creator.get("type")
    if login != TRUSTED_BOT:
        return f"status creator.login={login!r} 不在可信名单（{TRUSTED_BOT}）"
    if utype != TRUSTED_CREATOR_TYPE:
        return f"status creator.type={utype!r} 非 {TRUSTED_CREATOR_TYPE}（拒绝仅凭 login 采信）"
    return ""


# ───────────────────────── Check Run（可证明 App 身份）─────────────────────────

def create_check_run(client: Client, sha: str, run_id: str, target_url: str = "") -> dict:
    """创建 **in_progress** Check Run（``checks: write``）。

    关键：创建时**不带 conclusion**。``in_progress`` 自身不是 success 结论，不会被
    Ruleset 采信——但**这不等于同 SHA 上不存在此前发布的 success status**（历史 success
    由事务步骤 0 单独探查并撤销，见 ``_run_transaction``）。
    """
    body = {
        "name": CONTEXT,
        "head_sha": sha,
        "status": "in_progress",
        "external_id": f"{EXTERNAL_ID_PREFIX}{run_id}",
        "details_url": target_url or f"https://github.com/{client.repo}/actions/runs/{run_id}",
        "output": {"title": "Route A 受信校验进行中",
                   "summary": (f"workflow run {run_id} 已核验为 pull_request/completed/success，"
                               f"三个必需 job 全部 success；等待发布后一致性复核。")},
    }
    status, payload = client.post(f"/repos/{client.repo}/check-runs", body)
    if status not in (200, 201) or not isinstance(payload, dict):
        raise Refused(f"创建 check run 失败: HTTP {status} {str(payload)[:200]}")
    return payload


def finalize_check_run(client: Client, check_run_id: int, conclusion: str,
                       summary: str = "") -> dict:
    """把 Check Run 更新为 completed/<conclusion>。

    ``conclusion`` 只允许 success 或失败态（failure/cancelled/neutral）——后者用于
    **补偿写入**（把已完成的成功降级）。补偿写入本身也可能失败，调用方必须处理。
    """
    if conclusion not in ("success", "failure", "cancelled", "neutral"):
        raise Refused(f"非法 conclusion={conclusion!r}")
    body = {"status": "completed", "conclusion": conclusion,
            "output": {"title": ("Route A 受信校验通过" if conclusion == "success"
                                 else "Route A 受信校验未完成（已降级）"),
                       "summary": summary or ("run 级核验与发布后复核全部通过。"
                                              if conclusion == "success"
                                              else "发布事务未完成；本通道的撤销以回读确认为准。")}}
    status, payload = client.patch(f"/repos/{client.repo}/check-runs/{check_run_id}", body)
    if status not in (200, 201) or not isinstance(payload, dict):
        raise Refused(f"更新 check run 失败: HTTP {status} {str(payload)[:200]}")
    return payload


def list_check_runs(client: Client, sha: str) -> list[dict]:
    status, doc = client.call(f"/repos/{client.repo}/commits/{sha}/check-runs?per_page=100")
    if status != 200 or not isinstance(doc, dict):
        raise Refused(f"读取 check-runs 失败: HTTP {status}")
    return [c for c in doc.get("check_runs") or []
            if isinstance(c, dict) and c.get("name") == CONTEXT]


def verify_check_run(client: Client, sha: str, run_id: str, *, attempts: int = 5,
                     delay: float = 1.0, expect_conclusion: str = "success",
                     check_run_id: int | None = None) -> dict:
    """回读并校验 Check Run：``app.id``、head SHA、结论、``external_id`` 与 run 绑定。

    这是 R-1 的关键证据：``app.id`` 直接来自 GitHub 返回对象，不是推断。
    ``check_run_id`` 给定时，只有该 id 的对象被采信（避免旧 run 的证据被冒充）。
    """
    expected_external = f"{EXTERNAL_ID_PREFIX}{run_id}"
    last, last_problem = None, ""
    for i in range(attempts):
        ours = list_check_runs(client, sha)
        if check_run_id is not None:
            ours = [c for c in ours if c.get("id") == check_run_id]
        if not ours:
            last_problem = "回读未见该 check run"
            time.sleep(delay * (i + 1))
            continue
        candidate = ours[0]
        last = candidate
        app = candidate.get("app") or {}
        if app.get("id") != EXPECTED_APP_ID:
            raise Refused(f"check run 来源 app.id={app.get('id')!r}，非 {EXPECTED_APP_ID}"
                          f"（拒绝仅凭名称采信）")
        if candidate.get("head_sha") != sha:
            raise Refused("check run head_sha 与目标 SHA 不一致")
        if candidate.get("external_id") != expected_external:
            raise Refused(f"check run 未绑定到 run {run_id}"
                          f"（external_id={candidate.get('external_id')!r}）")
        if expect_conclusion == "in_progress":
            if candidate.get("status") != "in_progress":
                raise Refused(f"check run 状态为 {candidate.get('status')!r}，非 in_progress")
        elif candidate.get("status") != "completed" or candidate.get("conclusion") != expect_conclusion:
            raise Refused(f"check run 状态为 {candidate.get('status')}/{candidate.get('conclusion')}，"
                          f"非 completed/{expect_conclusion}")
        return {"verified": True, "app_id": app.get("id"), "app_slug": app.get("slug"),
                "head_sha": candidate.get("head_sha"), "conclusion": candidate.get("conclusion"),
                "status": candidate.get("status"), "external_id": candidate.get("external_id"),
                "details_url": candidate.get("details_url"), "id": candidate.get("id")}
    raise Refused(f"回读未看到 {CONTEXT} check run（{last_problem}；当前 {last!r}）")


def verify_published(client: Client, sha: str, *, attempts: int = 5, delay: float = 1.0,
                     expect_run_id: str = "") -> dict:
    last = None
    last_problem = ""
    for i in range(attempts):
        status, items = client.call(f"/repos/{client.repo}/commits/{sha}/statuses?per_page=100")
        if status == 200 and isinstance(items, list):
            ours = [s for s in items if s.get("context") == CONTEXT]
            if ours:
                last = ours[0]
                problem = _creator_problem(last)
                target = str(last.get("target_url") or "")
                if not problem and expect_run_id:
                    err = parse_run_url(target)
                    if err is None:
                        problem = f"status target_url 不可解析为合法 run URL: {target!r}"
                    elif err.get("run_id") != str(expect_run_id):
                        problem = (f"status target_url run_id={err.get('run_id')}"
                                   f" 非 {expect_run_id}（拒绝模糊匹配）")
                if not problem and last.get("state") == "success":
                    creator = last.get("creator") or {}
                    return {"verified": True, "creator": creator.get("login"),
                            "creator_type": creator.get("type"), "state": last["state"],
                            "target_url": target}
                last_problem = problem or f"state={last.get('state')!r}"
        time.sleep(delay * (i + 1))
    raise Refused(f"发布后回读未看到可信 {CONTEXT}=success（当前 {last!r}；{last_problem}）")


def _downgrade(client: Client, check_run_id: int, reason: str, *, head_sha: str = "",
               run_id: str = "", attempts: int = 5) -> dict:
    """补偿写入：把已创建/已完成的 Check Run 降级为 failure，并做**对象级回读证明**。

    只有下列条件**全部**成立才返回 ``ok=True``：

    1. PATCH 返回 2xx；
    2. PATCH 返回对象的 ``id`` == 被补偿的 ``check_run_id``、``status == "completed"``、
       ``conclusion == "failure"``；
    3. 远端重新 GET 回读（``verify_check_run(expect_conclusion="failure",
       check_run_id=...)``）确认**同一个** Check Run 仍为 completed/failure，且
       ``app.id == 15368``、``head_sha`` 等于目标 SHA、``external_id == route-a-run:<run_id>``。

    仅凭 PATCH 2xx 不算补偿成功；回读失败/超时/断连/不可见/被并发覆盖/字段不匹配
    一律 ``ok=False`` 并记录具体原因。
    """
    out: dict = {"attempted": True, "id": check_run_id, "http": None, "patch_object": None,
                 "readback": None, "ok": False, "error": ""}
    try:
        code, obj = client.patch(f"/repos/{client.repo}/check-runs/{check_run_id}",
                                 {"status": "completed", "conclusion": "failure",
                                  "output": {"title": "Route A 受信校验未完成（已降级）",
                                             "summary": reason or
                                             "发布事务未完成；本通道的撤销以回读确认为准。"}})
        out["http"] = code
        if code not in (200, 201) or not isinstance(obj, dict):
            out["error"] = f"补偿 PATCH 失败或响应畸形：HTTP {code} {str(obj)[:120]}"
            return out
        out["patch_object"] = {"id": obj.get("id"), "status": obj.get("status"),
                               "conclusion": obj.get("conclusion"),
                               "head_sha": obj.get("head_sha"),
                               "external_id": obj.get("external_id")}
        if obj.get("id") != check_run_id:
            out["error"] = f"补偿 PATCH 返回的 id={obj.get('id')!r} 非目标 {check_run_id!r}"
            return out
        if obj.get("status") != "completed":
            out["error"] = f"补偿 PATCH 返回 status={obj.get('status')!r}，非 completed"
            return out
        if obj.get("conclusion") != "failure":
            out["error"] = f"补偿 PATCH 返回 conclusion={obj.get('conclusion')!r}，非 failure"
            return out
    except Exception as exc:                      # noqa: BLE001 — 补偿失败必须如实记录
        out["error"] = f"补偿 PATCH 异常：{type(exc).__name__}: {exc}"
        return out

    if not head_sha or not run_id:
        out["error"] = "缺少 head_sha/run_id，无法完成对象级回读证明"
        return out
    try:
        readback = verify_check_run(client, head_sha, run_id, attempts=attempts,
                                    expect_conclusion="failure", check_run_id=check_run_id)
        out["readback"] = {"id": readback.get("id"), "app_id": readback.get("app_id"),
                           "status": readback.get("status"),
                           "conclusion": readback.get("conclusion"),
                           "head_sha": readback.get("head_sha"),
                           "external_id": readback.get("external_id")}
        out["ok"] = True
    except Exception as exc:                      # noqa: BLE001
        out["error"] = f"补偿回读未确认 failure：{type(exc).__name__}: {exc}"
    return out


def compensate_status_failure(client: Client, sha: str, run_id: str, reason: str,
                             target_url: str = "") -> dict:
    """**尽力**撤销同 SHA、同 context 的 success status：发布 failure 并回读确认。

    语义边界（不得夸大）：
      * 只在 context ``route-a/verify`` 上发 ``failure``；
      * 回读必须**先按 context 过滤**（同一 SHA 上可能有其它 context 的更新状态），
        再确认同 context 的**最新**一条是 failure，且其 ``id`` 就是本次补偿 POST
        返回的对象（防止并发/后续覆盖或拿历史 failure 冒充）；
      * 回读失败/状态不符/绑定不符/对象不符 ⇒ ``ok=False``，**不吞掉**；
      * 这是尽力恢复，**不是原子回滚**。
    """
    out: dict = {"attempted": True, "http": None, "posted_id": None, "readback": None,
                 "ok": False, "error": ""}
    url = target_url or f"https://github.com/{client.repo}/actions/runs/{run_id}"
    try:
        code, posted = client.post(f"/repos/{client.repo}/statuses/{sha}", {
            "state": "failure", "context": CONTEXT,
            "description": f"Route A 发布事务失败，已尝试撤销：{reason}"[:140], "target_url": url})
        out["http"] = code
        if code not in (200, 201) or not isinstance(posted, dict):
            out["error"] = f"补偿 POST 失败或响应畸形：HTTP {code} {str(posted)[:120]}"
            return out
        posted_id = posted.get("id")
        if not posted_id:
            out["error"] = "补偿 POST 响应缺少 status id（无法与回读对象绑定）"
            return out
        out["posted_id"] = posted_id
    except Refused as exc:
        out["error"] = f"补偿 POST 传输异常（远端可能已写入）：{exc}"
        return out
    try:
        code, items = client.call(f"/repos/{client.repo}/commits/{sha}/statuses?per_page=100")
        if code != 200 or not isinstance(items, list):
            out["error"] = f"补偿回读 HTTP {code}"
            return out
        # 关键：先按 context 过滤——items[0] 可能是其它 context 的更新状态
        matching = [i for i in items if isinstance(i, dict) and i.get("context") == CONTEXT]
        newest = matching[0] if matching else None
        if newest is None:
            out["error"] = "回读没有找到 route-a/verify 的有效状态"
            return out
        out["readback"] = {"id": newest.get("id"), "state": newest.get("state"),
                           "context": newest.get("context"), "target_url": newest.get("target_url"),
                           "created_at": newest.get("created_at")}
        problem = _creator_problem(newest)
        if not problem and newest.get("state") != "failure":
            problem = f"同 context 最新 state={newest.get('state')!r}，非 failure"
        if not problem:
            parsed = parse_run_url(str(newest.get("target_url") or ""))
            if parsed is None or parsed.get("run_id") != str(run_id):
                problem = f"回读 target_url 未绑定 run {run_id}（{(newest.get('target_url') or '')!r}）"
        if not problem and newest.get("id") != posted_id:
            problem = (f"同 context 最新状态的 id={newest.get('id')!r} 并非本次补偿 POST 对象"
                       f"（{posted_id!r}）；可能被并发/后续状态覆盖")
        if problem:
            out["error"] = problem
            return out
        out["ok"] = True
    except Refused as exc:
        out["error"] = f"补偿回读传输异常：{exc}"
    return out


def inspect_route_a_status(client: Client, sha: str) -> dict:
    """只读探查同 SHA 上 ``route-a/verify`` 的**当前有效状态**（同 context 最新一条）。

    用于回答一个关键问题：**本轮之前是否已经存在可满足门禁的 success 证据**。
    读不到就返回 ``readable=False``（证据不确定），绝不推断为"没有"。
    """
    try:
        code, items = client.call(f"/repos/{client.repo}/commits/{sha}/statuses?per_page=100")
    except Exception as exc:                      # noqa: BLE001 — 读不到 = 不确定
        return {"readable": False, "error": f"{type(exc).__name__}: {exc}"}
    if code != 200 or not isinstance(items, list):
        return {"readable": False, "error": f"HTTP {code}"}
    matching = [i for i in items if isinstance(i, dict) and i.get("context") == CONTEXT]
    latest = matching[0] if matching else None
    if latest is None:
        return {"readable": True, "observed": False, "latest": None}
    creator = latest.get("creator") or {}
    return {"readable": True, "observed": latest.get("state") == "success",
            "latest": {"id": latest.get("id"), "state": latest.get("state"),
                       "target_url": latest.get("target_url"),
                       "creator": creator.get("login"), "creator_type": creator.get("type")}}


def _compensate_all(client: Client, report: dict, reason: str, target_url: str = "") -> dict:
    """统一失败补偿：status（若已尝试发布 success）+ Check Run（若已创建/完成）。

    设计要点：
      * **逐路独立**：一路失败不影响另一路（两条都尽力尝试）；
      * **不重复覆盖更好结果**：某一路已确认撤销（``ok=True``）则跳过，其余路径的
        尝试与结果记入 ``compensation_log``，不丢失原始更准确的信息；
      * **异常安全**：补偿自身抛出任何异常（含 ``OSError``/``Refused``）都被记录为
        ``ok=False`` + ``error``，绝不冒泡覆盖原始失败原因，也绝不返回成功。
    """
    log = report.setdefault("compensation_log", [])
    check_comp = report.get("compensation")
    status_comp = report.get("status_compensation")

    cr_evidence = report.get("check_run") or report.get("check_run_created")
    if cr_evidence and not (check_comp or {}).get("ok"):
        try:
            check_comp = _downgrade(client, cr_evidence["id"], reason,
                                    head_sha=report["run"]["head_sha"],
                                    run_id=report["run"]["run_id"])
        except Exception as exc:                       # noqa: BLE001 — 补偿失败必须如实记录
            check_comp = {"attempted": True, "ok": False, "id": cr_evidence.get("id"),
                          "error": f"{type(exc).__name__}: {exc}"}
        report["compensation"] = check_comp
        log.append({"channel": "check_run", "reason": reason, "result": check_comp})

    if report.get("status_publish_attempted") and not (status_comp or {}).get("ok"):
        try:
            status_comp = compensate_status_failure(client, report["run"]["head_sha"],
                                                    report["run"]["run_id"], reason, target_url)
        except Exception as exc:                       # noqa: BLE001
            status_comp = {"attempted": True, "ok": False,
                           "error": f"{type(exc).__name__}: {exc}"}
        report["status_compensation"] = status_comp
        log.append({"channel": "status", "reason": reason, "result": status_comp})

    report["compensations"] = {"status": status_comp, "check_run": check_comp}
    status_risky = bool(report.get("status_publish_attempted")) and not (status_comp or {}).get("ok")
    # 只有"该 Check Run 可能已经完成 success"时，未确认撤销才算证据残留风险；
    # 若它从未进入 success（例如停在 in_progress），要如实区分"无 success 观察"与"风险推断"。
    check_may_have_succeeded = bool((report.get("check_run_created") or {}).get("may_have_succeeded")
                                    or report.get("check_run"))
    check_risky = bool(cr_evidence) and check_may_have_succeeded and not (check_comp or {}).get("ok")
    report["check_run_may_have_succeeded"] = check_may_have_succeeded
    report["check_run_compensation_unconfirmed_without_success_observed"] = bool(
        cr_evidence and not check_may_have_succeeded and not (check_comp or {}).get("ok"))
    # 同 SHA 历史 success：只有被本次操作**确认撤销**才不算风险
    hist_risky = (bool(report.get("historical_success_observed"))
                  and report.get("historical_success_revoked") is not True)
    if hist_risky and not (report.get("historical_success_revocation") or {}).get("ok"):
        report["historical_success_risk"] = True
    report["possible_success_evidence_remains"] = bool(status_risky or check_risky or hist_risky)
    report["compensation_is_best_effort_not_atomic"] = True
    report["operator_action"] = ("需要人工只读核验远端是否仍有 route-a/verify=success"
                                 if report["possible_success_evidence_remains"] else "无需人工核验")
    return report


def publish_for_run(*, repo: str, run_id: str, pr: int, head_sha: str, required: set[str],
                    token: str, api: str = "https://api.github.com", target_url: str = "",
                    attempts: int = 5, publish_mode: str = "status") -> dict:
    """核验后发布 ``route-a/verify``（发布事务，路线 A 为权威）。

    ``publish_mode``：
      * ``check-run`` —— 仅 Check Run（**权威通道**：``app.id`` 对象级来源证明）；
      * ``dual``      —— Check Run 权威 + commit status 兼容回退；
      * ``status``    —— 仅 commit status（历史行为；无 ``app.id``，仅供兼容/回归）。

    路线 A 事务顺序（关键：成功证据最后一步才写）::

        1. verify_run()  真实 pull_request run + 仓库/path/event/PR 归属/head/conclusion/三 job
        2. verify_pr()   PR open、base=main、head 精确、base SHA == main tip
        3. create_check_run(in_progress)       ← 不可被 required context 采信
        4. verify_check_run(expect in_progress) ← app.id/head_sha/external_id 绑定
        5. verify_pr()   **创建后**再复核一次（防 head/base 漂移）
        6. finalize_check_run(success)          ← 第一个可被采信的成功证据
        7. verify_check_run(expect success)     ← 回读确认（失败 → 补偿降级）
        8. publish(status) + verify_published() ← 兼容回退，且**仅在 7 成功之后**
        9. verify_pr()   收尾复核

    步骤 0 先只读探查同 SHA 上 ``route-a/verify`` 的当前有效状态：
      * 读不到 ⇒ ``status_evidence_unknown=True``，直接停止（证据不确定，不发布任何成功证据）；
      * 观察到历史 success ⇒ 先用同一套严格机制撤销并**回读确认**；确认不了就停止事务，
        绝不发布新的成功证据；确认成功才继续，并记录 ``historical_success_revoked=True``。

    任一步失败：若 6/7 已发生，先补偿降级（并如实报告是否成功），再 ``Refused``。
    **status 绝不在 check run 证明之前发布**。

    注意（有界表述）：本函数只在**相应通道完成严格对象级回读确认**后才宣称该通道的
    success 已撤销；"本轮未尝试发布 success"**不等于**"远端没有 success 证据"——
    同 SHA 的历史 success 由步骤 0 单独处理并在 ``historical_*`` 字段中如实报告。

    status 一旦**开始**发布（``status_publish_attempted=True``，含 POST 超时/连接中断），
    其后任何异常都会进入统一补偿：① 同 SHA、同 context 发 failure status 并回读确认；
    ② 把本次 Check Run 降级为 failure；③ 两路结果都写进 ``report``。

    **语义边界（不得夸大）**：GitHub API 写入**没有原子事务语义**，补偿是尽力恢复。
    任一路未确认撤销 ⇒ ``possible_success_evidence_remains=True``，需要人工只读核验；
    本函数**不保证**在任何网络/API 故障下都 fail-closed。
    """
    if publish_mode not in PUBLISH_MODES:
        raise Refused(f"未知 publish_mode={publish_mode!r}（可选 {PUBLISH_MODES}）")
    client = Client(repo, token, api)
    if not pr:
        pr = resolve_pr(client, head_sha)
    run_info = verify_run(client, run_id, head_sha, required, pr=pr)
    pr_info = verify_pr(client, pr, head_sha)
    report: dict = {"ok": True, "run": run_info, "pr": pr_info, "publish_mode": publish_mode,
                    "steps": [], "compensation": None, "status_compensation": None,
                    "status_publish_attempted": False,
                    "status_may_have_been_published": False,
                    "historical_success_observed": False,
                    "historical_success_revoked": None,
                    "status_evidence_unknown": False,
                    "possible_success_evidence_remains": False}

    try:
        _run_transaction(client, report, run_id=run_id, pr=pr, head_sha=head_sha,
                         target_url=target_url, attempts=attempts, publish_mode=publish_mode)
    except Exception as exc:                     # noqa: BLE001 — 统一收敛，任何异常都不得绕过补偿
        report["ok"] = False
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        # 已创建 Check Run 或已尝试发布 success status ⇒ 必须确认补偿是否已执行/完成；
        # 局部已确认撤销的通道不会被重复覆盖（见 _compensate_all）。
        if (report.get("check_run") or report.get("check_run_created")
                or report.get("status_publish_attempted")
                or report.get("historical_success_observed")):
            _compensate_all(client, report, f"事务异常 {type(exc).__name__}: {exc}", target_url)
        if isinstance(exc, Refused):
            exc.report = report
            raise
        refused = Refused(f"Route A 事务失败：{type(exc).__name__}: {exc}")
        refused.report = report
        raise refused from exc
    return report


def _run_transaction(client: Client, report: dict, *, run_id: str, pr: int, head_sha: str,
                     target_url: str, attempts: int, publish_mode: str) -> None:
    """路线 A 事务主体。任何失败都抛 ``Refused``；调用方负责把 ``report`` 附到异常上。"""
    # ── 步骤 0：同 SHA 历史 success 探查与撤销（在写入任何成功证据之前） ──
    hist = inspect_route_a_status(client, head_sha)
    report["historical_status_probe"] = hist
    if not hist.get("readable"):
        report["status_evidence_unknown"] = True
        report["possible_success_evidence_remains"] = True
        report["operator_action"] = "状态不可读：需要人工只读核验远端 route-a/verify 状态"
        raise Refused(f"无法读取同 SHA 的 route-a/verify 状态（证据不确定）：{hist.get('error')}")
    report["historical_success_observed"] = bool(hist.get("observed"))
    report["historical_success_revoked"] = None
    if report["historical_success_observed"]:
        rev = compensate_status_failure(client, head_sha, run_id,
                                        "启动前撤销同 SHA 历史 success", target_url)
        report["historical_success_revocation"] = rev
        report["historical_success_revoked"] = bool(rev.get("ok"))
        if not rev.get("ok"):
            report["possible_success_evidence_remains"] = True
            report["operator_action"] = "需要人工只读核验远端是否仍有 route-a/verify=success"
            raise Refused(f"未能确认撤销同 SHA 历史 success（不发布任何新的成功证据）："
                          f"{rev.get('error')}")
        report["steps"].append("revoke_historical_success")

    if publish_mode in ("check-run", "dual"):
        created = create_check_run(client, head_sha, run_id, target_url)
        cr_id = created.get("id")
        # 立刻记录 ID 与"是否可能已进入 success"，供任何后续异常的统一补偿使用
        report["check_run_created"] = {"id": cr_id, "may_have_succeeded": False}
        report["steps"].append("create_check_run:in_progress")
        app = created.get("app") or {}
        if app.get("id") != EXPECTED_APP_ID:
            _compensate_all(client, report, f"创建返回 app.id 不符：{app.get('id')!r}", target_url)
            raise Refused(f"创建返回 app.id={app.get('id')!r}，非 {EXPECTED_APP_ID}")
        try:
            verify_check_run(client, head_sha, run_id, attempts=attempts,
                             expect_conclusion="in_progress", check_run_id=cr_id)
        except Refused as exc:
            _compensate_all(client, report, f"in_progress 回读失败：{exc}", target_url)
            raise Refused(f"check run 回读未确认 in_progress：{exc}") from exc
        report["steps"].append("verify_check_run:in_progress")
        pr_info = verify_pr(client, pr, head_sha)          # 创建后复核
        report["steps"].append("verify_pr:post-create")
        report["check_run_created"]["may_have_succeeded"] = True   # 发起前即按"可能已 success"处理
        try:
            finalized = finalize_check_run(client, cr_id, "success")
        except Refused as exc:
            # PATCH 传输异常同样按"远端可能已完成 success"处理
            _compensate_all(client, report, f"完成失败（远端可能已 success）：{exc}", target_url)
            raise Refused(f"完成 check run 为 success 失败：{exc}") from exc
        report["steps"].append("finalize_check_run:success")
        try:
            verified = verify_check_run(client, head_sha, run_id, attempts=attempts,
                                        expect_conclusion="success", check_run_id=cr_id)
        except Refused as exc:
            _compensate_all(client, report, f"回读失败：{exc}", target_url)
            raise Refused(f"check run 回读未确认 success：{exc}") from exc
        report["steps"].append("verify_check_run:success")
        report["check_run"] = {"id": cr_id, "name": finalized.get("name"),
                               "head_sha": finalized.get("head_sha"),
                               "external_id": finalized.get("external_id"),
                               "may_have_succeeded": True, "verify": verified}

    if publish_mode in ("status", "dual"):
        try:
            pr_info = verify_pr(client, pr, head_sha)      # 发布前最后一道基线检查
            report["steps"].append("verify_pr:pre-status")
        except Refused as exc:
            _compensate_all(client, report, f"status 前置复核失败：{exc}", target_url)
            raise
        # 标记：此后任何异常都必须假设远端**可能已接受** success 写入
        report["status_publish_attempted"] = True
        report["status_may_have_been_published"] = True
        try:
            posted = publish(client, head_sha, run_id, "受信校验通过（run 级核验）", target_url)
            verified_status = verify_published(client, head_sha, attempts=attempts,
                                               expect_run_id=run_id)
            report["status"] = {**posted, "verify": verified_status}
            if isinstance(posted, dict):
                report.update(posted)
            report["verify"] = verified_status
            report["steps"].append("publish:status+verify")
        except Refused as exc:
            _compensate_all(client, report, f"status 阶段失败：{exc}", target_url)
            raise
    try:
        report["after"] = verify_pr(client, pr, head_sha)
        report["steps"].append("verify_pr:final")
    except Refused as exc:
        _compensate_all(client, report, f"发布后复核失败：{exc}", target_url)
        raise
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P0-1 受信状态发布（人工 PR）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--required-checks", default=".github/required-checks.json")
    ap.add_argument("--target-url", default="")
    ap.add_argument("--publish-mode", choices=PUBLISH_MODES, default="status",
                    help="check-run（权威，含 app.id 证据）/ dual（权威 + status 兼容回退）/ "
                         "status（历史行为，无 app.id 证据）")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)
    try:
        doc = json.loads(Path(args.required_checks).read_text(encoding="utf-8"))
        required = {c["name"] for c in doc.get("checks") or [] if isinstance(c, dict) and c.get("name")}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ 读取必需检查清单失败：{exc}", file=sys.stderr)
        return 1
    try:
        report = publish_for_run(repo=args.repo, run_id=args.run_id, pr=args.pr, head_sha=args.head_sha,
                                 required=required, token=os.environ.get("GITHUB_TOKEN", ""),
                                 api=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                                 target_url=args.target_url, publish_mode=args.publish_mode)
    except Refused as exc:
        print(f"❌ 拒绝发布：{exc}", file=sys.stderr)
        if args.json:
            detail = getattr(exc, "report", None)
            json.dump({"ok": False, "reason": str(exc), **({"evidence": detail} if detail else {})},
                      args.json, ensure_ascii=False, indent=1)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
