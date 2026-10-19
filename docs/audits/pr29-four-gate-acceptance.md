# PR #29 四关口验收（G1–G4）

> 对象：`feat/pr-first-automation` @ `690a1e5`（base `f4447b7`）
> 时间：2026-10-19（Asia/Shanghai）
> 本文件把 G1–G4 落成"输入 / 执行步骤 / 验收标准 / 当前结果 / 未执行原因"，
> 避免用"代码写完了"替代"验收通过"。
> 标签：`[本轮实测]` `[源码直接证明]` `[历史报告记录]` `[无法验证]` `[待用户批准]`

---

## 总览

| 关口 | 含义 | 当前状态 | 阻塞原因 |
|---|---|---|---|
| G1 | 独立代码审计 | **PASS WITH CONDITIONS** | 审计只覆盖到 `690a1e5`；head 再变需重审受影响范围 |
| G2 | 合并资格验收（不执行合并） | **PASS WITH CONDITIONS** | `reviews=0`；`route-a/verify` 未进 Ruleset（不影响本 PR 自身，但影响候选路径） |
| G3 | 平台控制面验收与上线顺序 | **BLOCKED** | Ruleset 缺 `route-a/verify`、`state/gate` 未初始化；平台写入需授权 |
| G4-A | D-15/Route A 本地端到端（无生产写入） | **PASS** | — |
| G4-B | 真实 GitHub 集成验证 | **BLOCKED / NOT EXECUTED** | 需单独授权；`state/gate` 未创建 |

---

## G1：独立代码审计

* 输入：PR 全量变更（57 文件）、base/head tree、workflow、测试。
* 步骤：结构扫描 → 高风险文件逐行阅读 → 不变量判定（I1–I12）→ 缺陷修复与回归测试。
* 通过标准：57 文件入册；P0/P1 已修或有源码级证明不存在；每项修复有回归测试；
  未删测试/未弱化断言；全量测试 + 全部 guard 通过；未能验证的平台行为列为阻塞；
  所有证据对应同一 head。
* 结果：**PASS WITH CONDITIONS**
  * 详见 `docs/audits/pr29-p1-independent-audit.md`。
  * 修掉 2 项：F-01（平台判定把其他分支规则当 main 规则）、F-02（发布者只校验 login）。
  * 遗留 1 项 P2（审批人由 owner 推断，F-03）**待用户裁定**；2 项 P3（文档债、测试抖动）。
  * 全量测试 **1252 tests OK (skipped=1)** `[本轮实测]`（修复前 1194）。
  * 条件：head 若变化，F-01/F-02 受影响范围需重审；`docs/audits` 历史文档未改写。

## G2：合并资格验收（**不执行合并**）

* 输入：最终 head/base、commit 列表、完整 diff、reviews、CI、Ruleset。
* 步骤与结果：

| # | 检查 | 结果 |
|---|---|---|
| 1 | head/base 与审计对象一致 | 一致（`690a1e5` / `f4447b7` = 当前 main）`[本轮实测]` |
| 2 | 三个必需 check-run 在最终 head success | success（run 见 §G2 证据）`[本轮实测]` |
| 3 | 必需检查来源为 GitHub Actions | manifest 声明 `app_id=15368`；CI 由 `pr-verify.yml` 产生 `[本轮实测]` |
| 4 | 候选 PR 的 `route-a/verify` 契约与 Ruleset 一致 | **不一致**：Ruleset 尚无该 context → 候选路径不可用（不影响本 PR） |
| 5 | 新增 Ruleset 的人工审批绑定当前 head | 本 PR 无 `NEW_RULESET` 变更，不触发该路径 `[源码直接证明]` |
| 6 | governance/mixed/unknown 不得走自动合并 | 本 PR 分类为 `GOVERNANCE_CHANGE`，只能人工 PR `[本轮实测]` |
| 7 | 无未解决的 P0/P1 | 成立（F-01/F-02 已修） |
| 8 | 有独立人工 review | **reviews=0** `[本轮实测]` |

* 结论：**PASS WITH CONDITIONS** —— 具备"可以由人类合并"的技术资格，
  但 `route-a/verify` 未进 Ruleset、`state/gate` 未初始化，因此**不具备开启生产同步/自动合并的资格**。
* 未满足条件：① 人工 review（是否强制由用户决定）；② 平台侧 `route-a/verify` 契约；③ D-15 初始化。
* 合并前仍需的人工操作：人类执行 merge（本轮禁止）；如需 review，先请人审。
* 本轮**未合并**，也不把 G2 的 PASS 解释为授权。

## G3：平台控制面验收

### G3.1 当前平台事实（只读实测）

| 项目 | 实测值 | 判定 |
|---|---|---|
| Ruleset `protect-main` | id 25330782，`enforcement=active`，`target=branch`，`conditions.include=["~DEFAULT_BRANCH"]`，`bypass_actors=[]` | 覆盖 main ✔ |
| 规则集合 | 仅 `deletion`、`non_fast_forward`、`required_status_checks` | **无 `pull_request` 规则**（不强制人工 review）✘ |
| 必需 contexts | `general-pr-verify`、`workflow-policy-guard`、`trusted-baseline-guard`（均 `integration_id=15368`） | **缺 `route-a/verify`** ✘ |
| `default_workflow_permissions` | `read` | ✔ |
| `can_approve_pull_request_reviews` | `true` | 平台事实，非本 PR 目标 |
| Actions variables | 空（三个开关均未设置） | 生产开关关闭 ✔ |
| `refs/heads/state/gate` | 404 Not Found | 未初始化 ✘（M3 未授权） |
| `.github/workflows/route-a-status.yml` @ main | 不存在 | 未上线 ✘ |
| 候选 workflow 真实运行 | 无 run（开关关闭） | `[无法验证]` |

