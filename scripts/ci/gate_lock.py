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
import json
import os
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


def _snapshot(store, state: gs.GateState, extra: dict) -> str:
    body = {"schema_version": gs.SCHEMA_VERSION, "generation": state.generation,
            "state": state.to_doc(), "meta": extra}
    import hashlib
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    body["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    try:
        return store.snapshot_put(state, data)
    except gs.StateError as exc:              # 快照已存在（同代重跑）→ 不算失败
        return f"existing:{exc}"


def _write(store, state: gs.GateState, old_sha: str | None, *, run_id: str) -> int:
    doc = state.to_doc()
    doc["snapshot_digest"] = gs.canonical_digest(doc)
    state = gs.GateState.from_doc(doc)
    try:
        result = store.cas_write(state, expected_old_sha=old_sha or None)
    except gs.CasConflict as exc:
        print(f"❌ CAS 冲突（{run_id}）：{exc}", file=sys.stderr)
        return EXIT_CONFLICT
    if not result.ok:
        print(f"❌ 写入被拒绝：{result.reason}", file=sys.stderr)
        return EXIT_CONFLICT
    print(json.dumps({"ok": True, "generation": state.generation, "phase": state.phase,
                      "ref_sha": result.ref_sha}, ensure_ascii=False))
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
        if current.phase == "paused":
            print("❌ 状态处于 paused：必须先 resume（不允许普通运行清除暂停）", file=sys.stderr)
            return EXIT_NEEDS_HUMAN
        if current.lock_owner and current.lease_until > now and current.lock_owner != args.run_id:
            print(f"❌ 锁被 {current.lock_owner} 持有（lease 至 {current.lease_until:.0f}），拒绝并发运行",
                  file=sys.stderr)
            return EXIT_BUSY
        # 为**旧状态**落快照，供日后恢复
        _snapshot(store, current, {"kind": "pre-acquire"})

    generation = 1 if current is None else current.generation + 1
    state = gs.GateState(
        run_id=args.run_id, generation=generation, epoch=0 if current is None else current.epoch,
        base_sha=args.base_sha, candidate_sha=args.candidate_sha or args.base_sha,
        target_ref=args.target_ref, phase="candidate", check_status="pending", conclusion="none",
        lock_owner=f"gate-{args.run_id}", lock_token=os.urandom(16).hex(),
        lease_until=now + args.lease_seconds, updated_at=now)
    rc = _write(store, state, sha, run_id=args.run_id)
    if rc == EXIT_OK:
        _snapshot(store, state, {"kind": "acquired", "trigger": args.trigger})
    return rc


def cmd_complete(args) -> int:
    store = _store(args)
    current, sha = store.read()
    if current is None:
        print("❌ 无当前状态", file=sys.stderr)
        return EXIT_INVALID
    if current.phase == "paused" and not args.resume_paused:
        print("❌ 状态为 paused：普通完成路径不得清除暂停（需 --resume-paused 或 resume 子命令）",
              file=sys.stderr)
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
        lock_owner="", lock_token="", lease_until=0.0, updated_at=time.time())
    rc = _write(store, state, sha, run_id=args.run_id)
    if rc == EXIT_OK:
        _snapshot(store, state, {"kind": "completed", "result": args.result, "pr": args.pr})
    return rc


def cmd_pause(args) -> int:
    store = _store(args)
    current, sha = store.read()
    if current is None:
        print("❌ 无当前状态", file=sys.stderr)
        return EXIT_INVALID
    state = gs.GateState(
        run_id=current.run_id, generation=current.generation + 1, epoch=current.epoch,
        base_sha=current.base_sha, candidate_sha=current.candidate_sha, target_ref=current.target_ref,
        phase="paused", check_status="failure", conclusion="failure",
        lock_owner="", lock_token="", lease_until=0.0, updated_at=time.time())
    rc = _write(store, state, sha, run_id=args.run_id)
    if rc == EXIT_OK:
        _snapshot(store, state, {"kind": "paused", "reason": args.reason,
                                 "evidence": args.evidence})
    return rc


def cmd_resume(args) -> int:
    store = _store(args)
    current, sha = store.read()
    if current is None or current.phase != "paused":
        print("❌ 仅能从 paused 恢复", file=sys.stderr)
        return EXIT_INVALID
    state = gs.GateState(
        run_id=args.run_id, generation=current.generation + 1, epoch=current.epoch + 1,
        base_sha=current.base_sha, candidate_sha=current.candidate_sha, target_ref=current.target_ref,
        phase="candidate", check_status="pending", conclusion="none",
        lock_owner=f"gate-{args.run_id}", lock_token=os.urandom(16).hex(),
        lease_until=time.time() + args.lease_seconds, updated_at=time.time())
    rc = _write(store, state, sha, run_id=args.run_id)
    if rc == EXIT_OK:
        _snapshot(store, state, {"kind": "resumed", "reason": args.reason})
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-15 逻辑锁/状态机 CLI")
    ap.add_argument("--repo")
    ap.add_argument("--local-dir", type=Path, help="仅测试：本地后端")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    ap.add_argument("--lease-seconds", type=float, default=1800)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    a = sub.add_parser("acquire")
    a.add_argument("--base-sha", required=True)
    a.add_argument("--candidate-sha", default="")
    a.add_argument("--target-ref", default="refs/heads/candidate/rules-update")
    a.add_argument("--trigger", default="schedule")

    c = sub.add_parser("complete")
    c.add_argument("--result", choices=("success", "failure"), required=True)
    c.add_argument("--candidate-sha", default="")
    c.add_argument("--pr", default="")
    c.add_argument("--resume-paused", action="store_true")

    pa = sub.add_parser("pause")
    pa.add_argument("--reason", required=True)
    pa.add_argument("--evidence", default="")

    r = sub.add_parser("resume")
    r.add_argument("--reason", required=True)

    args = ap.parse_args(argv)
    if not args.local_dir and not args.repo:
        print("❌ 需要 --repo（或 --local-dir）", file=sys.stderr)
        return EXIT_INVALID
    if args.cmd != "status" and not args.run_id:
        print("❌ 需要 --run-id（或 GITHUB_RUN_ID）", file=sys.stderr)
        return EXIT_INVALID
    handler = {"status": cmd_status, "acquire": cmd_acquire, "complete": cmd_complete,
               "pause": cmd_pause, "resume": cmd_resume}[args.cmd]
    try:
        return handler(args)
    except gs.StateError as exc:
        print(f"❌ 状态后端错误（fail-closed）：{exc}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
