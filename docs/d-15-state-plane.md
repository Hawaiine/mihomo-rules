# D-15 持久状态控制平面（方案 A）

用户已确认 D-15 采用**方案 A**：GitHub 控制平面（专用状态 ref + 逻辑仓库锁 + CAS），
运行快照与恢复证据放在工作区、runner 临时目录与 cache 之外的**远端持久结构**。

## 1. 组件

| 组件 | 文件 | 作用 |
|---|---|---|
| 状态模型 | `scripts/ci/gate_state.py` | `GateState`（schema_version=1）、`validate_doc`、`canonical_digest` |
| 后端 | 同上 | `GitHubRefStore`（唯一生产后端）、`LocalFileStore`（**仅测试**） |
| 恢复 CLI | `scripts/ci/recover_gate_state.py` | 只从已验证快照生成新 generation |

## 2. 状态 schema（v1）

```json
{
  "schema_version": 1,
  "generation": 7,
  "epoch": 3,
  "run_id": "run-20261018T010203Z-abc123",
  "base_sha": "<40 hex>",
  "candidate_sha": "<40 hex>",
  "target_ref": "refs/heads/candidate/rules-update",
  "phase": "candidate|validation|pr|trusted-gate|gate|final-review|paused|failed",
  "check_status": "pending|in_progress|completed|failure",
  "conclusion": "none|success|failure|skipped",
  "lock_owner": "gate-<run_id>",
  "lock_token": "<32+ chars>",
  "lease_until": 0.0,
  "snapshot_digest": "sha256:...",
  "updated_at": 0.0
}
```

约束：`generation` 单调且 `>=1`；`snapshot_digest = sha256(canonical_json(除 digest/updated_at 外))`；
`phase × check_status` 只允许白名单组合（`pending`→`candidate`；`in_progress`→`validation|pr|trusted-gate|gate`；
`completed`→`final-review`；`failure`→同上）。任何不符即 `StateError`（fail-closed）。

## 3. CAS 语义（关键）

1. `GET /git/ref/heads/state/gate` 取当前 SHA（404 → 未初始化，`expected_old_sha=None`）。
2. 若 `expected_old_sha` 与当前 SHA 不符 → **先拒绝，不产生任何写入**（本地即可判定的 stale）。
3. 写 blob → tree（`base_tree` 继承）→ commit → `PATCH /git/refs/heads/state/gate {"force": false}`。
4. `409/422` → `CasConflict`；调用方必须重新读取后重新决策，**禁止重试覆盖**。
5. 绝不使用 `force: true`，也绝不把 `git push --force` 当作 CAS。

过期租约**不回收**：只能由新 generation 的 CAS 接管；旧持有者随后任何写入因 generation 落后而失败。

## 4. 快照持久化与保留

* 内容：`{schema_version, generation, state, digest}`。
* 位置：同分支 `state/snapshots/gen-<N>.json`（blob）+ 轻量 tag `state/gate/gen-<N>`（指向快照提交）。
* 独立性：不写 worktree、不写 `$RUNNER_TEMP`、不写 cache；runner 被销毁后仍可读。
* 保留：tag 与分支引用存在即对象不被 GC；建议保留最近 30 代 + 所有 `final-review` 代，
  删除需独立 CLI 与审计（本 PR 未实现删除器，避免误删）。
* 前置条件：需要 `contents: write` 的 GITHUB_TOKEN，且 `state/*` 分支不在受保护列表内。
  **平台侧未配置，属阻塞项**。

## 5. 恢复 CLI

```
python3 scripts/ci/recover_gate_state.py --repo owner/name \
  --from-generation 6 --expected-current-generation 6 [--dry-run]
```

拒绝条件（全部 fail-closed，退出码 1）：当前状态不存在 / `generation` 与 `--expected-current-generation`
不符 / 目标快照不早于当前代（防回退、防重放）/ 快照 schema 不兼容 / digest 不符 / 状态非法 /
CAS 冲突 / 写入后回读不一致。成功时 `generation = 旧 + 1`、`epoch = max(旧, 快照) + 1`，
旧状态保留在 ref 历史中。

## 6. 恢复 runbook

1. **判断**：`read()` 报 schema/digest 失败，或 `phase` 卡住且租约过期。
2. **取证（只读）**：记录状态 ref SHA、generation、最近 3 代 tag、CI run URL。
3. **选目标**：最近一个 digest 校验通过的代 `N`。
4. **预演**：`--dry-run` → 打印将写入的 generation/epoch 与 `expected_old_sha`。
5. **执行**：去掉 `--dry-run`；成功必须回读并展示新状态。
6. **失败即停**：任何 stale/schema/digest/权限错误都停止并上报，**不得重试到成功**。
7. **回滚**：不使用 force push；回到更早代同样走本 CLI（生成新 generation）。

## 7. 测试矩阵与当前证据

| 场景 | 用例 | 结果 |
|---|---|---|
| 创建/读取/初次 CAS | `test_create_then_read` | PASS |
| ref 已存在时禁止重建 | `test_create_when_ref_exists_is_rejected` | PASS |
| 陈旧 expected SHA | `test_stale_expected_sha_is_rejected` | PASS |
| 两并发写者只有一个成功 | `test_concurrent_writers_only_one_wins` | PASS |
| digest 不符 | `test_digest_mismatch_rejected` | PASS |
| 非法 phase 组合 / generation=0 | `test_invalid_phase_combination_rejected` 等 | PASS |
| 状态文件损坏 | `test_corrupted_state_file_fails_closed` | PASS |
| 快照不覆盖 / 缺失 | `test_snapshot_is_idempotent_and_not_overwritten` 等 | PASS |
| 恢复：正常/重放/代不符/未来快照/digest 损坏/无当前状态 | `RecoveryTests` 6 例 | PASS |
| GitHub 协议：404→未初始化、422→CasConflict 且 `force=false`、403→fail-closed、无 token 拒绝、读取解码 | `GitHubRefStoreTests` 7 例 | PASS |
| 状态发布：head 漂移/base 漂移/PR closed/403/缺失 context 回读 | `PublishStatusTests` 6 例 | PASS |

**未覆盖（[无法验证]）**：真实 GitHub ref 上的并发竞争、真实半写中断、真实租约过期接管、
平台演练 D1–D7。这些需要写入权限与平台设置，本轮禁止执行。

## 8. 平台演练清单（需授权身份执行，本轮未做）

| # | 演练 | 期望 |
|---|---|---|
| D1 | 创建 `state/gate`（首次，`expected=None`） | 201，generation=1 |
| D2 | 正常 CAS 更新（`force:false`） | 200，ref 前进一代 |
| D3 | 用旧 SHA 再更新 | 422/409，ref 不变 |
| D4 | 沙箱分支上对比 `force:true` | 记录差异，证明生产不用 force |
| D5 | 删除本地工作区后读回快照 | 内容与 digest 一致 |
| D6 | `recover_gate_state.py --dry-run` 后实跑 | gen+1，旧状态保留 |
| D7 | 只读 token 执行 D1/D2 | 403 且流程 fail-closed |

## 9. 与其它门槛的边界

D-15 的本地实现通过**不等于**阶段十三通过：P0-09 gate 接线、P0-12 Ruleset、P0-15 status-source
仍是独立门槛；本 PR 未打开任何 runtime 写入开关（`CANDIDATE_SYNC_ENABLED` /
`CANDIDATE_AUTOMERGE_ENABLED` 均未设置）。