### G3.2 设计要求 vs 实测差异

| 设计 | 实测 | 差异处理 |
|---|---|---|
| 人工 PR 走三个 check-run | ✔ 已满足 | 无需动作 |
| Ruleset 含 `pull_request` 规则（强制人工审阅） | ✘ 未配置 | 治理选择，需用户裁定后再写平台 |
| 候选 PR 走 `route-a/verify` status | ✘ 未纳入 Ruleset | 需平台写（步骤 3） |
| `route-a-status.yml` 在默认分支生效 | ✘ main 无此文件 | 需先合并本 PR（步骤 1–2） |
| D-15 状态可持久化 | ✘ 未初始化 | 需 M3 授权（步骤 4） |
| 生产开关默认关闭 | ✔ 全部未设置 | 保持 |

### G3.3 需管理员执行的最小步骤（**本轮全部未执行**）

1. 人类合并 PR #29 → 2. 确认 `route-a-status.yml` 在 main → 3. Ruleset 增加
   `route-a/verify`（绑定 15368）→ 4. 只读重跑 preflight 确认 1–3 生效 →
   5. **单独授权**初始化 `state/gate` 并做协议实测 → 6. 才讨论开启
   `CANDIDATE_SYNC_ENABLED` → 7. `CANDIDATE_AUTOMERGE_ENABLED` 永远最后单独评估。

* 每步的前置/验证/回滚见 `docs/audits/pr29-production-preflight.md` §7。
* 会改变平台状态的步骤：1、3、5、6、7 —— 全部 `[待用户批准]`。
* 只读 preflight 的完整 JSON 与终端输出：见上文件 §4。

## G4-A：D-15 / Route A 本地端到端（无生产写入）

| 要求 | 承载测试 | 结果 |
|---|---|---|
| CAS 协议 + stale SHA | `test_gate_state` / `test_route_a_protocol::GitHubRefStoreTests` | PASS |
| 并发写者只能一个成功 | `test_gate_state::LocalStoreTests.test_concurrent_writers_only_one_wins` | PASS |
| 422/403/404/超时/错误 JSON 故障注入 | `test_gate_state`、`test_publish_run_status`、`test_route_a_protocol` | PASS |
| 写入超时回读对账 | `test_gate_lock::test_write_timeout_reconciled_when_write_landed` / `..._not_landed_fails_closed` | PASS |
| 快照先写后 CAS；快照失败不改状态 | `test_gate_lock::test_snapshot_failure_blocks_state_change` | PASS |
| 同代同内容幂等、不同内容拒绝 | `test_gate_state::RecoveryTests` | PASS |
| paused/recovery 只能显式 resume | `test_gate_lock::test_pause_then_normal_complete_is_refused` | PASS |
| generation/epoch/owner/lease/base/head 一致性 | `test_gate_lock`、`test_gate_status` | PASS |
| Route A 发布前后 SHA/PR 状态校验 | `test_publish_run_status`（含本轮新增 type/run 绑定） | PASS |
| head/base 漂移、PR 关闭、发布 403、回读失败 | `test_route_a_protocol`、`test_publish_run_status` | PASS |
| workflow 失败语义/no-change/异常 finalize | `test_runtime_policy_wiring::WorkflowFailureSemanticsTest` | PASS |
| 全量 unittest + 全部 guard + 配置/规则集/统计/diff | 见 §验证命令 | PASS |

* 结论：**PASS**（本地语义层）。注意：这些都基于 fake API/临时目录，
  **不构成真实 GitHub ref 的持久化证据**。

## G4-B：真实 GitHub 集成验证

* 状态：**BLOCKED / NOT EXECUTED**。
* 原因：① 需单独授权；② `state/gate` 未创建；③ `route-a/verify` 未进 Ruleset，
  真实 status 与 Ruleset 兼容性无法验证。
* 授权后必须先在 runbook 写明：隔离目标、不触发 main 写入/自动合并、持久化恢复证据、
  测试 ref 与生产状态区分、中止条件。**不得用本地模拟替代本关口。**

---

## 本轮验证命令与结果（head `690a1e5`）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python3 -m unittest discover -s scripts/tests` | 0 | **1252 tests, OK (skipped=1)** |
| `python3 scripts/ci/verify_workflow_policy.py` | 0 | 通过（4 个 workflow 结构化判定） |
| `python3 scripts/ci/verify_general_pr.py`（带 base/head SHA） | 0 | 全 guard PASS |
| `python3 scripts/ci/verify_policy_baseline.py --base-sha … --head-sha …` | 0 | 通过（安全敏感文件 44） |
| `python3 scripts/verify_configs.py`（图标基准固定 revision） | 0 | 4/4 PASS |
| `python3 scripts/verify_rulesets.py` | 0 | 165/165 PASS |
| `python3 scripts/readme_stats.py --check` | 0 | 一致 |
| `python3 scripts/ci/verify_production_prerequisites.py --repo …` | **1** | **BLOCKED**（3 项未就绪，`writes_performed=false`） |

> 生产就绪度：**0%**。本轮代码完成度提高，但不改变生产就绪度。
