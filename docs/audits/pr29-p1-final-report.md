# PR #29 阶段十三 P1 最终报告（第 3/3 部分执行结果）

- 执行时间：2026-10-18 14:05 (Asia/Shanghai) / 06:05 UTC
- 仓库：`Hawaiine/mihomo-rules`（唯一修改对象）
- PR：https://github.com/Hawaiine/mihomo-rules/pull/29（`open`，未合并）
- base：`main @ f4447b73023474cb2f62de51277397fb8df911f0`
- **被测代码提交**：`4e50347d80e49bb0ad2cc50a5a54ea80df9cfcf7`（tree `8d7abffb`）
- 本轮新增提交：`84c23b3`、`b9bf20b`、`4e50347`
- 最终 PR head：见 §H（报告提交后由 push 决定）

---

## A. 执行摘要

**结论：PASS WITH CONDITIONS**（代码与测试范围内 PASS；平台端到端 BLOCKED；生产保持关闭）

本轮完成：
1. 九项历史风险逐条复核（2 项 NOT_PRESENT、6 项 CONFIRMED 并修复、1 项 PARTIALLY）；
2. 锁序修复（generate 必须在 lock 之后）、严格 SHA、身份类型校验、publisher 收紧；
3. 快照错误分类 + 先快照后 CAS + 写入超时对账；
4. 新增变更分类器 / 人工审批校验 / 合并决策矩阵（只判定不合并）；
5. `RECOVERY_REQUIRED` 落地、`require-usable` 前置检查；
6. 失败矩阵守卫（31 场景登记，删测试即失败）。

未完成 / 阻塞：真实 `state/gate` 持久化与并发、`workflow_run` 真实触发、平台是否接受 bot status
（均需合并或 M3 授权）；生产同步/自动合并仍关闭。

---

## B. 基线（本轮实测）

| 项 | 值 |
|---|---|
| 工作区 | 干净（`git status --porcelain` = 0）；分支 `feat/pr-first-automation` |
| PR | `open`、`mergeable_state=clean`、49→50 文件、17→18 提交 |
| head（开始时） | `b9bf20b131d5593f47f2a1fc27e67b45b79ada1a` |
| base | `f4447b73…` |
| checks | 三个 check-run 全 success |
| reviews | 0（无人工批准） |
| rulesets | `protect-main`（25330782, active, bypass 空） |
| Actions 设置 | `can_approve_pull_request_reviews=true`，`default_workflow_permissions=read` |
| `state/gate` | 404（不存在，未创建） |
| 基线测试 | 1099 → 1152 → 1160 tests（随新增模块递增，见 §D） |

**安全事件（已处置）**：本地 worktree 的 `origin` remote URL 曾内嵌 `x-access-token:gho_…`。
已执行 `git remote set-url origin https://github.com/Hawaiine/mihomo-rules.git` 清除；
后续 push 使用一次性 URL（不落盘）。该 token 从未进入仓库文件或提交。

---

## C. 缺陷修复对照表

| ID | 等级 | 状态 | 证据（当前源码） | 修复 | 测试 | 剩余风险 |
|---|---|---|---|---|---|---|
| R1 App/PAT 残留 | 高 | **NOT_PRESENT** | `grep -rn "APP_TOKEN\|PR_FIRST_APP\|create-github-app-token"` → 0 实际引用 | 守卫遇关键字即失败 | `test_publish_with_app_token_rejected` | 无 |
| R2 generate 早于 lock | 高 | **CONFIRMED → FIXED** | 修前 `generate.needs=gate` | `needs=[gate,lock]` + `if lock.result=='success'` | `LockOrderTest.test_generate_without_lock_is_rejected` | 无 |
| R3 缺 SHA 绕过 | 高 | **CONFIRMED → FIXED** | 修前 `if item.get("sha") and …` | 必须 40 位且等于 expected | `test_missing_sha_rejected` | 无 |
| R4 身份只比 login | 高 | **CONFIRMED → FIXED** | 修前 `login == BOT_LOGIN` | 加 `type=Bot`/`type=User`；status creator 亦须 `Bot` | `test_creator_user_type_rejected`、`test_expected_mode_rejects_login_only_spoof` | 无 |
| R5 run 识别过宽 | 高 | **CONFIRMED → FIXED** | 修前只校验 repo/event/name/head/conclusion | 增 `path`、`head_repository`、`pull_requests` 关联 | 3 例拒绝测试 | 无 |
| R6 快照错误被吞 | 高 | **CONFIRMED → FIXED** | 修前 `except StateError → existing`；先写状态后快照 | `SnapshotExists` 单列；`_commit` 先快照后 CAS | `test_snapshot_failure_blocks_state_change` | 无 |
| R7 文档/源码不一致 | 低 | **NOT_PRESENT** | 文档与两个 workflow 均只用 `secrets.GITHUB_TOKEN` | — | 守卫 | 无 |
| R8 候选 PR 无 check-run vs Ruleset | 中 | **CONFIRMED（惰性阻塞）** | Ruleset 要求 3 个 check-run；bot PR 不产生 check-run | 设计已定：合并后把 `route-a/verify` 加入 Ruleset | — | 生产开启后会阻塞，需按序处理 |
| R9 过期租约静默接管 | 高 | **PARTIALLY → FIXED** | 修前无 `--takeover`、无 epoch 推进 | 默认 `EXIT_NEEDS_HUMAN`；`--takeover` 才接管 | `test_expired_lease_requires_explicit_takeover` | 真实 ref 未实测 |
| 新增：RECOVERY_REQUIRED 缺失 | 高 | **CONFIRMED → FIXED** | 修前只有 `paused` | 新增 phase + `BLOCKING_PHASES` + `require-usable` | `test_recovery_required_blocks_acquire_and_complete` | — |
| 新增：写入超时不确定 | 高 | **CONFIRMED → FIXED** | 修前 `StateError` 直接失败，可能重复写 | 回读对账（generation+run_id+phase 一致即幂等接受） | 2 例（已生效/未生效） | 真实网络抖动未实测 |

