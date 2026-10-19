#!/usr/bin/env python3
"""gate_lock.py — D-15 接入日常同步流程的 CLI（逻辑锁 + 状态机 + 持久快照）。

子命令：

* ``status``  只读当前状态（不写）。
* ``acquire`` 获取逻辑锁：读当前状态 → 到期/占用检查 → CAS 写入 generation+1 并落快照。
* ``complete`` 以持有者身份写终态（success/failure），清锁；**拒绝清除 paused**。
* ``pause``   显式暂停（保留证据），清锁但 phase=paused。
* ``resume``  从 paused 恢复（必须给 --run-id 与 --reason，写新 generation）。

退出码：0 成功；1 参数/状态非法；3 锁被占用；4 CAS 冲突；5 需要人工处理（paused/证据缺失）。
所有写入都用 CAS（expected old SHA + force=false 语义）；任何不确定都 fail-closed。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_state as gs  # noqa: E402

EXIT_OK, EXIT_INVALID, EXIT_BUSY, EXIT_CONFLICT, EXIT_NEEDS_HUMAN = 0, 1, 3, 4, 5


def _store(args):
    if args.local_dir:
        return gs.LocalFileStore(Path(args.local_dir))
    return gs.GitHubRefStore(args.repo, os.environ.get("GITHUB_TOKEN", ""))


def _fingerprint(state: gs.GateState) -> dict:
    return {"run_id": state.run_id, "generation": state.generation, "epoch": state.epoch,
            "phase": state.phase, "check_status": state.check_status}


def _snapshot_digest(generation: int, state_doc: dict, snap_run_id: str, snap_candidate_sha: str) -> str:
    """摘要覆盖状态及外层身份绑定；meta 仍排除以保留同状态幂等性。"""
    import hashlib
    semantic = {k: v for k, v in (state_doc or {}).items()
                if k not in ("snapshot_digest", "updated_at")}
    canonical = json.dumps({"schema_version": gs.SCHEMA_VERSION, "generation": generation,
                            "snap_run_id": snap_run_id,
                            "snap_candidate_sha": snap_candidate_sha,
                            "state": semantic}, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _snapshot(store, state: gs.GateState, extra: dict) -> str:
    """写快照；``SnapshotExists`` 只表示**同代对象已存在**，必须回读核对状态。

    * 回读后 digest 与 state 规范化内容完全一致 ⇒ 幂等成功（同代重跑）。
    * 读取失败 / JSON 损坏 / digest 不符 / state 不同 ⇒ ``SNAPSHOT_CONFLICT``
      fail-closed，绝不静默复用可能属于另一份 payload 的快照。
    """
    import hashlib
    state_doc = state.to_doc()
    body = {"schema_version": gs.SCHEMA_VERSION, "generation": state.generation,
            "state": state_doc, "meta": extra,
            "snap_run_id": state.run_id,
            "snap_candidate_sha": state.candidate_sha,
            "digest": _snapshot_digest(state.generation, state_doc, state.run_id, state.candidate_sha)}
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    try:
        return store.snapshot_put(state, data)
    except gs.SnapshotExists:
        try:
            existing = store.snapshot_get(state.generation)
        except gs.StateError as exc:
            raise gs.StateError(
                f"SNAPSHOT_UNVERIFIABLE: gen-{state.generation} 已存在但无法回读（{exc}）") from exc
        try:
            doc = json.loads(existing.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise gs.StateError(
                f"SNAPSHOT_UNVERIFIABLE: gen-{state.generation} 内容不可解析（{exc}）") from exc
        recorded = doc.get("digest") if isinstance(doc, dict) else None
        state_now = doc.get("state") if isinstance(doc, dict) else None
        if not isinstance(recorded, str) or recorded != _snapshot_digest(
                state.generation, state_now or {}, doc.get("snap_run_id", ""),
                doc.get("snap_candidate_sha", "")):
            raise gs.StateError(
                f"SNAPSHOT_CONFLICT: gen-{state.generation} 已有快照的 digest 与自身内容不符"
                f"（recorded={recorded!r}）")
        if recorded != body["digest"]:
            raise gs.StateError(
                f"SNAPSHOT_CONFLICT: gen-{state.generation} 已存在但状态不同"
                f"（existing={recorded} wanted={body['digest']}）")
        return f"existing-same:{hashlib.sha256(data).hexdigest()}"


def _write(store, state: gs.GateState, old_sha: str | None, *, run_id: str,
           reject_uncertain: bool = False) -> int:
    doc = state.to_doc()
    doc["snapshot_digest"] = gs.canonical_digest(doc)
    state = gs.GateState.from_doc(doc)
    try:
        result = store.cas_write(state, expected_old_sha=old_sha or None)
    except gs.CasConflict as exc:
        print(f"❌ CAS 冲突（{run_id}）：{exc}", file=sys.stderr)
        return EXIT_CONFLICT
    except gs.StateError as exc:
        # API 超时/网络错误：写入可能已生效 —— 先回读核对，不盲目重试
        try:
            current, _ = store.read()
        except gs.StateError:
            print(f"❌ 写入结果未知且状态不可读（fail-closed）：{exc}", file=sys.stderr)
            return EXIT_INVALID
        if (current is not None and current.generation == state.generation
                and current.run_id == state.run_id and current.phase == state.phase):
            if reject_uncertain:
                print(f"❌ 写入响应不确定：确认操作一律不把超时对账成成功（fail-closed）：{exc}", file=sys.stderr)
                return EXIT_INVALID
            print(f"⚠️ 写入报错但回读确认已生效（幂等接受）：{exc}", file=sys.stderr)
            print(json.dumps({"ok": True, "reconciled": True, "generation": state.generation,
                              "phase": state.phase}, ensure_ascii=False))
            return EXIT_OK
        print(f"❌ 写入失败且回读不一致（fail-closed）：{exc}", file=sys.stderr)
        return EXIT_INVALID
    if not result.ok:
        print(f"❌ 写入被拒绝：{result.reason}", file=sys.stderr)
        return EXIT_CONFLICT
    print(json.dumps({"ok": True, "generation": state.generation, "phase": state.phase,
                      "ref_sha": result.ref_sha}, ensure_ascii=False))
    return EXIT_OK


def cmd_require_enabled(args) -> int:
    """P0-2：状态锁是安全前置条件。未显式 true 时不得继续产生任何副作用。"""
    raw = os.environ.get("GATE_STATE_ENABLED", "")
    if raw.strip().lower() != "true":
        print(f"❌ GATE_STATE_ENABLED={raw!r}：状态锁未启用，拒绝继续同步（fail-closed）",
              file=sys.stderr)
        return EXIT_INVALID
    print("✅ GATE_STATE_ENABLED=true")
    return EXIT_OK


def _commit(store, state: gs.GateState, old_sha: str | None, meta: dict, *, run_id: str,
            reject_uncertain: bool = False) -> int:
    """先落快照、再 CAS 写状态：快照失败则不改变任何状态。"""
    try:
        _snapshot(store, state, meta)
    except gs.StateError as exc:
        print(f"❌ 快照写入失败，状态未变更（fail-closed）：{exc}", file=sys.stderr)
        return EXIT_INVALID
    return _write(store, state, old_sha, run_id=run_id, reject_uncertain=reject_uncertain)


def cmd_require_usable(args) -> int:
    """确认当前状态允许继续同步（非 paused / 非 recovery-required）。"""
    state, _ = _store(args).read()
    if state is None:
        print("✅ 无既有状态（首次运行）")
        return EXIT_OK
    if state.phase in gs.BLOCKING_PHASES:
        print(f"❌ 状态为 {state.phase}：禁止继续同步（需人工 resume）", file=sys.stderr)
        return EXIT_NEEDS_HUMAN
    if state.notice_digest and not state.notice_acked:
        print(f"❌ 存在未确认的关键通知（notice_digest={state.notice_digest[:16]}...）："
              f"请先执行 confirm-notice 或 pause --recovery", file=sys.stderr)
        return EXIT_NEEDS_HUMAN
    print(f"✅ 状态可用（phase={state.phase}, generation={state.generation}）")
    return EXIT_OK


def cmd_status(args) -> int:
    state, sha = _store(args).read()
    print(json.dumps({"state": state.to_doc() if state else None, "ref_sha": sha}, ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_acquire(args) -> int:
    store = _store(args)
    current, sha = store.read()
    now = time.time()
    if current is not None:
        if current.phase in gs.BLOCKING_PHASES:
            print(f"❌ 状态处于 {current.phase}：必须先由人工 resume（普通运行不得继续）",
                  file=sys.stderr)
            return EXIT_NEEDS_HUMAN
        expired = bool(current.lock_owner) and current.lease_until <= now \
            and current.lock_owner != f"gate-{args.run_id}"
        if current.lock_owner and current.lease_until > now and current.lock_owner != f"gate-{args.run_id}":
            print(f"❌ 锁被 {current.lock_owner} 持有（lease 至 {current.lease_until:.0f}），拒绝并发运行",
                  file=sys.stderr)
            return EXIT_BUSY
        if expired and not args.takeover:
            print(f"❌ 租约已过期但未授权接管（原持有者 {current.lock_owner}）："
                  f"需人工确认后用 --takeover", file=sys.stderr)
            return EXIT_NEEDS_HUMAN
        # 为**旧状态**落快照，供日后恢复（失败即中止）
        try:
            _snapshot(store, current, {"kind": "pre-acquire", "by": args.run_id})
        except gs.StateError as exc:
            print(f"❌ 旧状态快照失败，拒绝继续（fail-closed）：{exc}", file=sys.stderr)
            return EXIT_INVALID
        takeover = expired
    else:
        takeover = False

    generation = 1 if current is None else current.generation + 1
    state = gs.GateState(
        run_id=args.run_id, generation=generation,
        epoch=(0 if current is None else current.epoch + (1 if takeover else 0)),
        base_sha=args.base_sha, candidate_sha=args.candidate_sha or args.base_sha,
        target_ref=args.target_ref, phase="candidate", check_status="pending", conclusion="none",
        lock_owner=f"gate-{args.run_id}", lock_token=os.urandom(16).hex(),
        lease_until=now + args.lease_seconds, updated_at=now)
    return _commit(store, state, sha, {"kind": "acquired", "trigger": args.trigger,
                                       "takeover": takeover,
                                       "previous_owner": (current.lock_owner if current else "")},
                   run_id=args.run_id)


def cmd_complete(args) -> int:
    store = _store(args)
    current, sha = store.read()
    if current is None:
        print("❌ 无当前状态", file=sys.stderr)
        return EXIT_INVALID
    if current.phase in gs.BLOCKING_PHASES:
        print(f"❌ 状态为 {current.phase}：complete 永远不能清除 PAUSED/RECOVERY_REQUIRED，"
              f"只能用 resume 子命令正式恢复", file=sys.stderr)
        return EXIT_NEEDS_HUMAN
    if current.notice_digest and not current.notice_acked:
        print(f"❌ 存在未确认的关键通知：必须先 confirm-notice 或 pause --recovery", file=sys.stderr)
        return EXIT_NEEDS_HUMAN
    if current.lock_owner != f"gate-{args.run_id}":
        print(f"❌ 非锁持有者（当前 {current.lock_owner!r}）", file=sys.stderr)
        return EXIT_INVALID
    success = args.result == "success"
    state = gs.GateState(
        run_id=current.run_id, generation=current.generation + 1, epoch=current.epoch,
        base_sha=current.base_sha, candidate_sha=args.candidate_sha or current.candidate_sha,
        target_ref=current.target_ref, phase="final-review" if success else "failed",
        check_status="completed" if success else "failure",
        conclusion="success" if success else "failure",
        lock_owner="", lock_token="", lease_until=0.0, updated_at=time.time(),
        notice_digest="", notice_acked=False, notice_acked_by="", notice_acked_at=0.0)
    return _commit(store, state, sha,
                   {"kind": "completed", "result": args.result, "pr": args.pr}, run_id=args.run_id)


def cmd_mark_notice_needed(args) -> int:
    """U-09：标记当前运行需要关键通知确认（设置 notice_digest）。"""
    store = _store(args)
    current, sha = store.read()
    if current is None:
        print("❌ 无当前状态", file=sys.stderr)
        return EXIT_INVALID
    if current.phase in gs.BLOCKING_PHASES:
        print(f"❌ 状态为 {current.phase}：禁止标记通知", file=sys.stderr)
        return EXIT_NEEDS_HUMAN
    if current.lock_owner != f"gate-{args.run_id}":
        print(f"❌ 非锁持有者（当前 {current.lock_owner!r}）", file=sys.stderr)
        return EXIT_INVALID
    state = gs.GateState(
        run_id=current.run_id, generation=current.generation + 1, epoch=current.epoch,
        base_sha=current.base_sha, candidate_sha=current.candidate_sha,
        target_ref=current.target_ref, phase=current.phase,
        check_status=current.check_status, conclusion=current.conclusion,
        lock_owner=current.lock_owner, lock_token=current.lock_token,
        lease_until=current.lease_until, snapshot_digest="",
        notice_digest=args.digest, notice_acked=False,
        notice_acked_by="", notice_acked_at=0.0,
        updated_at=time.time())
    return _commit(store, state, sha,
                   {"kind": "notice-marked", "digest": args.digest}, run_id=args.run_id)


def confirm_notice(store, *, notice_run_id: str, notice_digest: str, expected_generation: int,
                   actor_id: int, actor_login: str, actor_type: str, now: float | None = None,
                   strict_write: bool = False):
    """Perform a state transition only after the caller supplies verified actor data."""
    current, sha = store.read()
    evidence = {"ok": False, "notice_run_id": notice_run_id, "notice_digest": notice_digest,
                "expected_generation": expected_generation, "generation_before": None,
                "generation_after": None, "actor_id": actor_id, "actor_login": actor_login,
                "actor_type": actor_type, "decision": "reject", "reason": "", "state_written": False}
    def reject(reason, code=EXIT_INVALID):
        evidence["reason"] = reason
        print(json.dumps(evidence, ensure_ascii=False))
        return code
    if current is None:
        return reject("STATE_MISSING")
    evidence["generation_before"] = current.generation
    if (not isinstance(notice_run_id, str) or not re.fullmatch(r"[1-9][0-9]*(?:-[1-9][0-9]*)?", notice_run_id)
            or not isinstance(notice_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", notice_digest)
            or not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool) or expected_generation < 1):
        return reject("REQUEST_INVALID")
    if actor_id != 152380613 or actor_login != "Hawaiine" or actor_type != "User":
        return reject("ACTOR_NOT_AUTHORIZED", EXIT_NEEDS_HUMAN)
    if not strict_write:
        return reject("TRUSTED_EVENT_ATTESTATION_REQUIRED", EXIT_NEEDS_HUMAN)
    if current.generation != expected_generation:
        return reject("GENERATION_MISMATCH")
    if current.run_id != notice_run_id:
        return reject("NOTICE_RUN_ID_MISMATCH")
    if current.notice_digest != notice_digest:
        return reject("NOTICE_DIGEST_MISMATCH")
    if not current.notice_digest or current.notice_acked:
        return reject("NOTICE_NOT_PENDING")
    if current.phase in gs.BLOCKING_PHASES:
        return reject("PHASE_BLOCKS_CONFIRMATION", EXIT_NEEDS_HUMAN)
    after = current.generation + 1
    stamp = now or time.time()
    state = dataclasses.replace(current, generation=after, updated_at=stamp,
        notice_acked=True, notice_acked_by=f"{actor_login} ({actor_id})", notice_acked_at=stamp,
        notice_acked_by_id=actor_id, notice_acked_by_login=actor_login,
        notice_acked_run_id=notice_run_id, notice_acked_digest=notice_digest,
        notice_acked_generation_from=current.generation, notice_acked_generation_to=after)
    rc = _commit(store, state, sha, {"kind": "notice-confirmed", "actor_id": actor_id,
        "actor_login": actor_login, "notice_run_id": notice_run_id, "notice_digest": notice_digest},
        run_id=f"notice-{notice_run_id}", reject_uncertain=True)
    if rc != EXIT_OK:
        return reject("SNAPSHOT_OR_CAS_WRITE_FAILED", rc)
    evidence.update(ok=True, decision="confirm", generation_after=after, state_written=True, reason="CONFIRMED")
    print(json.dumps(evidence, ensure_ascii=False))
    return EXIT_OK


def cmd_confirm_notice(args) -> int:
    """CLI has no trusted event attestation path; refuse user-supplied identity claims."""
    evidence = {"ok": False, "decision": "reject", "reason": "TRUSTED_EVENT_ATTESTATION_REQUIRED",
                "notice_run_id": args.notice_run_id, "notice_digest": args.digest,
                "expected_generation": args.expected_generation, "state_written": False}
    print(json.dumps(evidence, ensure_ascii=False))
    return EXIT_NEEDS_HUMAN



def cmd_pause(args) -> int:
    store = _store(args)
    current, sha = store.read()
    if current is None:
        print("❌ 无当前状态", file=sys.stderr)
        return EXIT_INVALID
    state = gs.GateState(
        run_id=current.run_id, generation=current.generation + 1, epoch=current.epoch,
        base_sha=current.base_sha, candidate_sha=current.candidate_sha, target_ref=current.target_ref,
        phase="recovery-required" if args.recovery else "paused",
        check_status="failure", conclusion="failure",
        lock_owner="", lock_token="", lease_until=0.0, updated_at=time.time(),
        notice_digest="", notice_acked=False, notice_acked_by="", notice_acked_at=0.0)
    return _commit(store, state, sha,
                   {"kind": "paused", "reason": args.reason, "evidence": args.evidence},
                   run_id=args.run_id)


def cmd_resume(args) -> int:
    store = _store(args)
    current, sha = store.read()
    if current is None or current.phase not in gs.BLOCKING_PHASES:
        print("❌ 仅能从 paused/recovery-required 恢复", file=sys.stderr)
        return EXIT_INVALID
    state = gs.GateState(
        run_id=args.run_id, generation=current.generation + 1, epoch=current.epoch + 1,
        base_sha=current.base_sha, candidate_sha=current.candidate_sha, target_ref=current.target_ref,
        phase="candidate", check_status="pending", conclusion="none",
        lock_owner=f"gate-{args.run_id}", lock_token=os.urandom(16).hex(),
        lease_until=time.time() + args.lease_seconds, updated_at=time.time())
    return _commit(store, state, sha, {"kind": "resumed", "reason": args.reason},
                   run_id=args.run_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-15 逻辑锁/状态机 CLI")
    ap.add_argument("--repo")
    ap.add_argument("--local-dir", type=Path, help="仅测试：本地后端")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    ap.add_argument("--lease-seconds", type=float, default=1800)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("require-enabled")
    sub.add_parser("require-usable")

    a = sub.add_parser("acquire")
    a.add_argument("--base-sha", required=True)
    a.add_argument("--candidate-sha", default="")
    a.add_argument("--target-ref", default="refs/heads/candidate/rules-update")
    a.add_argument("--trigger", default="schedule")
    a.add_argument("--takeover", action="store_true",
                   help="显式接管**已过期**的租约（新 generation 且 epoch+1，记录原持有者）")

    c = sub.add_parser("complete")
    c.add_argument("--result", choices=("success", "failure"), required=True)
    c.add_argument("--candidate-sha", default="")
    c.add_argument("--pr", default="")

    pa = sub.add_parser("pause")
    pa.add_argument("--reason", required=True)
    pa.add_argument("--evidence", default="")
    pa.add_argument("--recovery", action="store_true",
                    help="标记为 recovery-required（证据/状态冲突，需人工介入）")

    mn = sub.add_parser("mark-notice-needed")
    mn.add_argument("--digest", required=True, help="待确认通知的规范摘要")

    cn = sub.add_parser("confirm-notice")
    cn.add_argument("--notice-run-id", required=True)
    cn.add_argument("--digest", required=True, help="与 mark-notice-needed 使用的 digest 精确匹配")
    cn.add_argument("--expected-generation", required=True, type=int)

    r = sub.add_parser("resume")
    r.add_argument("--reason", required=True)

    args = ap.parse_args(argv)
    if not args.local_dir and not args.repo:
        print("❌ 需要 --repo（或 --local-dir）", file=sys.stderr)
        return EXIT_INVALID
    if args.cmd not in ("status", "require-enabled", "require-usable") and not args.run_id:
        print("❌ 需要 --run-id（或 GITHUB_RUN_ID）", file=sys.stderr)
        return EXIT_INVALID
    handler = {"status": cmd_status, "require-enabled": cmd_require_enabled,
               "require-usable": cmd_require_usable, "acquire": cmd_acquire, "complete": cmd_complete,
               "pause": cmd_pause, "resume": cmd_resume,
               "mark-notice-needed": cmd_mark_notice_needed,
               "confirm-notice": cmd_confirm_notice}[args.cmd]
    try:
        return handler(args)
    except gs.StateError as exc:
        print(f"❌ 状态后端错误（fail-closed）：{exc}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
