#!/usr/bin/env python3
"""publish_candidate_status.py — 候选 PR 路径的 Route A 状态发布器（双发事务）。

为什么需要独立入口
==================
`publish_run_status.py` 的事务前提是「存在一个真实的 `pull_request` 事件 run，且该 run
的 path 必须是 `.github/workflows/pr-verify.yml`」——它用 run 里的三个必需 job 作为证据源。
候选 PR 由内置 `GITHUB_TOKEN` 创建，**不会**产生 `pr-verify.yml` 的 run（见
`pr-verify.yml` 头部注释与 `required-checks.json` 的 notes）。因此候选路径不能复用
`publish_for_run()` 的 run 级核验模型。

本脚本把「证据源」换成候选路径自身的受信验证结果：
  * `verify_candidate.py`（完整分页 diff / 单一路径政策 / 树自洽）
  * `evaluate_candidate_policy.py --mode verify`（分类 + 新增 Ruleset 人工审批）
两者的退出码由调用方汇总成 `--state success|failure`，本脚本**只发布该状态**，不自行
推断成功。

共同的安全性质（与 `publish_run_status.py` 完全一致）
=====================================================
1. 绑定**精确 head SHA**：发布前必须确认 PR open、base 分支与基线未变、`PR head == --head-sha`；
   创建 Check Run 后、发布 status 前、事务收尾各复核一次（防 head/base 漂移）。
2. 事务顺序：① 只读探查同 SHA 既有 `route-a/verify`（读不到即停止；发现历史 success 先撤销
   并回读确认，确认不了即停止）② 建 in_progress Check Run（app.id=15368）③ 对象级回读
   ④ 复核 PR/head/base ⑤ 完成 Check Run 为**实际状态** ⑥ 回读确认 ⑦ 才发 commit status
   ⑧ 回读确认 ⑨ 收尾复核。
3. 失败即补偿：任何一步失败都尽力把两条通道降级为 failure 并做对象级回读；未确认撤销时
   置 `possible_success_evidence_remains=true`，要求人工只读核验。GitHub API 写入没有
   原子事务语义，本脚本**不保证**任何故障下都 fail-closed。

CLI 契约（`candidate-pr-sync.yml` 的 verify job 直接使用）
=========================================================
    publish_candidate_status.py --repo R --pr N --head-sha SHA --state success|failure
        --run-id ID --run-url URL [--base-branch main] [--description TEXT] [--json PATH]

exit: 0 发布并回读确认；1 拒绝/失败（原因见 stderr 与 --json）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_run_status as prs  # noqa: E402

CONTEXT = prs.CONTEXT
LEGAL_STATES = ("success", "failure")


def create_check_run(client: prs.Client, sha: str, run_id: str, target_url: str,
                     description: str) -> dict:
    """创建 in_progress Check Run（候选路径语义：证据来自候选验证，不是 run job）。"""
    body = {
        "name": CONTEXT,
        "head_sha": sha,
        "status": "in_progress",
        "external_id": f"{prs.EXTERNAL_ID_PREFIX}{run_id}",
        "details_url": target_url or f"https://github.com/{client.repo}/actions/runs/{run_id}",
        "output": {"title": "Route A 候选校验进行中",
                   "summary": (f"候选路径受信验证（verify_candidate + "
                               f"evaluate_candidate_policy --mode verify）已完成，"
                               f"等待发布后一致性复核。描述：{description or '无'}")},
    }
    status, payload = client.post(f"/repos/{client.repo}/check-runs", body)
    if status not in (200, 201) or not isinstance(payload, dict):
        raise prs.Refused(f"创建 check run 失败: HTTP {status} {str(payload)[:200]}")
    return payload


def finalize_check_run(client: prs.Client, check_run_id: int, conclusion: str,
                       description: str) -> dict:
    """把 Check Run 更新为 completed/<conclusion>（success 或 failure）。"""
    if conclusion not in LEGAL_STATES:
        raise prs.Refused(f"非法 conclusion={conclusion!r}")
    title = ("Route A 候选校验通过" if conclusion == "success"
             else "Route A 候选校验未通过")
    body = {"status": "completed", "conclusion": conclusion,
            "output": {"title": title,
                       "summary": description or (
                           "候选路径受信验证通过；PR head/base 绑定已在发布前后复核。"
                           if conclusion == "success"
                           else "候选路径受信验证未通过；本结论已在精确 head SHA 上发布。")}}
    status, payload = client.patch(f"/repos/{client.repo}/check-runs/{check_run_id}", body)
    if status not in (200, 201) or not isinstance(payload, dict):
        raise prs.Refused(f"更新 check run 失败: HTTP {status} {str(payload)[:200]}")
    return payload


def publish_status_state(client: prs.Client, sha: str, state: str, description: str,
                         target_url: str) -> dict:
    status, body = client.post(f"/repos/{client.repo}/statuses/{sha}", {
        "state": state, "context": CONTEXT, "description": description[:140],
        "target_url": target_url})
    if status not in (200, 201):
        raise prs.Refused(f"发布状态失败: HTTP {status} {body}")
    return {"posted": True, "sha": sha, "context": CONTEXT, "state": state}


def verify_published_state(client: prs.Client, sha: str, expect_state: str, *,
                           attempts: int = 5, delay: float = 1.0,
                           expect_run_id: str = "") -> dict:
    """回读 status：creator、state、target_url 的 run 绑定全部核对。"""
    last, last_problem = None, ""
    for i in range(attempts):
        status, items = client.call(
            f"/repos/{client.repo}/commits/{sha}/statuses?per_page=100")
        if status == 200 and isinstance(items, list):
            ours = [s for s in items if prs.is_route_a_context(s.get("context"))]
            if ours:
                last = ours[0]
                problem = prs._creator_problem(last)
                target = str(last.get("target_url") or "")
                if not problem and expect_run_id:
                    err = prs.parse_run_url(target)
                    if err is None:
                        problem = f"status target_url 不可解析为合法 run URL: {target!r}"
                    elif err.get("run_id") != str(expect_run_id):
                        problem = (f"status target_url run_id={err.get('run_id')}"
                                   f" 非 {expect_run_id}（拒绝模糊匹配）")
                if not problem and last.get("state") == expect_state:
                    creator = last.get("creator") or {}
                    return {"verified": True, "creator": creator.get("login"),
                            "creator_type": creator.get("type"),
                            "state": last["state"], "target_url": target}
                last_problem = problem or f"state={last.get('state')!r}"
        time.sleep(delay * (i + 1))
    raise prs.Refused(f"发布后回读未看到可信 {CONTEXT}={expect_state}"
                      f"（当前 {last!r}；{last_problem}）")


def publish_candidate_result(*, repo: str, pr: int, head_sha: str, state: str,
                             token: str, run_id: str, run_attempt: str = "1",
                             description: str = "",
                             api: str = "https://api.github.com", base_branch: str = "main",
                             target_url: str = "", attempts: int = 5) -> dict:
    """候选路径发布事务。``state`` 必须是调用方真实汇总的 success/failure。

    ``run_id`` 是**数字**的 Actions run id（用于 target_url 绑定校验）；
    ``run_attempt`` 区分同一 run 的重试。Check Run 的 ``external_id`` 用
    ``route-a-run:<run_id>-<run_attempt>``，从而同一 run 的第 N 次尝试不会被第
    N-1 次的回读证据冒充，同时 status 的 URL 绑定仍只核对数字 run id。
    """
    if state not in LEGAL_STATES:
        raise prs.Refused(f"非法 state={state!r}（可选 {LEGAL_STATES}）")
    report: dict = {"ok": True, "publish_mode": "dual", "source": "candidate_self_verification",
                    "state": state, "steps": [], "compensation": None,
                    "status_compensation": None, "status_publish_attempted": False,
                    "status_may_have_been_published": False,
                    "historical_success_observed": False,
                    "historical_success_revoked": None,
                    "status_evidence_unknown": False,
                    "possible_success_evidence_remains": False}
    binding_id = f"{run_id}-{run_attempt}"
    client = None
    try:
        client = prs.Client(repo, token, api)
        report["pr"] = prs.verify_pr(client, pr, head_sha, base_branch)
        report["run"] = {"run_id": binding_id, "url_run_id": run_id,
                         "run_attempt": run_attempt, "head_sha": head_sha,
                         "source": "candidate_self_verification",
                         "jobs": [], "note": ("候选路径无 pr-verify run；证据为 "
                                              "verify_candidate + evaluate_candidate_policy "
                                              "的退出码，由 --state 传入")}
        report["steps"].append("verify_pr:pre")

        # ── 步骤 0：同 SHA 历史 success 探查与撤销 ──
        hist = prs.inspect_route_a_status(client, head_sha)
        report["historical_status_probe"] = hist
        if not hist.get("readable"):
            report["status_evidence_unknown"] = True
            report["status_evidence_unknown_reason"] = f"步骤 0 状态不可读：{hist.get('error')}"
            report["possible_success_evidence_remains"] = True
            report["operator_action"] = prs._derive_operator_action(report)
            raise prs.Refused(
                f"无法读取同 SHA 的 route-a/verify 状态（证据不确定）：{hist.get('error')}")
        report["historical_success_observed"] = bool(hist.get("observed"))
        if report["historical_success_observed"]:
            rev = prs.compensate_status_failure(
                client, head_sha, run_id, "启动前撤销同 SHA 历史 success", target_url)
            report["historical_success_revocation"] = rev
            report["historical_success_revoked"] = bool(rev.get("ok"))
            if not rev.get("ok"):
                report["possible_success_evidence_remains"] = True
                report["operator_action"] = prs._derive_operator_action(report)
                raise prs.Refused("未能确认撤销同 SHA 历史 success（不发布任何新的成功证据）："
                                  f"{rev.get('error')}")
            report["steps"].append("revoke_historical_success")

        # ── Check Run 权威通道 ──
        created = create_check_run(client, head_sha, binding_id, target_url, description)
        cr_id = created.get("id")
        report["check_run_created"] = {"id": cr_id,
                                       "may_have_succeeded": (state == "success")}
        report["steps"].append("create_check_run:in_progress")
        app = created.get("app") or {}
        if app.get("id") != prs.EXPECTED_APP_ID:
            prs._compensate_all(client, report,
                                f"创建返回 app.id 不符：{app.get('id')!r}", target_url)
            raise prs.Refused(f"创建返回 app.id={app.get('id')!r}，非 {prs.EXPECTED_APP_ID}")
        try:
            prs.verify_check_run(client, head_sha, binding_id, attempts=attempts,
                                 expect_conclusion="in_progress", check_run_id=cr_id)
        except prs.Refused as exc:
            prs._compensate_all(client, report, f"in_progress 回读失败：{exc}", target_url)
            raise prs.Refused(f"check run 回读未确认 in_progress：{exc}") from exc
        report["steps"].append("verify_check_run:in_progress")

        report["pr"] = prs.verify_pr(client, pr, head_sha, base_branch)   # 创建后复核
        report["steps"].append("verify_pr:post-create")
        try:
            finalized = finalize_check_run(client, cr_id, state, description)
        except prs.Refused as exc:
            prs._compensate_all(client, report,
                                f"完成失败（远端可能已写入）：{exc}", target_url)
            raise prs.Refused(f"完成 check run 为 {state} 失败：{exc}") from exc
        report["steps"].append(f"finalize_check_run:{state}")
        try:
            verified = prs.verify_check_run(client, head_sha, binding_id, attempts=attempts,
                                            expect_conclusion=state, check_run_id=cr_id)
        except prs.Refused as exc:
            prs._compensate_all(client, report, f"回读失败：{exc}", target_url)
            raise prs.Refused(f"check run 回读未确认 {state}：{exc}") from exc
        report["steps"].append(f"verify_check_run:{state}")
        report["check_run"] = {"id": cr_id, "name": finalized.get("name"),
                               "head_sha": finalized.get("head_sha"),
                               "external_id": finalized.get("external_id"),
                               "may_have_succeeded": (state == "success"),
                               "verify": verified}

        # ── commit status 兼容通道（仅在 Check Run 回读确认之后）──
        report["pr"] = prs.verify_pr(client, pr, head_sha, base_branch)
        report["steps"].append("verify_pr:pre-status")
        report["status_publish_attempted"] = True
        report["status_may_have_been_published"] = True
        try:
            posted = publish_status_state(
                client, head_sha, state,
                description or f"候选路径受信校验 {state}", target_url)
            verified_status = verify_published_state(
                client, head_sha, state, attempts=attempts, expect_run_id=run_id)
            report["status"] = {**posted, "verify": verified_status}
            report.update({k: v for k, v in posted.items() if k in ("sha", "context", "state")})
            report["verify"] = verified_status
            report["steps"].append("publish:status+verify")
        except prs.Refused as exc:
            prs._compensate_all(client, report, f"status 阶段失败：{exc}", target_url)
            raise

        report["after"] = prs.verify_pr(client, pr, head_sha, base_branch)
        report["steps"].append("verify_pr:final")
    except Exception as exc:                       # noqa: BLE001
        report["ok"] = False
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        if client is not None:
            if "historical_status_probe" not in report:
                hist = prs.inspect_route_a_status(client, head_sha)
                report["historical_status_probe"] = hist
                if hist.get("readable"):
                    report["historical_success_observed"] = bool(hist.get("observed"))
                    if report["historical_success_observed"]:
                        report["historical_success_disposition"] = \
                            "not_processed_pre_verification_failure"
                        report["possible_success_evidence_remains"] = True
                else:
                    report["status_evidence_unknown"] = True
                    report["status_evidence_unknown_reason"] = \
                        f"历史状态不可读：{hist.get('error')}"
                    report["possible_success_evidence_remains"] = True
            if (report.get("check_run") or report.get("check_run_created")
                    or report.get("status_publish_attempted")
                    or report.get("historical_success_observed")):
                prs._compensate_all(client, report,
                                    f"事务异常 {type(exc).__name__}: {exc}", target_url)
        else:
            report["status_evidence_unknown"] = True
            report["status_evidence_unknown_reason"] = "无法建立 GitHub 客户端，未执行只读探查"
            report["possible_success_evidence_remains"] = True
        report["operator_action"] = prs._derive_operator_action(report)
        if isinstance(exc, prs.Refused):
            exc.report = report
            raise
        refused = prs.Refused(f"候选发布事务失败：{type(exc).__name__}: {exc}")
        refused.report = report
        raise refused from exc
    return report


def build_parser() -> argparse.ArgumentParser:
    """CLI 契约的唯一来源：测试用同一 parser 校验 workflow 实际传入的参数。"""
    ap = argparse.ArgumentParser(description="Route A 候选 PR 状态发布（双发事务）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--state", required=True, choices=LEGAL_STATES)
    ap.add_argument("--run-id", required=True,
                    help="GitHub Actions run id（数字；用于 target_url 绑定）")
    ap.add_argument("--run-attempt", default="1",
                    help="run attempt（同一 run 的重试序号，用于 external_id 去重）")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--description", default="")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        report = publish_candidate_result(
            repo=args.repo, pr=args.pr, head_sha=args.head_sha, state=args.state,
            token=os.environ.get("GITHUB_TOKEN", ""), run_id=args.run_id,
            run_attempt=args.run_attempt, description=args.description,
            api=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            base_branch=args.base_branch, target_url=args.run_url)
    except prs.Refused as exc:
        print(f"❌ 拒绝发布：{exc}", file=sys.stderr)
        if args.json:
            detail = getattr(exc, "report", None)
            json.dump({"ok": False, "reason": str(exc),
                       **({"evidence": detail} if detail else {})},
                      args.json, ensure_ascii=False, indent=1)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