---

## D. 测试证据（本轮实测）

环境：Python 3.14.7，`MIHOMO_ICON_REPO` 指向 Oasisic-Icons 检出，`GITHUB_RUN_ID=999`（模拟 CI）。

| 命令 | rc | 结果 |
|---|---|---|
| `python3 -m unittest discover -s scripts/tests` | 0 | **1160 tests OK (skipped=1)** |
| `python3 scripts/ci/verify_workflow_policy.py` | 0 | 4 workflow 通过（含锁序、App 禁令、唯一入口） |
| `python3 scripts/ci/verify_general_pr.py` | 0 | all invariants satisfied |
| `python3 scripts/ci/verify_policy_baseline.py` | 0 | 通过（base 无该脚本，一次性限制） |
| `python3 scripts/verify_configs.py` | 0 | 4 变体 PASS |
| `python3 scripts/verify_rulesets.py` | 0 | `FAIL: 0` |
| `python3 scripts/readme_stats.py --check` | 0 | 一致 |
| `git diff --check` | 0 | 无空白错误 |
| CI（`4e50347`） | — | `general-pr-verify` / `workflow-policy-guard` / `trusted-baseline-guard` 全 success（run 39113566912） |

**本轮失败→修复记录**：`test_out_of_scope_path_is_unknown` 首版把 `scripts/evil.py` 判为
`GOVERNANCE_CHANGE` 之外的值 —— 根因是治理面只列了 `scripts/ci/`；已把整个 `scripts/`
纳入治理面（更严格），不是改断言迁就实现。

---

## E. D-15 状态机审计

**Schema**：`schema_version=1`、`generation`（严格 +1）、`epoch`、`run_id`、`base_sha`/`candidate_sha`
（40 hex）、`target_ref`、`phase`、`check_status`、`conclusion`、`lock_owner`、`lock_token`、
`lease_until`、`snapshot_digest`、`updated_at`。

**转换表**（详见 `docs/d-15-state-plane.md` §10）：

| 状态 | 取值 | 允许去向 |
|---|---|---|
| IDLE | 无状态 | → LOCKED |
| LOCKED | `phase=candidate, check_status=pending` | → VERIFYING / PAUSED / FAILED |
| VERIFYING | `phase=validation|pr|trusted-gate|gate, in_progress` | → FINALIZING / PAUSED |
| PAUSED | `phase=paused` | → LOCKED（仅 resume） |
| RECOVERY_REQUIRED | `phase=recovery-required` | → LOCKED（仅 resume） |
| COMPLETED | `phase=final-review, completed` | 终态 |
| FAILED | `phase=failed, failure` | 终态 |

**关键行为与证据**：

| 项 | 结果 | 证据 |
|---|---|---|
| 两个 run 并发申请 | 恰好一个成功 | `test_concurrent_writers_only_one_wins` |
| 过期租约 | 默认拒绝，需 `--takeover`（epoch+1） | `test_expired_lease_requires_explicit_takeover` |
| 快照失败 | 状态不写、生成不继续 | `test_snapshot_failure_blocks_state_change` |
| 快照已存在 | 仅 `SnapshotExists` 幂等 | `test_snapshot_is_idempotent_and_not_overwritten` |
| CAS 冲突 | 422 → `CasConflict`，`force=false` 断言 | `test_cas_conflict_422_raises` |
| 写入超时 | 回读对账后幂等接受 / 否则 fail-closed | 2 例 |
| PAUSED/RECOVERY 下 complete | 拒绝（`EXIT_NEEDS_HUMAN`） | `test_pause_then_normal_complete_is_refused`、recovery 用例 |
| 未授权 resume | 拒绝 | `test_recovery_required_blocks_acquire_and_complete` |
| 恢复重放/回退/摘要不符 | 全部拒绝 | `RecoveryTests` 6 例 |
| 持久化位置 | 设计：同分支 `state/snapshots/gen-N.json` + tag | **未在真实 ref 实测** `[无法验证]` |

