#!/usr/bin/env python3
"""recover_gate_state.py — D-15 B：从已验证快照恢复状态（只新增 generation）。

只做三件事：读当前状态 → 校验目标快照 → 以 CAS 写入 generation+1。
任何不确定都拒绝（fail-closed），绝不覆盖现有状态、绝不 force 更新 ref。

用法：
  python3 scripts/ci/recover_gate_state.py --repo owner/name \
      --from-generation 6 --expected-current-generation 6 [--dry-run]

退出码：0 成功（或 dry-run 通过）；1 拒绝/失败（原因见输出）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_state as gs  # noqa: E402


class RecoveryRefused(RuntimeError):
    pass


def _load_snapshot(store, generation: int) -> dict:
    raw = store.snapshot_get(generation)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecoveryRefused(f"快照 gen-{generation} 不是合法 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise RecoveryRefused(f"快照 gen-{generation} 不是对象")
    for key in ("schema_version", "generation", "state"):
        if key not in doc:
            raise RecoveryRefused(f"快照 gen-{generation} 缺少字段 {key}")
    if doc["schema_version"] != gs.SCHEMA_VERSION:
        raise RecoveryRefused(f"快照 schema_version={doc['schema_version']} 不兼容")
    if doc["generation"] != generation:
        raise RecoveryRefused("快照 generation 与请求不一致")
    problems = gs.validate_doc(doc["state"])
    if problems:
        raise RecoveryRefused("快照内状态非法: " + "; ".join(problems))
    recorded = doc.get("digest", "")
    body = {k: v for k, v in doc.items() if k != "digest"}
    actual = "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if recorded != actual:
        raise RecoveryRefused("快照 digest 不符（可能被篡改或损坏）")
    return doc


def recover(store, *, from_generation: int, expected_current: int, dry_run: bool) -> dict:
    current, ref_sha = store.read()
    if current is None:
        raise RecoveryRefused("当前状态不存在：拒绝用快照凭空创建")
    if current.generation != expected_current:
        raise RecoveryRefused(
            f"当前 generation={current.generation} 与 --expected-current-generation={expected_current} 不一致")
    if from_generation >= current.generation:
        raise RecoveryRefused("目标快照必须来自更早的 generation（防止重放/回退）")
    snap = _load_snapshot(store, from_generation)
    base_state = gs.GateState.from_doc(snap["state"])
    if base_state.run_id == current.run_id and base_state.epoch >= current.epoch:
        raise RecoveryRefused("检测到恢复重放：同 run_id 的 epoch 未推进")

    new_state = gs.GateState(
        run_id=current.run_id, generation=current.generation + 1, epoch=max(base_state.epoch, current.epoch) + 1,
        base_sha=current.base_sha, candidate_sha=current.candidate_sha, target_ref=current.target_ref,
        phase=base_state.phase, check_status=base_state.check_status, conclusion=base_state.conclusion,
        lock_owner="", lock_token="", lease_until=0.0,
        snapshot_digest="", updated_at=0.0)
    doc = new_state.to_doc()
    doc["snapshot_digest"] = gs.canonical_digest(doc)
    new_state = gs.GateState.from_doc(doc)

    if dry_run:
        return {"dry_run": True, "would_write": new_state.to_doc(), "expected_old_sha": ref_sha}

    result = store.cas_write(new_state, expected_old_sha=ref_sha or None)
    if not result.ok:
        raise RecoveryRefused(f"CAS 写入失败: {result.reason}")
    reread, _ = store.read()
    if reread is None or reread.generation != new_state.generation:
        raise RecoveryRefused("写入后回读不一致")
    return {"dry_run": False, "state": reread.to_doc(), "ref_sha": result.ref_sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-15 状态恢复（只新增 generation）")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--from-generation", type=int, required=True)
    ap.add_argument("--expected-current-generation", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--local-dir", type=Path, help="仅测试：使用本地后端")
    args = ap.parse_args(argv)

    if args.local_dir:
        store = gs.LocalFileStore(args.local_dir)
    else:
        import os
        store = gs.GitHubRefStore(args.repo, os.environ.get("GITHUB_TOKEN", ""))
    try:
        out = recover(store, from_generation=args.from_generation,
                      expected_current=args.expected_current_generation, dry_run=args.dry_run)
    except (RecoveryRefused, gs.StateError) as exc:
        print(f"❌ 拒绝恢复: {exc}")
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
