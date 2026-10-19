#!/usr/bin/env python3
"""approval_policy.py — 显式人工审批策略（单一来源；缺失即 fail-closed）。

为什么需要它：审批人此前由 `base.repo.owner.login` 推断。仓库转移、改名或所有权变更会
**静默**改变"谁有权批准新增规则集"，而这一语义必须可审计、可复核。本模块只从仓库内显式
配置文件读取审批策略，不猜测、不回退到 owner、不接受空名单、不接受自动化账号。

策略文件（默认 `.github/approval-policy.json`）字段全部必填，未知字段一律拒绝：

```
{
  "schema": "mihomo-rules-approval-policy",
  "version": 1,
  "approvers": ["<github-login>", ...],     # 允许批准新增规则集的人类账号
  "required_approvals": 1,                  # 1..len(approvers)
  "allow_bot_approvers": false,             # 必须为 false（Route A 不引入 App/bot 审批）
  "head_binding": "exact",                  # 审批必须精确绑定当前 PR head
  "on_missing": "fail-closed"                # 策略缺失/无效时的行为，必须 fail-closed
}
```

任何解析/校验失败都返回 `ok=False` 与稳定 reason code，调用方必须把它当作"审批不可用"，
而不是"审批通过"。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

SCHEMA = "mihomo-rules-approval-policy"
VERSION = 1
DEFAULT_PATH = ".github/approval-policy.json"

REQUIRED_FIELDS = ("schema", "version", "approvers", "required_approvals",
                   "allow_bot_approvers", "head_binding", "on_missing")
# GitHub 账号名：1-39 字符，字母数字与单个连字符，不能以连字符开头/结尾
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


@dataclass(frozen=True)
class Policy:
    approvers: tuple[str, ...]
    required_approvals: int
    allow_bot_approvers: bool
    head_binding: str
    on_missing: str
    source: str

    def to_dict(self) -> dict:
        return {"approvers": list(self.approvers),
                "required_approvals": self.required_approvals,
                "allow_bot_approvers": self.allow_bot_approvers,
                "head_binding": self.head_binding,
                "on_missing": self.on_missing,
                "source": self.source}


@dataclass
class PolicyResult:
    ok: bool
    policy: Policy | None = None
    reason_codes: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok,
                "policy": self.policy.to_dict() if self.policy else None,
                "reason_codes": list(self.reason_codes),
                "evidence": dict(self.evidence)}


def validate_doc(doc: object, *, source: str = "") -> PolicyResult:
    """校验已解析的策略文档。任何不满足都 fail-closed（不返回半可用策略）。"""
    problems: list[str] = []
    evidence: dict = {"source": source, "type": type(doc).__name__}
    if not isinstance(doc, dict):
        return PolicyResult(False, None, ["APPROVAL_POLICY_MALFORMED"], evidence)

    unknown = sorted(set(doc) - set(REQUIRED_FIELDS))
    if unknown:
        problems.append("APPROVAL_POLICY_UNKNOWN_FIELDS:" + ",".join(unknown))
    missing = [k for k in REQUIRED_FIELDS if k not in doc]
    if missing:
        problems.append("APPROVAL_POLICY_MISSING_FIELDS:" + ",".join(missing))

    if doc.get("schema") != SCHEMA:
        problems.append("APPROVAL_POLICY_SCHEMA_MISMATCH")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != VERSION:
        problems.append("APPROVAL_POLICY_VERSION_UNSUPPORTED")
    if doc.get("allow_bot_approvers") is not False:
        problems.append("APPROVAL_POLICY_ALLOWS_BOTS")
    if doc.get("head_binding") != "exact":
        problems.append("APPROVAL_POLICY_HEAD_BINDING_UNSUPPORTED")
    if doc.get("on_missing") != "fail-closed":
        problems.append("APPROVAL_POLICY_ON_MISSING_UNSAFE")

    raw_approvers = doc.get("approvers")
    approvers: list[str] = []
    if not isinstance(raw_approvers, list):
        problems.append("APPROVAL_POLICY_APPROVER_INVALID:not-a-list")
    else:
        for item in raw_approvers:
            if not isinstance(item, str) or not LOGIN_RE.match(item):
                problems.append(f"APPROVAL_POLICY_APPROVER_INVALID:{item!r}")
                continue
            if item in approvers:
                problems.append(f"APPROVAL_POLICY_DUPLICATE_APPROVER:{item}")
                continue
            approvers.append(item)
        if not approvers:
            # 空名单（或全部条目非法）不是"不需要审批"，而是"没有可用审批人"——
            # 必须显式阻断新增规则集路径，绝不能退化成"无需批准"。
            problems.append("APPROVAL_POLICY_UNCONFIGURED")

    required = doc.get("required_approvals")
    if not isinstance(required, int) or isinstance(required, bool) or required < 1:
        problems.append("APPROVAL_POLICY_REQUIRED_INVALID")
    elif approvers and required > len(approvers):
        problems.append(f"APPROVAL_POLICY_REQUIRED_EXCEEDS_APPROVERS:{required}>{len(approvers)}")

    required_int = required if isinstance(required, int) and not isinstance(required, bool) else 0
    evidence.update({"approvers": approvers, "required_approvals": required_int,
                     "declared_approvers": len(raw_approvers) if isinstance(raw_approvers, list) else 0})
    if problems:
        return PolicyResult(False, None, problems, evidence)
    policy = Policy(approvers=tuple(approvers), required_approvals=required_int,
                    allow_bot_approvers=False, head_binding="exact",
                    on_missing="fail-closed", source=source)
    return PolicyResult(True, policy, [], evidence)


def load_policy(path: str = DEFAULT_PATH, *, read=None) -> PolicyResult:
    """读取并校验策略文件。`read` 可注入（测试/离线复现）；默认读本地文件。"""
    evidence: dict = {"path": path}
    try:
        if read is None:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = read(path)
    except FileNotFoundError:
        return PolicyResult(False, None, ["APPROVAL_POLICY_MISSING"], evidence)
    except OSError as exc:
        return PolicyResult(False, None, [f"APPROVAL_POLICY_UNREADABLE:{exc.__class__.__name__}"], evidence)
    evidence["bytes"] = len(text)
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return PolicyResult(False, None, ["APPROVAL_POLICY_INVALID_JSON"], evidence)
    return validate_doc(doc, source=path)


def resolve_approvers(*, explicit: str = "", policy_path: str = DEFAULT_PATH,
                      read=None) -> tuple[tuple[str, ...], int, PolicyResult | None]:
    """确定本次判定的审批人与所需批准数。

    * 显式 `--approver` 视为**人工显式输入**（不是推断），只需 1 人；
    * 否则必须由策略文件给出；策略不可用时返回空审批人集合，让调用方 fail-closed。
    """
    if explicit:
        return (explicit,), 1, PolicyResult(True, policy=None, reason_codes=[],
                                            evidence={"source": "explicit-cli"})
    result = load_policy(policy_path, read=read)
    if result.ok and result.policy is not None:
        return result.policy.approvers, result.policy.required_approvals, result
    return (), 0, result
