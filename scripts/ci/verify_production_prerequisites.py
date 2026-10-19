#!/usr/bin/env python3
"""verify_production_prerequisites.py — 生产前置条件的**只读**检查器。

它回答一个问题：**现在能不能开始真实的 Route A / D-15 生产运行？**
不是"代码写完了吗"，而是平台契约、开关、状态持久化是否真的就位。

硬约束：
* 只发 GET；不创建 ref、不改 Ruleset、不改变量、不触发合并、不碰任何开关；
* 报告里固定写 `writes_performed: false`；
* 任何 API 读不到/结构异常，都不得得到 READY（宁可 BLOCKED，也不猜）。

退出码（与 JSON 的 `decision` 必须一致）：
  0 = 所有前置条件均可证明通过（READY）
  1 = 前置条件明确不满足（BLOCKED）
  2 = 输入/配置错误（用法错、清单缺失等）
  3 = 外部 API 不可读或结果不可确定（UNVERIFIABLE）

用法：
  python3 scripts/ci/verify_production_prerequisites.py \
      --repo Hawaiine/mihomo-rules --target-branch main \
      --json /tmp/production-preflight.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate_state as gs          # noqa: E402
import platform_rulesets as prg  # noqa: E402

SCHEMA_VERSION = 1
CHECK_ORDER = ("ruleset_active", "route_a_required_status", "human_pr_required_checks",
               "actions_permissions", "production_switches", "gate_state",
               "snapshot_recovery")
SWITCHES = ("CANDIDATE_SYNC_ENABLED", "GATE_STATE_ENABLED", "CANDIDATE_AUTOMERGE_ENABLED")
STATUS_CONTEXT = "route-a/verify"
EXPECTED_APP_ID = 15368


class TransportError(RuntimeError):
    """网络/协议层不可用：调用方按 exit 3 处理。"""


class InputError(RuntimeError):
    """本地输入不合法：调用方按 exit 2 处理。"""


def _http_get(path: str, token: str, *, api: str = "https://api.github.com") -> tuple[int, object]:
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "route-a-preflight"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TransportError(f"GitHub API 不可用: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TransportError(f"GitHub API 返回非 JSON: {exc}") from exc


def _check(ok: bool, reasons: list[str], evidence: dict, *, unreadable: bool = False) -> dict:
    out = {"ok": bool(ok), "reason_codes": list(reasons), "evidence": evidence}
    if unreadable:
        out["unreadable"] = True
    return out


# ───────────────────────────── 各检查项 ─────────────────────────────

def check_ruleset_active(fetched: dict, target_branch: str) -> dict:
    if fetched.get("status") != "ok":
        return _check(False, list(fetched.get("reason_codes") or ["RULESET_UNREADABLE"]),
                      {"default_branch": fetched.get("default_branch")}, unreadable=True)
    out = prg.evaluate(fetched["docs"], repo="", target_branch=target_branch,
                       required_contexts=[], default_branch=fetched.get("default_branch"))
    covering = [r for r in out["evidence"]["rulesets"] if r.get("covers_target")]
    reasons = list(out["reason_codes"])
    if not covering and "RULESET_NOT_COVERING_TARGET" not in reasons:
        reasons.append("RULESET_NOT_COVERING_TARGET")
    unreadable = "RULESET_UNREADABLE" in out["reason_codes"]
    return _check(out["ok"], [] if out["ok"] else (reasons or ["MAIN_RULESET_NOT_READY"]), {
        "target_ref": out["evidence"]["target_ref"],
        "default_branch": fetched.get("default_branch"),
        "ruleset_count": fetched.get("ruleset_count"),
        "covering_rulesets": out["evidence"]["covering_rulesets"],
        "rulesets": out["evidence"]["rulesets"],
    }, unreadable=unreadable)


def check_route_a(fetched: dict, target_branch: str) -> dict:
    if fetched.get("status") != "ok":
        return _check(False, list(fetched.get("reason_codes") or ["RULESET_UNREADABLE"]),
                      {}, unreadable=True)
    out = prg.evaluate(fetched["docs"], repo="", target_branch=target_branch,
                       required_contexts=[STATUS_CONTEXT],
                       default_branch=fetched.get("default_branch"))
    reasons = list(out["reason_codes"]) or ["MAIN_REQUIRED_STATUS_CONTEXT_MISSING"]
    return _check(out["ok"], [] if out["ok"] else reasons, {
        "required_context": STATUS_CONTEXT,
        "expected_integration_id": EXPECTED_APP_ID,
        "found_contexts": out["evidence"].get("found_contexts"),
        "near_miss_contexts": out["evidence"].get("near_miss_contexts", []),
        "covering_rulesets": out["evidence"]["covering_rulesets"],
    }, unreadable="RULESET_UNREADABLE" in out["reason_codes"])


def check_human_pr_required_checks(root: str) -> dict:
    manifest_path = os.path.join(root, ".github", "required-checks.json")
    try:
        manifest = json.load(open(manifest_path, encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"缺少清单 {manifest_path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"清单不是合法 JSON: {exc}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise InputError("需要 PyYAML 才能解析 workflow") from exc

    wf_rel = manifest.get("workflow") or ".github/workflows/pr-verify.yml"
    wf_path = os.path.join(root, wf_rel)
    try:
        wf = yaml.safe_load(open(wf_path, encoding="utf-8")) or {}
    except FileNotFoundError:
        raise InputError(f"缺少 workflow {wf_path}")

    problems: list[str] = []
    checks = manifest.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("REQUIRED_CHECKS_MANIFEST_EMPTY")
        checks = []
    names = [c.get("name") for c in checks if isinstance(c, dict)]
    job_ids = [c.get("job_id") for c in checks if isinstance(c, dict)]
    for c in checks:
        if not isinstance(c, dict):
            problems.append("REQUIRED_CHECKS_ENTRY_MALFORMED")
            continue
        if (c.get("source") or {}).get("app_id") != EXPECTED_APP_ID:
            problems.append(f"REQUIRED_CHECK_SOURCE_UNEXPECTED:{c.get('name')}")
        if c.get("workflow") not in (wf_rel, None):
            problems.append(f"REQUIRED_CHECK_WORKFLOW_MISMATCH:{c.get('name')}")

    # PyYAML 会把 YAML 1.1 的 `on:` 解析成布尔键 True，这里两种都接受
    triggers = (wf.get("on") if isinstance(wf, dict) else None)
    if triggers is None and isinstance(wf, dict):
        triggers = wf.get(True)
    if isinstance(triggers, dict):
        has_pr = "pull_request" in triggers or "pull_request_target" in triggers
    elif isinstance(triggers, list):
        has_pr = "pull_request" in triggers
    else:
        has_pr = False
    if not has_pr:
        problems.append("PR_VERIFY_WORKFLOW_NOT_PULL_REQUEST_TRIGGERED")

    jobs = (wf.get("jobs") or {}) if isinstance(wf, dict) else {}
    for name, jid in zip(names, job_ids):
        if jid not in jobs:
            problems.append(f"REQUIRED_CHECK_JOB_MISSING:{name}")
            continue
        # job 的显示名会变成 check 名；必须与清单一致
        actual = (jobs[jid] or {}).get("name") or jid
        if actual != name:
            problems.append(f"REQUIRED_CHECK_NAME_DRIFT:{jid}->{actual}")
        if (jobs[jid] or {}).get("continue-on-error"):
            problems.append(f"REQUIRED_CHECK_CONTINUE_ON_ERROR:{jid}")
    if has_pr and set(jobs) != set(job_ids) and job_ids:
        problems.append("REQUIRED_CHECKS_JOB_SET_MISMATCH")

    contexts = [c.get("context") for c in manifest.get("status_contexts") or []
                if isinstance(c, dict)]
    if STATUS_CONTEXT not in contexts:
        problems.append("MANIFEST_MISSING_ROUTE_A_STATUS_CONTEXT")

    return _check(not problems, [] if not problems else
                  ["REQUIRED_CHECKS_CONTRACT_MISMATCH"] + problems, {
                      "manifest": wf_rel and ".github/required-checks.json",
                      "checks": names, "jobs": sorted(jobs), "status_contexts": contexts,
                  })


def check_actions_permissions(repo: str, call) -> dict:
    status, doc = call(f"/repos/{repo}/actions/permissions/workflow")
    if status != 200 or not isinstance(doc, dict):
        return _check(False, [f"ACTIONS_PERMISSION_UNREADABLE_HTTP_{status}"],
                      {"http": status}, unreadable=status in (0, 401, 403, 429) or status >= 500)
    perms = doc.get("default_workflow_permissions")
    ok = perms == "read"
    return _check(ok, [] if ok else ["ACTIONS_DEFAULT_PERMISSIONS_NOT_READ"],
                  {"default_workflow_permissions": perms,
                   "can_approve_pull_request_reviews": doc.get("can_approve_pull_request_reviews")})


def check_production_switches(repo: str, call) -> dict:
    status, doc = call(f"/repos/{repo}/actions/variables?per_page=100")
    if status != 200 or not isinstance(doc, dict):
        return _check(False, [f"PRODUCTION_VARIABLES_UNREADABLE_HTTP_{status}"],
                      {"http": status}, unreadable=status in (0, 401, 403, 429) or status >= 500)
    values = {v.get("name"): v.get("value") for v in doc.get("variables") or []
              if isinstance(v, dict)}
    problems: list[str] = []
    detail = {}
    for name in SWITCHES:
        raw = values.get(name, None)
        detail[name] = raw
        if raw is None:
            continue                      # 未设置 = 关闭（合格）
        if not isinstance(raw, str):
            problems.append(f"PRODUCTION_SWITCH_VALUE_INVALID:{name}")
            continue
        if raw.strip().lower() not in ("false", "0", ""):
            problems.append(f"PRODUCTION_SWITCH_ENABLED:{name}")
    return _check(not problems, [] if not problems else ["PRODUCTION_SWITCHES_NOT_READY"] + problems,
                  {"switches": detail, "note": "本轮要求全部关闭；开启属于另行授权的平台操作"})


def check_gate_state(repo: str, call) -> dict:
    status, doc = call(f"/repos/{repo}/git/ref/heads/state/gate")
    if status == 404:
        return _check(False, ["GATE_STATE_NOT_INITIALIZED"],
                      {"exists": False, "http": 404,
                       "note": "未初始化是当前预期状态；真实初始化需单独授权"})
    if status != 200 or not isinstance(doc, dict):
        return _check(False, [f"GATE_STATE_UNREADABLE_HTTP_{status}"], {"http": status},
                      unreadable=True)
    obj = doc.get("object") or {}
    sha, otype = obj.get("sha"), obj.get("type")
    if otype != "tag" or not isinstance(sha, str):
        return _check(False, ["GATE_STATE_REF_NOT_TAG_OBJECT"],
                      {"ref_object_type": otype, "sha": sha})
    st, tag = call(f"/repos/{repo}/git/tags/{sha}")
    if st != 200 or not isinstance(tag, dict) or not isinstance(tag.get("message"), str):
        return _check(False, [f"GATE_STATE_TAG_UNREADABLE_HTTP_{st}"], {"http": st},
                      unreadable=True)
    try:
        payload = json.loads(tag["message"])
    except json.JSONDecodeError:
        return _check(False, ["GATE_STATE_PAYLOAD_NOT_JSON"], {"tag_sha": sha})
    problems = gs.validate_doc(payload)
    evidence = {"exists": True, "tag_sha": sha, "phase": payload.get("phase"),
                "generation": payload.get("generation"), "epoch": payload.get("epoch"),
                "run_id": payload.get("run_id"), "conclusion": payload.get("conclusion")}
    if problems:
        return _check(False, ["GATE_STATE_SCHEMA_INVALID"] + problems, evidence)
    if payload.get("phase") in gs.BLOCKING_PHASES:
        return _check(False, [f"GATE_STATE_BLOCKING_PHASE:{payload.get('phase')}"], evidence)
    return _check(True, [], evidence)


def check_snapshot_recovery(repo: str, call, gate_evidence: dict) -> dict:
    status, refs = call(f"/repos/{repo}/git/matching-refs/tags/{gs.SNAPSHOT_TAG_PREFIX}")
    if status == 404:
        return _check(False, ["GATE_SNAPSHOT_ABSENT"], {"exists": False, "http": 404})
    if status != 200 or not isinstance(refs, list):
        return _check(False, [f"GATE_SNAPSHOT_UNREADABLE_HTTP_{status}"], {"http": status},
                      unreadable=True)
    if not refs:
        return _check(False, ["GATE_SNAPSHOT_ABSENT"], {"exists": False,
                                                        "note": "没有快照就无法证明可恢复"})
    problems: list[str] = []
    verified: list[dict] = []
    for ref in refs[:20]:
        if not isinstance(ref, dict):
            problems.append("SNAPSHOT_REF_MALFORMED")
            continue
        obj = ref.get("object") or {}
        sha, otype = obj.get("sha"), obj.get("type")
        if otype != "tag" or not isinstance(sha, str):
            problems.append(f"SNAPSHOT_REF_NOT_TAG:{ref.get('ref')}")
            continue
        st, tag = call(f"/repos/{repo}/git/tags/{sha}")
        if st != 200 or not isinstance(tag, dict) or not isinstance(tag.get("message"), str):
            problems.append(f"SNAPSHOT_TAG_UNREADABLE:{ref.get('ref')}")
            continue
        try:
            payload = json.loads(tag["message"])
        except json.JSONDecodeError:
            problems.append(f"SNAPSHOT_PAYLOAD_NOT_JSON:{ref.get('ref')}")
            continue
        bad = gs.validate_doc(payload)
        if bad:
            problems.append(f"SNAPSHOT_SCHEMA_INVALID:{ref.get('ref')}")
            continue
        verified.append({"ref": ref.get("ref"), "generation": payload.get("generation"),
                         "digest": payload.get("snapshot_digest"),
                         "run_id": payload.get("run_id")})
    if gate_evidence.get("generation") is not None:
        gens = {v["generation"] for v in verified}
        if gate_evidence["generation"] not in gens:
            problems.append("SNAPSHOT_MISSING_FOR_CURRENT_GENERATION")
    ok = bool(verified) and not problems
    return _check(ok, [] if ok else (problems or ["SNAPSHOT_RECOVERY_NOT_VERIFIED"]),
                  {"verified": verified, "count": len(refs)})


# ───────────────────────────── 汇总 ─────────────────────────────

def build_report(*, repo: str, target_branch: str, call, root: str = ".") -> dict:
    fetched = prg.fetch_ruleset_docs(repo, "", call=call)
    checks: dict[str, dict] = {}
    checks["ruleset_active"] = check_ruleset_active(fetched, target_branch)
    checks["route_a_required_status"] = check_route_a(fetched, target_branch)
    checks["human_pr_required_checks"] = check_human_pr_required_checks(root)
    checks["actions_permissions"] = check_actions_permissions(repo, call)
    checks["production_switches"] = check_production_switches(repo, call)
    checks["gate_state"] = check_gate_state(repo, call)
    checks["snapshot_recovery"] = check_snapshot_recovery(
        repo, call, checks["gate_state"].get("evidence") or {})

    unreadable = any(c.get("unreadable") for c in checks.values())
    all_ok = all(c.get("ok") for c in checks.values())
    if all_ok:
        decision = "READY"
    elif unreadable:
        decision = "UNVERIFIABLE"
    else:
        decision = "BLOCKED"
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(all_ok),
        "decision": decision,
        "repo": repo,
        "target_branch": target_branch,
        "checks": {k: checks[k] for k in CHECK_ORDER},
        "blocked_checks": [k for k in CHECK_ORDER if not checks[k].get("ok")],
        "writes_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Route A / D-15 生产前置条件只读检查")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--target-branch", default="main")
    ap.add_argument("--json", default="")
    ap.add_argument("--root", default=".")
    ap.add_argument("--api", default="https://api.github.com")
    args = ap.parse_args(argv)

    if not args.repo or "/" not in args.repo:
        print("错误：--repo 必须是 owner/name（或设置 GITHUB_REPOSITORY）", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("错误：需要 GITHUB_TOKEN（只读即可）", file=sys.stderr)
        return 2

    try:
        report = build_report(
            repo=args.repo, target_branch=args.target_branch,
            call=lambda path: _http_get(path, token, api=args.api), root=args.root)
    except TransportError as exc:
        report = {"schema_version": SCHEMA_VERSION, "ok": False, "decision": "UNVERIFIABLE",
                  "repo": args.repo, "target_branch": args.target_branch,
                  "checks": {k: _check(False, ["TRANSPORT_ERROR"], {"error": str(exc)})
                             for k in CHECK_ORDER},
                  "blocked_checks": list(CHECK_ORDER), "writes_performed": False}
        _emit(report, args.json)
        print(f"❌ 无法读取平台状态：{exc}", file=sys.stderr)
        return 3
    except InputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    _emit(report, args.json)
    for name in CHECK_ORDER:
        c = report["checks"][name]
        mark = "✅" if c["ok"] else "❌"
        print(f"{mark} {name}: {', '.join(c['reason_codes']) or 'ok'}")
    print(f"\n决策：{report['decision']}（writes_performed=False）")
    return {"READY": 0, "BLOCKED": 1, "UNVERIFIABLE": 3}[report["decision"]]


def _emit(report: dict, path: str) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


if __name__ == "__main__":
    sys.exit(main())
