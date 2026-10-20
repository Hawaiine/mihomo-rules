#!/usr/bin/env python3
"""classify_change.py — 变更分类器（可信代码；不信任 PR 自述）。

分类（优先级从高到低）：

1. ``GOVERNANCE_CHANGE``：碰到治理面（workflow / CI 脚本 / 测试 / required-checks /
   锁协议 / 状态 schema / 生成器 / 治理文档）。
2. ``MIXED_OR_UNKNOWN``：证据不完整（分页截断、SHA 非法、空文件列表但 SHA 不同、
   删除/重命名/类型或模式变化、未知路径、路径不合法）。
3. ``NEW_RULESET``：**由 base/head tree 证明**新增了 ruleset 对象。
4. ``EXISTING_DATA_ONLY``：仅修改 base 中已存在的规则集对象的数据文件。

任何不完整证据都**不会**降级为 ``EXISTING_DATA_ONLY``。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GOVERNANCE_CHANGE = "GOVERNANCE_CHANGE"
NEW_RULESET = "NEW_RULESET"
EXISTING_DATA_ONLY = "EXISTING_DATA_ONLY"
MIXED_OR_UNKNOWN = "MIXED_OR_UNKNOWN"

# 治理面：出现即 GOVERNANCE_CHANGE（不做例外）
GOVERNANCE_PREFIXES = (
    ".github/",          # workflow / required-checks / CODEOWNERS / 任何平台控制面
    "scripts/",          # 全部脚本都影响生成或门禁，按治理面处理（含 ci/tests/config_contract）
)
GOVERNANCE_FILES = {
    ".github/required-checks.json",
    ".github/CODEOWNERS",
    "docs/pr-first-automation.md",
    "docs/d-15-state-plane.md",
    "docs/change-classification.md",
    "scripts/generate_config.py",
    "scripts/batch_update.py",
    "scripts/readme_stats.py",
    "scripts/verify_configs.py",
    "scripts/verify_rulesets.py",
}
# 允许出现在纯数据变更中的路径（全部相对仓库根、已规范化）
DATA_FILES = {"README.md", "CHANGELOG.md"}
DATA_PATTERNS = (
    re.compile(r"^ruleset/[^/]+/[^/]+\.ya?ml$"),
    re.compile(r"^configs/(Android|Nikki)/config(\.min)?\.yaml$"),
)
RULESET_OBJECT_RE = re.compile(r"^ruleset/([^/]+)/[^/]+\.ya?ml$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    status: str = "modified"          # added|modified|removed|renamed|changed
    previous_filename: str = ""

    @staticmethod
    def from_api(item: dict) -> "ChangedFile":
        return ChangedFile(filename=str(item.get("filename") or ""),
                           status=str(item.get("status") or "modified"),
                           previous_filename=str(item.get("previous_filename") or ""))


@dataclass
class Classification:
    change_class: str
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    multi_labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"change_class": self.change_class, "reasons": self.reasons,
                "evidence": self.evidence, "multi_labels": self.multi_labels}


def normalize_path(path: str) -> str:
    """规范化相对路径；拒绝绝对路径、上跳、反斜杠与 NUL。"""
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise ValueError(f"非法路径: {path!r}")
    if path.startswith("/") or path.startswith("./"):
        raise ValueError(f"路径必须相对于仓库根: {path!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"路径包含空段/上跳: {path!r}")
    return "/".join(parts)


def is_data_path(path: str) -> bool:
    return path in DATA_FILES or any(p.match(path) for p in DATA_PATTERNS)


def is_governance_path(path: str) -> bool:
    return path in GOVERNANCE_FILES or path.startswith(GOVERNANCE_PREFIXES)


def classify(files: list[ChangedFile], *, base_sha: str, head_sha: str,
             truncated: bool = False, base_paths: set[str] | None = None,
             head_paths: set[str] | None = None) -> Classification:
    """纯函数分类。``base_paths``/``head_paths`` 为 tree 中的全部路径（用于证明新增）。

    没有 tree 证据时，任何 ``added`` 的 ruleset 文件都不能被当作"已存在对象的数据修改"，
    只能按 NEW_RULESET 或 MIXED_OR_UNKNOWN 处理。
    """
    reasons: list[str] = []
    if truncated:
        return Classification(MIXED_OR_UNKNOWN, ["DIFF_TRUNCATED"])
    if not (isinstance(base_sha, str) and SHA_RE.match(base_sha)
            and isinstance(head_sha, str) and SHA_RE.match(head_sha)):
        return Classification(MIXED_OR_UNKNOWN, ["SHA_INVALID"])
    if base_sha == head_sha:
        return Classification(MIXED_OR_UNKNOWN, ["SHA_IDENTICAL"])
    if not files:
        return Classification(MIXED_OR_UNKNOWN, ["EMPTY_DIFF_BUT_SHA_CHANGED"])

    norm: list[ChangedFile] = []
    for item in files:
        try:
            norm.append(ChangedFile(normalize_path(item.filename), item.status,
                                    normalize_path(item.previous_filename) if item.previous_filename else ""))
        except ValueError:
            return Classification(MIXED_OR_UNKNOWN, ["PATH_INVALID"])

    paths = [f.filename for f in norm]
    if len(paths) != len(set(paths)):
        return Classification(MIXED_OR_UNKNOWN, ["DUPLICATE_PATH"])
    if len({p.lower() for p in paths}) != len(paths):
        return Classification(MIXED_OR_UNKNOWN, ["CASE_COLLISION"])

    governance = [f for f in norm if is_governance_path(f.filename)
                  or (f.previous_filename and is_governance_path(f.previous_filename))]
    new_ruleset: list[str] = []
    data: list[str] = []
    unknown: list[str] = []

    for item in norm:
        if is_governance_path(item.filename) or is_governance_path(item.previous_filename):
            continue
        if item.status in ("removed", "renamed", "copied", "changed"):
            unknown.append(f"{item.status}:{item.filename}")
            continue
        if item.status not in ("added", "modified"):
            unknown.append(f"{item.status}:{item.filename}")
            continue
        if not is_data_path(item.filename):
            unknown.append(f"out-of-scope:{item.filename}")
            continue
        m = RULESET_OBJECT_RE.match(item.filename)
        if m:
            brand = m.group(1)
            existed_in_base = (base_paths is not None and item.filename in base_paths)
            if item.status == "added":
                if base_paths is None:
                    # 无 tree 证据：新增文件不能证明"已存在对象的数据修改"
                    new_ruleset.append(item.filename) if head_paths is not None else unknown.append(
                        f"no-tree-evidence:{item.filename}")
                else:
                    new_ruleset.append(item.filename) if not existed_in_base else data.append(item.filename)
            else:  # modified
                if base_paths is None:
                    unknown.append(f"no-tree-evidence:{item.filename}")
                elif not existed_in_base:
                    new_ruleset.append(item.filename)  # 修改了一个 base 中不存在的对象 ⇒ 新增
                else:
                    data.append(item.filename)
        else:
            data.append(item.filename)

    labels: list[str] = []
    if governance:
        labels.append(GOVERNANCE_CHANGE)
    if new_ruleset:
        labels.append(NEW_RULESET)
    if unknown:
        labels.append(MIXED_OR_UNKNOWN)
    evidence = {"files": len(norm), "governance": sorted(f.filename for f in governance),
                "new_ruleset": sorted(new_ruleset), "data": sorted(data), "unknown": sorted(unknown),
                "base_paths_provided": base_paths is not None,
                "head_paths_provided": head_paths is not None}

    if governance:
        reasons.append("GOVERNANCE_PATH_CHANGED")
        if new_ruleset:
            reasons.append("ALSO_NEW_RULESET")
        return Classification(GOVERNANCE_CHANGE, reasons, evidence, labels)
    if unknown:
        reasons.append("UNKNOWN_OR_UNPROVEN_CHANGE")
        return Classification(MIXED_OR_UNKNOWN, reasons, evidence, labels)
    if new_ruleset:
        return Classification(NEW_RULESET, ["NEW_RULESET_OBJECT_PROVEN"], evidence, labels)
    if not data:
        return Classification(MIXED_OR_UNKNOWN, ["NO_DATA_CHANGE"], evidence, labels)
    return Classification(EXISTING_DATA_ONLY, ["ALL_CHANGES_ARE_EXISTING_DATA"], evidence,
                          [EXISTING_DATA_ONLY])
