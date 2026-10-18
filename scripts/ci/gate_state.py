#!/usr/bin/env python3
"""gate_state.py — D-15 A：GitHub 控制平面状态存储（逻辑仓库锁 + CAS + 快照）。

设计（用户已确认 D-15 方案 A）：

* 状态：`refs/heads/state/gate` 上的单个 JSON 文件 `state.json`（schema_version=1）。
* 并发控制：**GitHub ref 的 CAS 语义**——写入用 `PATCH /git/refs/heads/state/gate`
  且 `force=false`；ref 已前进时 API 返回 422，调用方必须重新读取后重新决策。
  绝不允许 `force=true`，也不允许 `git push --force` 充当 CAS。
* 逻辑锁：状态里的 `lock{owner,token,lease_until}`。过期租约**不回收**：只能由新
  generation 的 CAS 写入接管，旧持有者随后任何写入都会因 generation 落后而失败。
* 快照：每次提交把 payload 写到同一分支的 `state/snapshots/gen-<N>.json`，并打
  tag `state/gate/gen-<N>`；两者都在远端，独立于工作区 / runner 临时目录 / cache。

后端：

* ``GitHubRefStore``：唯一生产后端（REST API）。
* ``LocalFileStore``：**仅测试**，用 CAS 语义模拟（比较并替换 + 原子 rename）。
* 生产配置只能选 github-ref；出现本地后端即视为配置错误（fail-closed）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1
STATE_REF = "refs/heads/state/gate"
STATE_PATH = "state.json"
SNAPSHOT_TAG_PREFIX = "state/gate/gen-"
LEGAL_PHASES = {"candidate", "validation", "pr", "trusted-gate", "gate", "final-review",
                "paused", "recovery-required", "failed"}
BLOCKING_PHASES = {"paused", "recovery-required"}
LEGAL_CHECK_STATUS = {"pending", "in_progress", "completed", "failure"}
LEGAL_COMBINATIONS = {
    "pending": ({"candidate"}, {"none"}),
    "in_progress": ({"validation", "pr", "trusted-gate", "gate"}, {"none"}),
    "completed": ({"final-review"}, {"success", "failure", "skipped"}),
    "failure": ({"validation", "pr", "trusted-gate", "gate"}, {"failure"}),
}


class StateError(RuntimeError):
    """状态不可读 / 语义非法 / 后端不可用：一律 fail-closed。"""


class SnapshotExists(StateError):
    """该 generation 的快照已存在（重复写同一代）；与真实失败区分。"""


class CasConflict(StateError):
    """CAS 失败：ref 已被其他写者推进，或期望的旧 SHA 不匹配。"""


@dataclass(frozen=True)
class GateState:
    run_id: str
    generation: int
    epoch: int
    base_sha: str
    candidate_sha: str
    target_ref: str
    phase: str
    check_status: str
    conclusion: str = "none"
    lock_owner: str = ""
    lock_token: str = ""
    lease_until: float = 0.0
    snapshot_digest: str = ""
    updated_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def to_doc(self) -> dict[str, Any]:
        doc = dataclasses.asdict(self)
        doc["updated_at"] = self.updated_at or time.time()
        return doc

    @staticmethod
    def from_doc(doc: dict[str, Any]) -> "GateState":
        problems = validate_doc(doc)
        if problems:
            raise StateError("; ".join(problems))
        return GateState(**{k: doc[k] for k in GateState.__dataclass_fields__ if k in doc})


def canonical_digest(doc: dict[str, Any]) -> str:
    """对除 snapshot_digest / updated_at 之外的字段做规范化摘要。"""
    body = {k: v for k, v in doc.items() if k not in ("snapshot_digest", "updated_at")}
    return "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def validate_doc(doc: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["state 必须是 JSON 对象"]
    if doc.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    gen = doc.get("generation")
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 1:
        problems.append("generation 必须是 >=1 的整数")
    epoch = doc.get("epoch", 0)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        problems.append("epoch 必须是 >=0 的整数")
    for key in ("run_id", "base_sha", "candidate_sha", "target_ref", "phase", "check_status"):
        if not isinstance(doc.get(key), str) or not doc.get(key):
            problems.append(f"{key} 必须是非空字符串")
    for key in ("base_sha", "candidate_sha"):
        value = doc.get(key)
        if isinstance(value, str) and value and not (len(value) == 40 and all(c in "0123456789abcdef" for c in value)):
            problems.append(f"{key} 必须是 40 位小写 SHA")
    phase, check = doc.get("phase"), doc.get("check_status")
    if phase in LEGAL_PHASES and check in LEGAL_CHECK_STATUS:
        allowed_phases, allowed_conclusions = LEGAL_COMBINATIONS[check]
        if phase not in allowed_phases and phase not in ("paused", "failed", "recovery-required"):
            problems.append(f"phase={phase} 与 check_status={check} 组合非法")
        elif doc.get("conclusion", "none") not in allowed_conclusions | {"none"}:
            problems.append("conclusion 与 check_status 组合非法")
    if check == "completed" and phase == "final-review" and not doc.get("check_status"):
        problems.append("completed 必须有结论")
    digest = doc.get("snapshot_digest", "")
    if digest:
        expected = canonical_digest(doc)
        if digest != expected:
            problems.append("snapshot_digest 与内容不一致")
    return problems


@dataclass(frozen=True)
class CasResult:
    ok: bool
    state: GateState | None = None
    ref_sha: str = ""
    reason: str = ""


class GateStateStore(Protocol):
    backend: str

    def read(self) -> tuple[GateState | None, str]: ...

    def cas_write(self, state: GateState, *, expected_old_sha: str | None) -> CasResult: ...

    def snapshot_put(self, state: GateState, payload: bytes) -> str: ...

    def snapshot_get(self, generation: int) -> bytes: ...


# ───────────────────────── 本地测试后端 ─────────────────────────

class LocalFileStore:
    """仅测试用：用「读-比较-原子替换」模拟 CAS；生产禁用。"""

    backend = "local-file"

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / STATE_PATH
        self.snapshots = self.directory / "snapshots"
        self.snapshots.mkdir(exist_ok=True)
        # 模拟服务端 CAS 原子性：真实的比较并交换发生在 GitHub ref 上。
        import threading
        self._lock = threading.Lock()

    def _ref_sha(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else ""

    def read(self) -> tuple[GateState | None, str]:
        if not self.path.exists():
            return None, ""
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"state 不可读: {exc}") from exc
        return GateState.from_doc(doc), self._ref_sha()

    def cas_write(self, state: GateState, *, expected_old_sha: str | None) -> CasResult:
        with self._lock:
            return self._cas_write_locked(state, expected_old_sha=expected_old_sha)

    def _cas_write_locked(self, state: GateState, *, expected_old_sha: str | None) -> CasResult:
        current = self._ref_sha()
        if expected_old_sha is None:
            if current:
                return CasResult(False, reason="状态已存在（期望创建，但 ref 非空）")
        elif current != expected_old_sha:
            return CasResult(False, reason="stale: 当前 ref 与 expected_old_sha 不一致")
        doc = state.to_doc()
        problems = validate_doc(doc)
        if problems:
            return CasResult(False, reason="; ".join(problems))
        tmp = self.directory / f".{STATE_PATH}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        return CasResult(True, state=state, ref_sha=self._ref_sha())

    def snapshot_put(self, state: GateState, payload: bytes) -> str:
        target = self.snapshots / f"gen-{state.generation}.json"
        if target.exists():
            raise SnapshotExists(f"快照已存在，拒绝覆盖: {target.name}")
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, target)
        return hashlib.sha256(payload).hexdigest()

    def snapshot_get(self, generation: int) -> bytes:
        target = self.snapshots / f"gen-{generation}.json"
        if not target.exists():
            raise StateError(f"快照不存在: gen-{generation}")
        return target.read_bytes()


# ───────────────────────── GitHub ref 后端 ─────────────────────────

class GitHubRefStore:
    """生产后端：状态 ref + snapshot tag + CAS（force=false）。

    需要 contents: write。所有非 2xx 一律抛 StateError（fail-closed），不降级到本地。
    """

    backend = "github-ref"

    def __init__(self, repo: str, token: str, *, state_ref: str = STATE_REF,
                 api: str = "https://api.github.com", timeout: int = 30):
        if not token:
            raise StateError("缺少 GITHUB_TOKEN：GitHub 后端不可用（fail-closed）")
        self.repo, self.token, self.state_ref = repo, token, state_ref
        self.api, self.timeout = api.rstrip("/"), timeout

    # -- HTTP ----------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.api}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "route-a-gate-state"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw.decode("utf-8", "replace")
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError) as exc:
            raise StateError(f"GitHub API 不可用: {exc}") from exc

    # -- ref helpers ---------------------------------------------------
    def _ref_sha(self) -> str:
        status, payload = self._request("GET", f"/repos/{self.repo}/git/ref/heads/{self.state_ref.removeprefix('refs/heads/')}")
        if status == 404:
            return ""
        if status != 200:
            raise StateError(f"读取状态 ref 失败: HTTP {status}")
        return payload["object"]["sha"]

    def read(self) -> tuple[GateState | None, str]:
        sha = self._ref_sha()
        if not sha:
            return None, ""
        status, payload = self._request("GET", f"/repos/{self.repo}/contents/{STATE_PATH}?ref={sha}")
        if status != 200 or not isinstance(payload, dict) or "content" not in payload:
            raise StateError(f"状态文件不可读: HTTP {status}")
        import base64
        try:
            doc = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise StateError(f"状态文件损坏: {exc}") from exc
        return GateState.from_doc(doc), sha

    def cas_write(self, state: GateState, *, expected_old_sha: str | None) -> CasResult:
        """CAS：先基于 expected_old_sha 建提交，再以 force=false 更新 ref。"""
        current = self._ref_sha()
        if expected_old_sha is None and current:
            return CasResult(False, reason="状态已存在（期望创建，但 ref 非空）")
        if expected_old_sha is not None and current != expected_old_sha:
            return CasResult(False, reason="stale: 当前 ref 与 expected_old_sha 不一致")
        doc = state.to_doc()
        problems = validate_doc(doc)
        if problems:
            return CasResult(False, reason="; ".join(problems))

        base = current or self._default_branch_sha()
        status, payload = self._request("POST", f"/repos/{self.repo}/git/blobs", {
            "content": json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
            "encoding": "utf-8"})
        if status not in (200, 201):
            raise StateError(f"创建 blob 失败: HTTP {status}")
        blob_sha = payload["sha"]
        status, payload = self._request("POST", f"/repos/{self.repo}/git/trees", {
            "base_tree": self._tree_sha(base),
            "tree": [{"path": STATE_PATH, "mode": "100644", "type": "blob", "sha": blob_sha}]})
        if status not in (200, 201):
            raise StateError(f"创建 tree 失败: HTTP {status}")
        tree_sha = payload["sha"]
        status, payload = self._request("POST", f"/repos/{self.repo}/git/commits", {
            "message": f"chore(gate-state): generation {state.generation} ({state.phase})",
            "tree": tree_sha, "parents": [base]})
        if status not in (200, 201):
            raise StateError(f"创建 commit 失败: HTTP {status}")
        commit_sha = payload["sha"]

        if current:
            status, payload = self._request("PATCH", f"/repos/{self.repo}/git/refs/heads/{self.state_ref.removeprefix('refs/heads/')}", {
                "sha": commit_sha, "force": False})
        else:
            status, payload = self._request("POST", f"/repos/{self.repo}/git/refs", {
                "ref": self.state_ref, "sha": commit_sha})
        if status in (409, 422):
            raise CasConflict(f"CAS 失败：ref 已被推进（HTTP {status}）")
        if status not in (200, 201):
            raise StateError(f"更新 ref 失败: HTTP {status}")
        return CasResult(True, state=state, ref_sha=commit_sha)

    def _default_branch_sha(self) -> str:
        status, payload = self._request("GET", f"/repos/{self.repo}/git/ref/heads/main")
        if status != 200:
            raise StateError(f"读取 main 失败: HTTP {status}")
        return payload["object"]["sha"]

    def _tree_sha(self, commit_sha: str) -> str:
        status, payload = self._request("GET", f"/repos/{self.repo}/git/commits/{commit_sha}")
        if status != 200:
            raise StateError(f"读取 commit 失败: HTTP {status}")
        return payload["tree"]["sha"]

    # -- snapshots -----------------------------------------------------
    def snapshot_put(self, state: GateState, payload: bytes) -> str:
        import base64
        status, blob = self._request("POST", f"/repos/{self.repo}/git/blobs", {
            "content": base64.b64encode(payload).decode(), "encoding": "base64"})
        if status not in (200, 201):
            raise StateError(f"快照 blob 创建失败: HTTP {status}")
        tree_path = f"state/snapshots/gen-{state.generation}.json"
        status, tree = self._request("POST", f"/repos/{self.repo}/git/trees", {
            "tree": [{"path": tree_path, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
        if status not in (200, 201):
            raise StateError(f"快照 tree 创建失败: HTTP {status}")
        status, commit = self._request("POST", f"/repos/{self.repo}/git/commits", {
            "message": f"chore(gate-state): snapshot gen-{state.generation}",
            "tree": tree["sha"]})
        if status not in (200, 201):
            raise StateError(f"快照 commit 创建失败: HTTP {status}")
        tag = f"{SNAPSHOT_TAG_PREFIX}{state.generation}"
        status, _ = self._request("POST", f"/repos/{self.repo}/git/refs",
                                  {"ref": f"refs/tags/{tag}", "sha": commit["sha"]})
        if status == 422:
            raise SnapshotExists(f"快照 tag 已存在: {tag}")
        if status not in (200, 201):
            raise StateError(f"快照 tag 创建失败: HTTP {status}")
        return hashlib.sha256(payload).hexdigest()

    def snapshot_get(self, generation: int) -> bytes:
        status, payload = self._request(
            "GET", f"/repos/{self.repo}/contents/state/snapshots/gen-{generation}.json?ref=refs/tags/{SNAPSHOT_TAG_PREFIX}{generation}")
        if status != 200:
            raise StateError(f"快照不可读 gen-{generation}: HTTP {status}")
        import base64
        return base64.b64decode(payload["content"])
