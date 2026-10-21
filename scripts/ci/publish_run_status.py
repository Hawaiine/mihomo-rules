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
        req = urllib.request.Request(
            f"{self.api}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "route-a-run-status"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:200]
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Refused(f"GitHub API 不可用: {exc}") from exc

    def patch(self, path: str, body: dict) -> tuple[int, object]:
        return _request(self, "PATCH", path, body)

    def post(self, path: str, body: dict) -> tuple[int, object]:
        req = urllib.request.Request(
            f"{self.api}{path}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "route-a-run-status"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:200]
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Refused(f"状态发布请求失败: {exc}") from exc


def _request(client: Client, method: str, path: str, body: dict | None = None):
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
    except (urllib.error.URLError, TimeoutError) as exc:
        raise Refused(f"GitHub API 不可用: {exc}") from exc


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

    关键：创建时**不带 conclusion**。``in_progress`` 不满足 required status check，
    因此这一阶段无论失败还是中断，都不会留下可被 Ruleset 采信的成功证据。
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
                                              else "发布事务未完成，成功证据已被撤销。")}}
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


def _downgrade(client: Client, check_run_id: int, reason: str) -> dict:
    """补偿写入：把已创建/已完成的 Check Run 降级为 failure。

    **不假定补偿一定成功**：失败会被如实记录到返回结构里（``compensation.ok=False``），
    调用方据此区分"已撤销"与"可能残留 success"两种证据状态。
    """
    try:
        obj = finalize_check_run(client, check_run_id, "failure", summary=reason)
        return {"ok": True, "id": obj.get("id"), "conclusion": obj.get("conclusion")}
    except Refused as exc:
        return {"ok": False, "id": check_run_id, "error": str(exc)}


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

    任一步失败：若 6/7 已发生，先补偿降级（并如实报告是否成功），再 ``Refused``；
    1–5 失败时不会存在任何可被采信的成功证据。**status 绝不在 check run 证明之前发布**，
    因此不存在"函数报失败但远端仍留 success status"的状态。
    """
    if publish_mode not in PUBLISH_MODES:
        raise Refused(f"未知 publish_mode={publish_mode!r}（可选 {PUBLISH_MODES}）")
    client = Client(repo, token, api)
    if not pr:
        pr = resolve_pr(client, head_sha)
    run_info = verify_run(client, run_id, head_sha, required, pr=pr)
    pr_info = verify_pr(client, pr, head_sha)
    report: dict = {"ok": True, "run": run_info, "pr": pr_info, "publish_mode": publish_mode,
                    "steps": [], "compensation": None}

    try:
        _run_transaction(client, report, run_id=run_id, pr=pr, head_sha=head_sha,
                         target_url=target_url, attempts=attempts, publish_mode=publish_mode)
    except Refused as exc:
        exc.report = report                      # 失败时仍暴露远端证据与补偿结果
        raise
    return report


def _run_transaction(client: Client, report: dict, *, run_id: str, pr: int, head_sha: str,
                     target_url: str, attempts: int, publish_mode: str) -> None:
    """路线 A 事务主体。任何失败都抛 ``Refused``；调用方负责把 ``report`` 附到异常上。"""
    if publish_mode in ("check-run", "dual"):
        created = create_check_run(client, head_sha, run_id, target_url)
        cr_id = created.get("id")
        report["steps"].append("create_check_run:in_progress")
        app = created.get("app") or {}
        if app.get("id") != EXPECTED_APP_ID:
            report["compensation"] = _downgrade(client, cr_id, "创建返回 app.id 不符")
            raise Refused(f"创建返回 app.id={app.get('id')!r}，非 {EXPECTED_APP_ID}")
        try:
            verify_check_run(client, head_sha, run_id, attempts=attempts,
                             expect_conclusion="in_progress", check_run_id=cr_id)
        except Refused as exc:
            report["compensation"] = _downgrade(client, cr_id, f"in_progress 回读失败：{exc}")
            raise Refused(f"check run 回读未确认 in_progress：{exc}") from exc
        report["steps"].append("verify_check_run:in_progress")
        pr_info = verify_pr(client, pr, head_sha)          # 创建后复核
        report["steps"].append("verify_pr:post-create")
        try:
            finalized = finalize_check_run(client, cr_id, "success")
        except Refused as exc:
            report["compensation"] = _downgrade(client, cr_id, f"完成失败：{exc}")
            raise Refused(f"完成 check run 为 success 失败：{exc}") from exc
        report["steps"].append("finalize_check_run:success")
        try:
            verified = verify_check_run(client, head_sha, run_id, attempts=attempts,
                                        expect_conclusion="success", check_run_id=cr_id)
        except Refused as exc:
            report["compensation"] = _downgrade(client, cr_id, f"回读失败：{exc}")
            raise Refused(f"check run 回读未确认 success：{exc}") from exc
        report["steps"].append("verify_check_run:success")
        report["check_run"] = {"id": cr_id, "name": finalized.get("name"),
                               "head_sha": finalized.get("head_sha"),
                               "external_id": finalized.get("external_id"), "verify": verified}

    if publish_mode in ("status", "dual"):
        try:
            pr_info = verify_pr(client, pr, head_sha)      # status 发布前最后一道基线检查
            report["steps"].append("verify_pr:pre-status")
        except Refused as exc:
            if report.get("check_run"):
                report["compensation"] = _downgrade(client, report["check_run"]["id"],
                                                    f"status 前置复核失败：{exc}")
            raise
        posted = publish(client, head_sha, run_id, "受信校验通过（run 级核验）", target_url)
        verified_status = verify_published(client, head_sha, attempts=attempts, expect_run_id=run_id)
        report["status"] = {**posted, "verify": verified_status}
        if isinstance(posted, dict):
            report.update(posted)
        report["verify"] = verified_status
        report["steps"].append("publish:status+verify")

    report["after"] = verify_pr(client, pr, head_sha)
    report["steps"].append("verify_pr:final")
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
