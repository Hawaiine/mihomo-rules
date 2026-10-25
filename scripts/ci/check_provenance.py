#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""必需检查的**来源/身份（provenance）严格验证** —— 共享单一实现。

为什么需要它：仅按 `name + head_sha + app_id` 选取「最新 run」是不够的。
**另一个 workflow 的同名成功检查可以覆盖真实必需检查的失败**。因此自动合并前必须证明：

  1. check run 的 `check_suite.id` 能映射到 head SHA 上的一个 workflow run；
  2. 该 workflow run 的 `path` 精确等于 manifest 声明的 `.github/workflows/pr-verify.yml`；
  3. 该 workflow run 的 `event` 为 `pull_request`；
  4. 该 workflow run 的 `head_sha` 等于当前 PR head SHA；
  5. check run 的 `id` 能映射到该 workflow run 的 **job 记录**（Actions 中 check run id == job id），
     且该 job 的 `name` 等于 manifest 声明的 job 身份；
  6. 该 job 记录的 `id`/`name`/`head_sha`/`status`/`conclusion` **全部存在**且与 check run 一致
     （任一字段缺失/类型异常 ⇒ fail-closed，绝不当作兼容成功路径）；
  7. 该 check run 的 `app.slug`/`app.id` 等于 manifest 声明的来源（GitHub Actions）；
  8. 以上任一项缺失、不一致、无法查询或 API 异常 ⇒ **fail-closed**（禁止自动合并）。

只有在**通过来源认证之后**，才允许在同一预期 workflow/job 内按运行时间选取最新 run；
其它 workflow 的同名 run **不能**参与选择，也不能把已验证的旧失败「超越」成成功。

本模块只做纯判定 + 只读 GET 采集；不做任何写请求。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

REQUIRED_WORKFLOW_PATH = ".github/workflows/pr-verify.yml"
EXPECTED_APP_SLUG = "github-actions"
EXPECTED_APP_ID = 15368
STRICT_CONCLUSION = "success"
ALLOWED_WORKFLOW_EVENT = "pull_request"
DEFAULT_MANIFEST = ".github/required-checks.json"

# 检查状态（对外的判定值）
STATE_SUCCESS = "success"
STATE_NOT_SUCCESS = "not-success"
STATE_IN_PROGRESS = "in-progress"
STATE_UNVERIFIED = "unverified"
STATE_MISSING = "missing"


class ManifestError(Exception):
    """manifest 不可读/结构非法/降级 ⇒ fail-closed。"""


class ApiError(Exception):
    """API 异常（含权限不足、限流、5xx、响应异常）⇒ fail-closed。"""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status} {detail}".strip())
        self.status = status
        self.detail = detail


# --------------------------------------------------------------------- manifest

