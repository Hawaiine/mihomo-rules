#!/usr/bin/env python3
"""decide_merge.py — 自动合并决策矩阵（**只出决策，不执行合并**）。

输入全部来自可信读取；任何缺失/冲突都返回 ``BLOCKED`` 与稳定 reason code。
``production_mode=False`` 时，即使 ``ELIGIBLE_*`` 也只表示"具备资格"，不产生任何写操作。
"""
from __future__ import annotations

import argparse
import json
import sys

from classify_change import (EXISTING_DATA_ONLY, GOVERNANCE_CHANGE, MIXED_OR_UNKNOWN,
                             NEW_RULESET)

BLOCKED = "BLOCKED"
ELIGIBLE_EXISTING_DATA = "ELIGIBLE_EXISTING_DATA"
ELIGIBLE_NEW_RULESET_AFTER_APPROVAL = "ELIGIBLE_NEW_RULESET_AFTER_APPROVAL"

REASON_HEAD_MISMATCH = "HEAD_SHA_MISMATCH"
REASON_BASE_DRIFT = "BASE_SHA_DRIFT"
REASON_CHECKS_NOT_OK = "CHECKS_NOT_OK"
REASON_LOCK_NOT_HELD = "LOCK_NOT_HELD"
REASON_LOCK_PAUSED = "LOCK_PAUSED"
REASON_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
REASON_SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
REASON_APPROVAL_MISSING = "APPROVAL_MISSING"
REASON_APPROVAL_STALE = "APPROVAL_STALE"
REASON_CHANGE_GOVERNANCE = "CHANGE_GOVERNANCE"
REASON_CHANGE_UNKNOWN = "CHANGE_UNKNOWN"
REASON_PR_NOT_OPEN = "PR_NOT_OPEN"
REASON_PLATFORM_NOT_READY = "PLATFORM_NOT_READY"
REASON_PRODUCTION_OFF = "PRODUCTION_DISABLED"


def decide(*, change_class: str, checks_ok: bool, head_sha_ok: bool, base_sha_ok: bool = True,
           approval_ok: bool = False, approval_reasons: list[str] | None = None,
           lock_state: str = "idle", pr_open: bool = True, platform_ok: bool = True,
           production_mode: bool = False) -> dict:
    """返回 {decision, reason_codes, production_allowed}。

    ``lock_state`` 取值：``idle``（无锁）/ ``held``（本 run 持有）/ ``paused`` /
    ``recovery_required`` / ``snapshot_failed`` / ``unknown``。
    """
    reasons: list[str] = []

    def block(*codes: str) -> dict:
        return {"decision": BLOCKED, "reason_codes": reasons + list(codes),
                "production_allowed": False}

    if not pr_open:
        return block(REASON_PR_NOT_OPEN)
    if not head_sha_ok:
        return block(REASON_HEAD_MISMATCH)
    if not base_sha_ok:
        return block(REASON_BASE_DRIFT)
    if not checks_ok:
        return block(REASON_CHECKS_NOT_OK)
    if lock_state == "paused":
        return block(REASON_LOCK_PAUSED)
    if lock_state == "recovery_required":
        return block(REASON_RECOVERY_REQUIRED)
    if lock_state == "snapshot_failed":
        return block(REASON_SNAPSHOT_FAILED)
    if lock_state != "held":
        return block(REASON_LOCK_NOT_HELD)
    if change_class == GOVERNANCE_CHANGE:
        return block(REASON_CHANGE_GOVERNANCE)
    if change_class == MIXED_OR_UNKNOWN:
        return block(REASON_CHANGE_UNKNOWN)
    if not platform_ok:
        return block(REASON_PLATFORM_NOT_READY)

    if change_class == NEW_RULESET:
        if not approval_ok:
            return block(*(approval_reasons or [REASON_APPROVAL_MISSING]))
        return {"decision": ELIGIBLE_NEW_RULESET_AFTER_APPROVAL,
                "reason_codes": reasons, "production_allowed": production_mode}

    if change_class == EXISTING_DATA_ONLY:
        out = {"decision": ELIGIBLE_EXISTING_DATA, "reason_codes": reasons,
               "production_allowed": production_mode}
        if not production_mode:
            out["reason_codes"] = [REASON_PRODUCTION_OFF]
        return out

    return block(REASON_CHANGE_UNKNOWN)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="自动合并决策（只出决策）")
    ap.add_argument("--change-class", required=True,
                    choices=[EXISTING_DATA_ONLY, NEW_RULESET, GOVERNANCE_CHANGE, MIXED_OR_UNKNOWN])
    ap.add_argument("--checks-ok", type=int, choices=[0, 1], required=True)
    ap.add_argument("--head-ok", type=int, choices=[0, 1], required=True)
    ap.add_argument("--base-ok", type=int, choices=[0, 1], default=1)
    ap.add_argument("--approval-ok", type=int, choices=[0, 1], default=0)
    ap.add_argument("--lock-state", default="idle",
                    choices=["idle", "held", "paused", "recovery_required", "snapshot_failed", "unknown"])
    ap.add_argument("--pr-open", type=int, choices=[0, 1], default=1)
    ap.add_argument("--platform-ok", type=int, choices=[0, 1], default=1)
    ap.add_argument("--production-mode", type=int, choices=[0, 1], default=0)
    args = ap.parse_args(argv)
    out = decide(change_class=args.change_class, checks_ok=bool(args.checks_ok),
                 head_sha_ok=bool(args.head_ok), base_sha_ok=bool(args.base_ok),
                 approval_ok=bool(args.approval_ok), lock_state=args.lock_state,
                 pr_open=bool(args.pr_open), platform_ok=bool(args.platform_ok),
                 production_mode=bool(args.production_mode))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["decision"] != BLOCKED else 1


if __name__ == "__main__":
    sys.exit(main())