---

## F. 分类 / 审批 / 决策矩阵

| 输入 | 分类 | 决策 |
|---|---|---|
| `ruleset/X/X.yaml` 修改且 base 存在 | EXISTING_DATA_ONLY | 全绿+持锁 → `ELIGIBLE_EXISTING_DATA`（生产关闭时 `production_allowed=false`） |
| 新增 `ruleset/Y/Y.yaml`（tree 证明） | NEW_RULESET | 有当前 head 审批 → `ELIGIBLE_NEW_RULESET_AFTER_APPROVAL`；否则 BLOCKED |
| 任意 `.github/**` / `scripts/**` | GOVERNANCE_CHANGE | 永远 BLOCKED |
| diff 截断/未知路径/删除/重命名/无 tree 证据 | MIXED_OR_UNKNOWN | BLOCKED |
| 审批为旧 commit | — | `APPROVAL_STALE` → BLOCKED |
| 审批被 DISMISSED/CHANGES_REQUESTED | — | `APPROVAL_INVALIDATED` → BLOCKED |
| 锁 paused / recovery / snapshot_failed | — | 对应 reason code → BLOCKED |

测试：`test_classify_change`（21）、`test_approval_and_decide`（22）。

---

## G. 平台只读复核（本轮实测，未修改）

| 项 | 值 | 说明 |
|---|---|---|
| Ruleset `protect-main` | active，required: `general-pr-verify`/`workflow-policy-guard`/`trusted-baseline-guard`（integration 15368），strict，bypass 空 | 已生效（本 PR `mergeable_state=clean` 即证据） |
| `route-a/verify` | **未**纳入必需状态 | 发布器需先在 main 生效，否则死锁 |
| `can_approve_pull_request_reviews` | `true` | 用户已授权，上轮设置 |
| `default_workflow_permissions` | `read` | 最小权限 |
| `state/gate` | 404 | 未创建（M3 未授权） |
| 生产开关 | 未设置 | 同步/自动合并均关闭 |
| GITHUB_TOKEN 建 PR 后事件 | 不触发 `pull_request`（已知平台行为） | 因此用 status 模式，见 §F |

无法验证：bot 发布的 status 是否被 Ruleset 接受（需先合并）；`workflow_run` 真实触发（同上）。

---

## H. 提交与 PR

| 项 | 值 |
|---|---|
| 提交 | `84c23b3`（九项风险修复）、`b9bf20b`（分类/审批/决策）、`4e50347`（D-15 收口 + 矩阵守卫） |
| 被测代码 tree | `8d7abffbd023ffc782921f07258a18babaf270c5` |
| PR | `open`，`mergeable_state=clean`，50 文件，18 提交 |
| 最新 checks | 三个 success（head `4e50347`） |
| 是否合并 | **否** |

---

## I. 进度估算（口径：只计有可复核证据的项）

| 维度 | 百分比 | 依据 |
|---|---:|---|
| 阶段十三设计 | 90% | P0/P1 设计全部定稿；仅"人工审批流程/治理审查流程"仍需制度确认 |
| 代码实现 | 92% | 15→19 个 ci 模块；1160 tests；仅运行时接线（workflow 调用分类/决策）未做 |
| 测试完备 | 90% | 31 场景矩阵 + 1160 tests；真实平台并发未测 |
| 独立审计覆盖 | 30% | ChatGPT 复审 + 自建测试；无第三方独立复核 |
| 平台验证 | 55% | M1/M2 已实测；status/Ruleset 兼容与 state ref 未验证 |
| **生产就绪度** | **35%** | 生产开关关闭、无真实 E2E、无 D-15 持久化实测 |
| 项目整体 | 66% | `0.3×0.92 + 0.2×0.90 + 0.2×0.30 + 0.2×0.55 + 0.1×0.35 = 0.645 ≈ 65%`；含品牌治理（40%）后四舍五入 66% |

生产就绪度**不得**因代码完成而上调。

---

## J. 最终状态标签

**PASS WITH CONDITIONS**

- 代码与测试范围内：全部 12 条成功标准满足（1160 tests、guard 全绿、CI 三检查 success）；
- 未关闭的高风险：R8（候选 PR 无 check-run vs Ruleset，生产开启即阻塞）、D-15 真实
  `state/gate` 并发/持久化/恢复未实测；
- 未关闭的中风险：`RECOVERY_REQUIRED` 的人工处置流程未文档化到 runbook；审批人仅
  `@Hawaiine`（单人，无备份审批人）；
- 生产同步、生产自动合并、状态迁移**仍全部关闭**；PR 未合并；未写入 main；未改平台设置（本轮）。
