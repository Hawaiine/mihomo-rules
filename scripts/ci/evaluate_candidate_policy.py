#!/usr/bin/env python3
"""evaluate_candidate_policy.py — D-NEW-1 可信运行时判定器（分类 + 审批 + 决策）。

为什么需要它：`classify_change.py` / `verify_approval.py` / `decide_merge.py` 本身只是
可独立运行的模块；只有当**可信 workflow 真的调用它们**、并把结果作为候选 PR 的门禁与
自动合并前置条件时，"模块存在"才等于"运行时已执行"。

本脚本做四件事，全部基于可信读取（候选树只作数据，绝不执行候选代码）：

1. 绑定身份：repository / PR / base SHA / head SHA / run id / lock generation；
2. 完整读取变更清单与 base/head tree（分页 + `truncated` 检查），交给
   `classify_change.classify()` 做四分类；
3. `NEW_RULESET` 时真实读取 review 分页并调用 `verify_approval.verify_approval()`；
4. 把分类、必需检查、锁状态、平台只读状态交给 `decide_merge.decide()`，输出稳定
   reason code；`production_allowed` 只有在生产开关打开且平台契约已核实时才可能为 true。

退出码：0 = 非 BLOCKED；1 = BLOCKED（理由见 --json）。任何 API 失败/字段缺失/截断
都按 BLOCKED 处理（fail-closed）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_change as cc          # noqa: E402
import decide_merge as dm             # noqa: E402
import verify_approval as va          # noqa: E402
import verify_candidate as vc         # noqa: E402  （共用完整分页的变更清单读取）
import verify_required_checks as vrc  # noqa: E402

STATUS_CONTEXT = "route-a/verify"
MAX_TREES = 100000


class PolicyError(RuntimeError):
    pass


# ───────────────────────────── 纯判定（可单测） ─────────────────────────────

def evaluate_policy(*, repo: str, pr: int, base_sha: str, head_sha: str, run_id: str,
                    generation: int, change_class: str, changed_files: int,
                    review_complete: bool = True, reviews: list | None = None,
                    approver: str = "", checks_ok: bool = False, checks_problems: list | None = None,
                    lock_state: str = "idle", lock_evidence: dict | None = None,
                    platform_ok: bool = False, platform_evidence: dict | None = None,
                    production_mode: bool = False) -> dict:
    """把可信输入折成决策。所有默认值都是 fail-closed 方向。"""
    evidence: dict = {
        "repo": repo, "pr": int(pr), "base_sha": base_sha, "head_sha": head_sha,
        "run_id": run_id, "lock_generation": int(generation),
        "changed_files": int(changed_files), "checks_ok": bool(checks_ok),
        "checks_problems": list(checks_problems or []),
        "lock_state": lock_state, "lock": lock_evidence or {},
        "platform_ok": bool(platform_ok), "platform": platform_evidence or {},
        "production_mode": bool(production_mode),
        "approval": {"approver": approver, "review_count": len(reviews or []),
                     "complete": bool(review_complete)},
    }

    approval_ok = False
    approval_reasons: list[str] = []
    if change_class == cc.NEW_RULESET:
        approval_ok, approval_reasons, ap_ev = va.verify_approval(
            reviews or [], head_sha=head_sha, approver=approver, complete=review_complete)
        evidence["approval"].update(ap_ev)

    decision = dm.decide(change_class=change_class, checks_ok=checks_ok, head_sha_ok=True,
                         base_sha_ok=True, approval_ok=approval_ok,
                         approval_reasons=approval_reasons, lock_state=lock_state,
                         pr_open=True, platform_ok=platform_ok,
                         production_mode=production_mode)
    decision.update({
        "change_class": change_class,
        "evidence": evidence,
        # eligible 只表示"具备资格"；真正允许发送合并 mutation 还需生产开关与平台契约
        "production_allowed": bool(decision.get("production_allowed")) and bool(platform_ok),
    })
    return decision


# ───────────────────────────── GitHub 采集 ─────────────────────────────

def _call(repo: str, token: str, path: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "route-a-policy"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PolicyError(f"GitHub API 不可用: {exc}") from exc


def _tree_paths(repo: str, token: str, sha: str) -> set[str]:
    """recursive tree；`truncated=true` 或条目异常即拒绝（不能证明完整）。"""
    status, payload = _call(repo, token, f"/repos/{repo}/git/trees/{sha}?recursive=1")
    if status != 200 or not isinstance(payload, dict):
        raise PolicyError(f"读取 tree {sha[:12]} 失败: HTTP {status}")
    if payload.get("truncated") is True:
        raise PolicyError(f"tree {sha[:12]} 被 GitHub 截断（truncated=true）")
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise PolicyError(f"tree {sha[:12]} 响应缺少 tree 数组")
    out: set[str] = set()
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = item.get("path")
        if isinstance(path, str) and path:
            out.add(path)
    if not out:
        raise PolicyError(f"tree {sha[:12]} 为空")
    return out


def _platform_state(repo: str, token: str) -> tuple[bool, dict]:
    """只读检查仓库 rulesets 是否把 `route-a/verify` 作为目标分支的必需 context。

    读不到（403/404/字段缺失）一律视为**未就绪**，绝不以"大概已配置"放行。
    """
    status, items = _call(repo, token, f"/repos/{repo}/rulesets")
    if status != 200 or not isinstance(items, list):
        return False, {"readable": False, "http": status,
                       "reason": "无法读取 rulesets（需要只读管理权限）"}
    evidence: dict = {"readable": True, "rulesets": [], "required_contexts": []}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        rid = item["id"]
        st, doc = _call(repo, token, f"/repos/{repo}/rulesets/{rid}")
        entry = {"id": rid, "name": item.get("name"), "target": item.get("target"),
                 "enforcement": item.get("enforcement"), "readable": st == 200}
        contexts: list[str] = []
        if st == 200 and isinstance(doc, dict):
            entry["enforcement"] = doc.get("enforcement", entry["enforcement"])
            for rule in doc.get("rules") or []:
                if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                    continue
                for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
                    ctx = (check or {}).get("context")
                    if isinstance(ctx, str) and ctx:
                        contexts.append(ctx)
        entry["contexts"] = contexts
        evidence["rulesets"].append(entry)
        if item.get("target") == "branch" and item.get("enforcement") == "active":
            evidence["required_contexts"].extend(contexts)
    ok = STATUS_CONTEXT in evidence["required_contexts"]
    if not ok:
        evidence["reason"] = (f"active branch ruleset 未包含 {STATUS_CONTEXT}"
                              f"（实际 {sorted(set(evidence['required_contexts']))}）")
    return ok, evidence


def _lock_state(repo: str, token: str, run_id: str, generation: int) -> tuple[str, dict]:
    """读取 D-15 状态（只读）。不创建 ref、不写任何状态。"""
    import gate_state as gs
    try:
        store = gs.GitHubRefStore(repo, token)
        state, sha = store.read()
    except gs.StateError as exc:
        return "unknown", {"readable": False, "error": str(exc)}
    if state is None:
        return "idle", {"readable": True, "exists": False}
    ev = {"readable": True, "exists": True, "ref_sha": sha, "phase": state.phase,
          "run_id": state.run_id, "generation": state.generation, "epoch": state.epoch,
          "conclusion": state.conclusion}
    if state.phase in gs.BLOCKING_PHASES:
        return state.phase, ev
    same_run = state.run_id == run_id
    gen_ok = generation <= 0 or state.generation == generation
    if same_run and gen_ok and state.conclusion in ("none", "success"):
        return "held", ev
    return "idle", ev


def collect_and_evaluate(*, repo: str, pr: int, base_sha: str, head_sha: str, run_id: str,
                         generation: int, token: str, base_branch: str = "main",
                         approver: str = "", production_mode: bool = False,
                         mode: str = "merge",
                         required_manifest: str = ".github/required-checks.json") -> dict:
    # 1) PR 身份绑定
    status, pr_obj = _call(repo, token, f"/repos/{repo}/pulls/{pr}")
    if status != 200 or not isinstance(pr_obj, dict):
        raise PolicyError(f"读取 PR #{pr} 失败: HTTP {status}")
    if pr_obj.get("state") != "open" or pr_obj.get("merged"):
        raise PolicyError(f"PR 非 open/已合并：state={pr_obj.get('state')} merged={pr_obj.get('merged')}")
    if (pr_obj.get("base") or {}).get("ref") != base_branch:
        raise PolicyError("PR 目标分支不是 " + base_branch)
    actual_head = (pr_obj.get("head") or {}).get("sha")
    if actual_head != head_sha:
        raise PolicyError(f"PR head 漂移：{actual_head} != {head_sha}")
    if (pr_obj.get("base") or {}).get("sha") != base_sha:
        raise PolicyError("PR base SHA 与预期不一致（base 漂移）")
    owner = ((pr_obj.get("base") or {}).get("repo") or {}).get("owner") or {}
    approver = approver or str(owner.get("login") or "")

    # 2) 变更清单 + tree（完整证据；保留 status/previous_filename 供分类器使用）
    raw_files = vc.pr_files(repo, token, pr, head_sha)
    files = [item["filename"] for item in raw_files]
    changed = [cc.ChangedFile.from_api(item) for item in raw_files]
    base_paths = _tree_paths(repo, token, base_sha)
    head_paths = _tree_paths(repo, token, head_sha)
    for path in files:
        if path not in head_paths and path not in base_paths:
            raise PolicyError(f"变更路径不在任何 tree 中：{path}")
    classification = cc.classify(changed, base_sha=base_sha, head_sha=head_sha,
                                 truncated=False, base_paths=base_paths, head_paths=head_paths)

    # 3) 审批（仅新增 Ruleset 需要；仍读完整分页）
    reviews: list = []
    review_complete = True
    if classification.change_class == cc.NEW_RULESET:
        def _getter(url: str, tok: str) -> tuple[int, object]:
            prefix = "https://api.github.com"
            path = url[len(prefix):] if url.startswith(prefix) else url
            return _call(repo, tok, path)

        reviews, review_complete = va.collect_reviews(
            _getter, repo=repo, pr=pr, token=token, api_base="https://api.github.com")

    # 4) 必需检查。verify 模式下"检查"就是本 run 刚执行完的可信验证（`--mode verify`），
    #    不能再去读本 run 尚未发布的 route-a/verify（否则形成自证循环）；merge 模式
    #    必须真实读取 check-run / commit status（`verify_required_checks`）。
    if mode == "verify":
        checks_ok, checks_report, checks_problems = True, {"gate_mode": "in-run"}, []
        checks_source = "in-run-trusted-verification"
    else:
        checks_ok, checks_report, checks_problems = vrc.verify(
            repo=repo, pr=int(pr), expected_head=head_sha, token=token,
            base_branch=base_branch, required_manifest=required_manifest)
        checks_source = "platform-required-checks"

    # 5) 锁与平台（只读）
    lock_state, lock_ev = _lock_state(repo, token, run_id, generation)
    platform_ok, platform_ev = _platform_state(repo, token)

    out = evaluate_policy(
        repo=repo, pr=pr, base_sha=base_sha, head_sha=head_sha, run_id=run_id,
        generation=generation, change_class=classification.change_class,
        changed_files=len(files), review_complete=review_complete, reviews=reviews,
        approver=approver, checks_ok=checks_ok, checks_problems=checks_problems,
        lock_state=lock_state, lock_evidence=lock_ev, platform_ok=platform_ok,
        platform_evidence=platform_ev, production_mode=production_mode)
    out["evidence"]["classification"] = classification.to_dict()
    out["evidence"]["checks_source"] = checks_source
    out["evidence"]["required_checks"] = {k: checks_report.get(k) for k in
                                          ("gate_mode", "gate_state", "provenance_state", "pr_head_sha")}
    # verify 模式的裁决口径：**只**看分类与审批（数据校验已由 verify_candidate 完成）。
    # merge 决策（含平台/锁/必需检查）仍完整计算并写入证据，但不作为状态门禁。
    verification_ok = classification.change_class == cc.EXISTING_DATA_ONLY
    verification_reasons: list[str] = []
    if classification.change_class == cc.GOVERNANCE_CHANGE:
        verification_reasons.append("GOVERNANCE_CHANGE_REQUIRES_HUMAN_PR")
    elif classification.change_class == cc.MIXED_OR_UNKNOWN:
        verification_reasons.append("CHANGE_UNPROVEN")
    elif classification.change_class == cc.NEW_RULESET:
        ok_approval, ap_reasons, _ = va.verify_approval(
            reviews, head_sha=head_sha, approver=approver, complete=review_complete)
        verification_ok = ok_approval
        verification_reasons.extend(ap_reasons)
    out["verification_ok"] = verification_ok
    out["verification_reasons"] = verification_reasons
    out["mode"] = mode
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-NEW-1 候选策略判定（只读，fail-closed）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    ap.add_argument("--generation", type=int, default=0)
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--mode", choices=["verify", "merge"], default="merge",
                    help="verify=候选验证阶段（分类+审批裁决）；merge=合并前完整决策")
    ap.add_argument("--approver", default=os.environ.get("ROUTE_A_APPROVER", ""),
                    help="人工审批人 login；缺省用仓库 owner（真实账号，非编造）")
    ap.add_argument("--production-mode", type=int, choices=[0, 1], default=0)
    ap.add_argument("--required-checks", default=".github/required-checks.json")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("❌ 缺少 GITHUB_TOKEN", file=sys.stderr)
        return 1
    try:
        result = collect_and_evaluate(
            repo=args.repo, pr=args.pr, base_sha=args.base_sha, head_sha=args.head_sha,
            run_id=args.run_id, generation=args.generation, token=token,
            base_branch=args.base_branch, approver=args.approver,
            production_mode=bool(args.production_mode), mode=args.mode,
            required_manifest=args.required_checks)
    except (PolicyError, vc.VerifyRefused, vrc.ManifestError) as exc:
        result = {"decision": dm.BLOCKED, "reason_codes": [f"ERROR:{exc}"],
                  "production_allowed": False, "change_class": cc.MIXED_OR_UNKNOWN,
                  "verification_ok": False, "verification_reasons": [f"ERROR:{exc}"],
                  "mode": args.mode, "evidence": {"error": str(exc)}}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    print(text)
    if args.json:
        args.json.write(text + "\n")
    if args.mode == "verify":
        return 0 if result.get("verification_ok") else 1
    return 0 if (result.get("decision") != dm.BLOCKED and result.get("production_allowed")) else 1


if __name__ == "__main__":
    sys.exit(main())