def load_manifest(path: str) -> list[dict]:
    """严格加载必需检查清单。

    必须为 `{"checks": [{"name","job_id","workflow","source":{"app_slug","app_id"}}, ...]}`。
    字段缺项/为空/重复/**旧宽松形态（required[]）** 一律 fail-closed ——
    生产验证路径不得静默应用较宽松默认值。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"无法读取必需检查清单 {path}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("checks"), list) or not doc["checks"]:
        raise ManifestError(f"{path}: 必须为非空 checks[]（旧形态 required[] 已被拒绝：缺少来源与身份契约）")
    checks: list[dict] = []
    for item in doc["checks"]:
        if not isinstance(item, dict):
            raise ManifestError(f"{path}: 检查项必须是对象（不得为字符串）：{item!r}")
        name = item.get("name")
        job_id = item.get("job_id")
        workflow = item.get("workflow")
        source = item.get("source")
        if not name or not isinstance(name, str):
            raise ManifestError(f"{path}: 检查项缺少 name：{item!r}")
        if not job_id or not isinstance(job_id, str):
            raise ManifestError(f"{path}: {name} 缺少 job_id（无法做 job 身份认证）")
        if workflow != REQUIRED_WORKFLOW_PATH:
            raise ManifestError(f"{path}: {name} 的 workflow 必须精确为 {REQUIRED_WORKFLOW_PATH}（实际 {workflow!r}）")
        if not isinstance(source, dict) or not source.get("app_slug") or source.get("app_id") is None:
            raise ManifestError(f"{path}: {name} 缺少 source（app_slug/app_id）")
        checks.append({"name": name, "job_id": job_id, "workflow": workflow,
                       "app_slug": source["app_slug"], "app_id": source["app_id"]})
    names = [c["name"] for c in checks]
    if len(names) != len(set(names)):
        raise ManifestError(f"{path}: 检查名重复：{names}")
    return checks


# ------------------------------------------------------------------- 只读采集

def http_get_json(url: str, token: str, timeout: int = 30) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "prfirst-provenance"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, {"_error": exc.read()[:400].decode("utf-8", "replace")}


def get_json(*, getter, token: str, api_base: str, path: str, what: str) -> dict:
    status, body = getter(f"{api_base.rstrip('/')}{path}", token)
    if status != 200 or not isinstance(body, dict) or "_error" in body:
        raise ApiError(status, f"{what}: {str(body)[:200]}")
    return body


def collect_check_runs(*, getter, token, api_base, repo, head_sha) -> list[dict]:
    body = get_json(getter=getter, token=token, api_base=api_base,
                    path=f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100",
                    what="GET check-runs")
    return list(body.get("check_runs") or [])


def collect_workflow_runs(*, getter, token, api_base, repo, head_sha) -> list[dict]:
    body = get_json(getter=getter, token=token, api_base=api_base,
                    path=f"/repos/{repo}/actions/runs?head_sha={head_sha}&per_page=100",
                    what="GET actions/runs")
    return list(body.get("workflow_runs") or [])


def collect_jobs(*, getter, token, api_base, repo, run_id) -> list[dict]:
    body = get_json(getter=getter, token=token, api_base=api_base,
                    path=f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
                    what=f"GET actions/runs/{run_id}/jobs")
    return list(body.get("jobs") or [])


def index_by_suite(workflow_runs: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for run in workflow_runs:
        suite = run.get("check_suite_id")
        if suite is None:
            continue
        out.setdefault(int(suite), run)   # 同一 suite 只保留一个 run
    return out


def index_jobs(jobs: list[dict]) -> dict[int, dict]:
    return {int(j["id"]): j for j in jobs if j.get("id") is not None}


def needed_run_ids(check_runs: list[dict], manifest: list[dict], workflow_runs: list[dict]) -> list[int]:
    """只取与必需检查名相关的 workflow run id（避免多余 API 调用）。"""
    names = {c["name"] for c in manifest}
    suite_to_run = {int(r["check_suite_id"]): r["id"] for r in workflow_runs
                    if r.get("check_suite_id") is not None and r.get("id") is not None}
    ids: list[int] = []
    for run in check_runs:
        if run.get("name") not in names:
            continue
        suite = (run.get("check_suite") or {}).get("id")
        if suite is not None and int(suite) in suite_to_run:
            rid = suite_to_run[int(suite)]
            if rid not in ids:
                ids.append(rid)
    return ids


# ------------------------------------------------------------------- 身份认证

def run_pr_numbers(run: dict) -> list[int]:
    """严格读取 workflow run 的 `pull_requests[]` 关联（真实 API 字段）。

    严格口径（整条关联一起判定，不做元素级宽容）：

    * 字段缺失、空列表、非列表 ⇒ ``[]``；
    * 任一元素不是对象 ⇒ ``[]``；
    * 任一元素缺失 `number`、`number` 为布尔/非整数/小于 1 ⇒ ``[]``
      （**不**把 `"35"` 转成整数，**不**把 `True` 当 1）；
    * 只在**全部**元素都可验证时返回编号列表。

    多个有效编号必须**原样保留**（不得只挑其中一个）：由 `run_belongs_to_pr()` 与
    `_newest_verification_run()` 判定为「多关联」并拒绝。

    为什么必须整条失效：若同时存在一条有效关联和一个畸形元素，元素级宽容会丢掉畸形项、
    只返回那一个有效编号，于是上层会把「本就不完整/格式异常的关联信息」当成唯一关联放行。
    """
    raw = run.get("pull_requests")
    if not isinstance(raw, list) or not raw:
        return []
    out: list[int] = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        num = item.get("number")
        if isinstance(num, bool) or not isinstance(num, int) or num < 1:
            return []
        out.append(num)
    return out


def run_belongs_to_pr(run: dict, expected_pr_number: int) -> tuple[bool, str]:
    """证明该 workflow run 确实属于**当前 PR**。

    只比对 name / app / head_sha / workflow path / event 是不够的：同一 head SHA 可以同时
    是两个 PR 的 head（本仓库真实发生过：PR #33 与 PR #35 共用 `b767503a…`），
    两条 run 的 event 都是 `pull_request`、head_sha 也相同，只有 `pull_requests[].number`
    能区分它们。缺字段即拒绝，不做任何推断。
    """
    nums = run_pr_numbers(run)
    if not nums:
        return False, "workflow run 缺少 pull_requests 关联（无法证明属于当前 PR）"
    if len(nums) != 1:
        return False, f"workflow run 关联了多个 PR {sorted(nums)}（拒绝采信）"
    if nums[0] != expected_pr_number:
        return False, (f"workflow run 属于 PR #{nums[0]}，非当前 PR #{expected_pr_number}"
                       f"（同 SHA 的历史运行不得复用）")
    return True, ""


def verify_check_run_provenance(*, check_run: dict, entry: dict, runs_by_suite: dict[int, dict],
                                jobs_by_id: dict[int, dict], expected_head: str,
                                pr_head: str,
                                expected_pr_number: int) -> tuple[bool, list[str], dict]:
    """认证单个 check run 是否真的来自 manifest 声明的 workflow/job 身份。"""
    problems: list[str] = []
    name = entry["name"]
    info: dict = {"check_run_id": check_run.get("id"), "name": check_run.get("name"),
                  "app_slug": (check_run.get("app") or {}).get("slug"),
                  "app_id": (check_run.get("app") or {}).get("id"),
                  "head_sha": check_run.get("head_sha"),
                  "status": check_run.get("status"), "conclusion": check_run.get("conclusion"),
                  "expected_workflow": entry["workflow"], "expected_job_id": entry["job_id"]}

    if check_run.get("name") != name:
        problems.append(f"{name}: check run 名称不符（{check_run.get('name')!r}）")
    app = check_run.get("app") or {}
    if app.get("slug") != entry["app_slug"] or app.get("id") != entry["app_id"]:
        problems.append(f"{name}: 来源 App 不符（实际 {app.get('slug')}/{app.get('id')}，"
                        f"期望 {entry['app_slug']}/{entry['app_id']}）")
    if check_run.get("head_sha") != expected_head or pr_head != expected_head:
        problems.append(f"{name}: head SHA 不符（check={check_run.get('head_sha')} "
                        f"pr={pr_head} 期望={expected_head}）")
    # C1：check run 自身的 status/conclusion 必需字段不得缺失（缺失即 fail-closed）
    if check_run.get("status") is None:
        problems.append(f"{name}: check run 缺少 status（fail-closed，不得当作兼容成功路径）")
    if check_run.get("status") == "completed" and check_run.get("conclusion") is None:
        problems.append(f"{name}: check run status=completed 但缺少 conclusion（fail-closed）")

    suite = (check_run.get("check_suite") or {}).get("id")
    info["check_suite_id"] = suite
    if suite is None:
        problems.append(f"{name}: check run 缺少 check_suite.id（无法映射 workflow run）")
        return False, problems, info
    run = runs_by_suite.get(int(suite))
    if run is None:
        problems.append(f"{name}: check_suite {suite} 无法映射到该 head SHA 上的 workflow run")
        return False, problems, info
    info["workflow_run_id"] = run.get("id")
    info["workflow_run_path"] = run.get("path")
    info["workflow_run_event"] = run.get("event")
    info["workflow_run_head_sha"] = run.get("head_sha")
    if run.get("path") != entry["workflow"]:
        problems.append(f"{name}: workflow run 路径不符（实际 {run.get('path')!r}，"
                        f"期望 {entry['workflow']!r}）——其它 workflow 的同名检查不得采信")
    if run.get("event") != ALLOWED_WORKFLOW_EVENT:
        problems.append(f"{name}: workflow run 事件不是 {ALLOWED_WORKFLOW_EVENT}"
                        f"（实际 {run.get('event')!r}）")
    if run.get("head_sha") != expected_head:
        problems.append(f"{name}: workflow run head SHA 不符（{run.get('head_sha')} != {expected_head}）")
    # R-2：必须再证明该 run 属于**当前 PR**——同 SHA 的另一 PR 的历史成功不得复用。
    belongs, why = run_belongs_to_pr(run, expected_pr_number)
    info["workflow_run_pr_number"] = (run_pr_numbers(run) or [None])[0]
    info["expected_pr_number"] = expected_pr_number
    if not belongs:
        problems.append(f"{name}: {why}")

    job = jobs_by_id.get(int(check_run["id"])) if check_run.get("id") is not None else None
    if job is None:
        problems.append(f"{name}: check run id {check_run.get('id')} 无法映射到该 workflow run 的 job 记录")
        return False, problems, info
    info["job_id"] = job.get("id")
    info["job_name"] = job.get("name")
    info["job_status"] = job.get("status")
    info["job_conclusion"] = job.get("conclusion")
    info["job_head_sha"] = job.get("head_sha")
    # C1：job 记录的**完整身份字段**必须存在且一致；缺失/类型异常一律 fail-closed。
    # 绝不能把「字段缺失」当成可兼容的成功路径。
    if job.get("id") is None:
        problems.append(f"{name}: job 记录缺少 id（fail-closed）")
    elif int(job["id"]) != int(check_run["id"]):
        problems.append(f"{name}: job id 与 check run id 不一致（job={job.get('id')} "
                        f"check={check_run.get('id')}）")
    if not job.get("name"):
        problems.append(f"{name}: job 记录缺少 name（无法完成 job 身份认证，fail-closed）")
    elif job.get("name") != entry["job_id"]:
        problems.append(f"{name}: job 身份不符（实际 job name {job.get('name')!r}，"
                        f"期望 {entry['job_id']!r}）")
    if job.get("head_sha") is None:
        problems.append(f"{name}: job 记录缺少 head_sha（fail-closed）")
    elif job.get("head_sha") != expected_head:
        problems.append(f"{name}: job head SHA 不符（{job.get('head_sha')} != {expected_head}）")
    if job.get("status") is None:
        problems.append(f"{name}: job 记录缺少 status（fail-closed）")
    elif check_run.get("status") is None:
        problems.append(f"{name}: check run 缺少 status，无法与 job 比对（fail-closed）")
    elif job.get("status") != check_run.get("status"):
        problems.append(f"{name}: job 与 check run 状态不一致"
                        f"（job={job.get('status')} check={check_run.get('status')}）")
    if job.get("conclusion") is None:
        if job.get("status") == "completed":
            problems.append(f"{name}: job 记录 status=completed 但缺少 conclusion（fail-closed）")
    elif check_run.get("conclusion") is None:
        problems.append(f"{name}: check run 缺少 conclusion，无法与 job 比对（fail-closed）")
    elif job.get("conclusion") != check_run.get("conclusion"):
        problems.append(f"{name}: job 与 check run 结论不一致"
                        f"（job={job.get('conclusion')} check={check_run.get('conclusion')}）")
    return (not problems), problems, info


def _newest_verification_run(workflow_runs: list[dict], entry: dict,
                             expected_pr_number: int) -> dict | None:
    """该 PR 在预期 workflow 上的**最新** pull_request 运行（按 created_at/attempt/id）。

    用于阻止「同一 PR 已有更新运行、但新运行尚未产出成功检查时，沿用旧运行的 success」。
    """
    candidates = [run for run in workflow_runs
                  if run.get("path") == entry["workflow"]
                  and run.get("event") == ALLOWED_WORKFLOW_EVENT
                  and run_pr_numbers(run) == [expected_pr_number]]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (str(r.get("created_at") or ""),
                                          int(r.get("run_attempt") or 1),
                                          int(r.get("id") or 0)))


def select_checks(*, check_runs: list[dict], manifest: list[dict], workflow_runs: list[dict],
                  jobs_by_id: dict[int, dict], expected_head: str, pr_head: str,
                  expected_pr_number: int) -> tuple[dict, list[str]]:
    """对每个必需检查做身份认证并选出**通过认证**的最新 run。

    返回 (per_check, problems)。per_check[name] = {state, ...}，
    state ∈ {success, not-success, in-progress, unverified, missing}。
    """
    runs_by_suite = index_by_suite(workflow_runs)
    per_check: dict[str, dict] = {}
    problems: list[str] = []
    for entry in manifest:
        name = entry["name"]
        candidates = [r for r in check_runs if r.get("name") == name]
        verified: list[tuple[dict, dict]] = []
        rejections: list[dict] = []
        for cand in candidates:
            ok, why, info = verify_check_run_provenance(
                check_run=cand, entry=entry, runs_by_suite=runs_by_suite, jobs_by_id=jobs_by_id,
                expected_head=expected_head, pr_head=pr_head,
                expected_pr_number=expected_pr_number)
            (verified.append((cand, info)) if ok else rejections.append({**info, "problems": why}))
        info_out: dict = {"found": bool(candidates), "verified": len(verified),
                          "rejected": rejections,
                          "expected_source": {"app_slug": entry["app_slug"], "app_id": entry["app_id"]},
                          "expected_workflow": entry["workflow"], "expected_job_id": entry["job_id"]}
        if not verified:
            if any(c.get("status") is None for c in candidates):
                # C1：status 字段缺失 = 响应不完整，绝不能降级成「还在跑」这种可等待状态
                info_out["state"] = STATE_UNVERIFIED
                info_out["detail"] = "同名检查缺少 status 字段：响应不完整，拒绝采信（fail-closed）"
                problems.append(f"{name}: {info_out['detail']}")
            elif any(c.get("status") != "completed" for c in candidates):
                info_out["state"] = STATE_IN_PROGRESS
                info_out["detail"] = "同名检查尚未完成，来源认证待其完成后判定"
                problems.append(f"{name}: {info_out['detail']}"
                                f"（status={[c.get('status') for c in candidates]}）")
            elif candidates:
                info_out["state"] = STATE_UNVERIFIED
                info_out["detail"] = "存在同名检查但**来源/身份认证失败**：拒绝采信（fail-closed）"
                problems.append(f"{name}: {info_out['detail']}；" + "；".join(
                    p for r in rejections for p in r["problems"])[:400])
            else:
                info_out["state"] = STATE_MISSING
                info_out["detail"] = "该 head SHA 上未找到任何同名检查"
                problems.append(f"{name}: {info_out['detail']}")
            per_check[name] = info_out
            continue
        latest, latest_info = sorted(verified, key=lambda t: (t[0].get("started_at") or "",
                                                              t[0].get("id") or 0))[-1]
        # R-2b：即使来源通过认证，也必须是该 PR **最新**的一次验证运行；否则拒绝。
        newest = _newest_verification_run(workflow_runs, entry, expected_pr_number)
        info_out["newest_pr_run_id"] = newest.get("id") if newest else None
        if newest is None:
            info_out["state"] = STATE_UNVERIFIED
            info_out["detail"] = ("该 PR 没有任何可识别的 pull_request 验证运行"
                                 "（无法证明检查属于当前事件）")
            problems.append(f"{name}: {info_out['detail']}")
            per_check[name] = info_out
            continue
        if int(newest.get("id") or 0) != int(latest_info.get("workflow_run_id") or 0):
            info_out["state"] = STATE_UNVERIFIED
            info_out["detail"] = (f"当前 PR 已有更新的验证运行 {newest.get('id')}，"
                                 f"不得沿用旧运行 {latest_info.get('workflow_run_id')} 的 "
                                 f"{latest.get('conclusion')!r} 检查")
            problems.append(f"{name}: {info_out['detail']}")
            per_check[name] = info_out
            continue
        # 只有**通过来源认证**的 run 才能被记录为 superseded（其它 workflow 的同名 run 不得参与）
        superseded = [{"check_run_id": c.get("id"), "conclusion": c.get("conclusion"),
                       "started_at": c.get("started_at"), "workflow_run_id": i.get("workflow_run_id"),
                       "job_id": i.get("job_id")} for c, i in verified if c is not latest]
        info_out.update({"check_run_id": latest.get("id"), "status": latest.get("status"),
                         "conclusion": latest.get("conclusion"),
                         "workflow_run_id": latest_info.get("workflow_run_id"),
                         "workflow_run_pr_number": latest_info.get("workflow_run_pr_number"),
                         "job_id": latest_info.get("job_id"), "job_name": latest_info.get("job_name"),
                         "details_url": latest.get("details_url"),
                         "superseded_verified_runs": superseded})
        if latest.get("status") is None:
            info_out["state"] = STATE_UNVERIFIED
            info_out["detail"] = "check run 缺少 status（fail-closed）"
            problems.append(f"{name}: {info_out['detail']}")
        elif latest.get("status") != "completed":
            info_out["state"] = STATE_IN_PROGRESS
        elif latest.get("conclusion") == STRICT_CONCLUSION:
            info_out["state"] = STATE_SUCCESS
        else:
            info_out["state"] = STATE_NOT_SUCCESS
            problems.append(f"{name}: 结论为 {latest.get('conclusion')!r}，"
                            f"严格口径仅接受 {STRICT_CONCLUSION!r}")
        per_check[name] = info_out
    return per_check, problems


def overall_state(per_check: dict) -> str:
    """把逐项状态汇总为 success / failed / pending。"""
    states = {v["state"] for v in per_check.values()}
    if states & {STATE_NOT_SUCCESS, STATE_UNVERIFIED}:
        return "failed"
    if states & {STATE_IN_PROGRESS, STATE_MISSING}:
        return "pending"
    if states == {STATE_SUCCESS} and per_check:
        return "success"
    return "failed" if not per_check else "pending"
