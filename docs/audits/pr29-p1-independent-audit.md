# PR #29 独立安全审计（阶段十三 P1.2 后续）

> 审计对象：`feat/pr-first-automation`，本轮收尾 head `690a1e5`（审计起点 `18d8b53`，修复提交 `bdb15cd`）
> base：`main @ f4447b73023474cb2f62de51277397fb8df911f0`
> 审计者：Hermes Agent（执行侧自审，**不替代** ChatGPT 的独立复核）
> 标签：`[源码直接证明]` `[本轮实测]` `[历史报告记录]` `[无法验证]` `[待用户批准]`

---

## 1. 方法与边界

* 覆盖范围：审计对象为**代码/测试/文档共 57 个文件**（53 个既有变更 + 本轮新增的
  `platform_rulesets.py`、`verify_production_prerequisites.py` 及两个测试文件）。
  该 57 个文件**不含**本文件、`pr29-production-preflight.md`、`pr29-four-gate-acceptance.md`
  这三份审计产物本身；加上它们，PR #29 当前共 **60 个变更文件**（`[本轮实测]` PR API
  `changed_files=60`）。
* 三类证据：
  1. **结构扫描**：对 57 个文件做危险模式扫描（`|| true`、`continue-on-error`、
     `shell=True`、`eval/exec`、`APP_TOKEN`、`persist-credentials: true`、
     `workflow_dispatch/pull_request_target`、写权限、网络调用、`subprocess`）。
  2. **逐行源码阅读**：20 个高风险文件全部读源码并核对不变量（见 §3）。
  3. **不变量判定**：用可复现命令（grep/计数/YAML 解析/测试）验证，而不是"看起来没问题"。
* 本轮**没有**任何平台写操作：没有合并、没有写 main、没有改 Ruleset/变量/权限/Secrets、
  没有创建 `state/gate`、没有开启生产开关。

### 1.1 审计方法上的一个坑（必须记录）

本机读取 workflow 时，终端输出把 `Authorization: Bearer ${GITHUB_TOKEN}` 显示成
`Authorization: Bearer ***`（输出层做了凭据遮盖）。如果只看屏幕输出，会误判为
"token 被硬编码/被写成占位符"。本轮改用**计数**判定：

```
Authorization: Bearer ${GITHUB_TOKEN} → 6 处
Bearer $GITHUB_TOKEN                  → 0 处
Bearer ***                            → 0 处（屏幕上看到的 *** 是遮盖，不是文件内容）
APP_TOKEN                             → 0 处
GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} → 14 处
```
`[本轮实测]`

---

## 2. 覆盖清单（代码/测试/既有文档 57/57）

| # | 文件 | 行数 | 风险 | 审计动作 |
|---|---|---|---|---|
| 1 | `.github/CODEOWNERS` | 22 | 中 | 结构扫描 + 关键段阅读 |
| 2 | `.github/required-checks.json` | 53 | 中 | 结构扫描 + 关键段阅读 |
| 3 | `.github/workflows/candidate-pr-sync.yml` | 687 | 高 | 逐行读源码 + 不变量判定 |
| 4 | `.github/workflows/daily-sync.yml` | 37 | 中 | 结构扫描 + 关键段阅读 |
| 5 | `.github/workflows/pr-verify.yml` | 327 | 高 | 逐行读源码 + 不变量判定 |
| 6 | `.github/workflows/route-a-status.yml` | 80 | 高 | 逐行读源码 + 不变量判定 |
| 7 | `docs/audits/p1-risk-reverification.md` | 33 | 低 | 与源码交叉核对 |
| 8 | `docs/audits/pr29-p1-final-report.md` | 195 | 低 | 与源码交叉核对 |
| 9 | `docs/audits/pr29-p1-report.md` | 242 | 低 | 与源码交叉核对 |
| 10 | `docs/audits/pr29-route-a-d15-convergence-report.md` | 191 | 低 | 与源码交叉核对 |
| 11 | `docs/audits/root-cause-and-preflight.md` | 28 | 低 | 与源码交叉核对 |
| 12 | `docs/change-classification.md` | 31 | 低 | 与源码交叉核对 |
| 13 | `docs/d-15-state-plane.md` | 187 | 低 | 与源码交叉核对 |
| 14 | `docs/pr-first-automation.md` | 131 | 低 | 与源码交叉核对 |
| 15 | `scripts/ci/await_checks.py` | 300 | 高 | 逐行读源码 + 不变量判定 |
| 16 | `scripts/ci/candidate_sha.py` | 159 | 高 | 逐行读源码 + 不变量判定 |
| 17 | `scripts/ci/check_provenance.py` | 348 | 高 | 逐行读源码 + 不变量判定 |
| 18 | `scripts/ci/classify_change.py` | 194 | 高 | 逐行读源码 + 不变量判定 |
| 19 | `scripts/ci/decide_merge.py` | 114 | 高 | 逐行读源码 + 不变量判定 |
| 20 | `scripts/ci/evaluate_candidate_policy.py` | 306 | 高 | 逐行读源码 + 不变量判定 |
| 21 | `scripts/ci/gate_lock.py` | 326 | 高 | 逐行读源码 + 不变量判定 |
| 22 | `scripts/ci/gate_state.py` | 372 | 高 | 逐行读源码 + 不变量判定 |
| 23 | `scripts/ci/gate_status.py` | 125 | 高 | 逐行读源码 + 不变量判定 |
| 24 | `scripts/ci/platform_rulesets.py` | 303 | 高 | 逐行读源码 + 不变量判定 |
| 25 | `scripts/ci/publish_run_status.py` | 239 | 高 | 逐行读源码 + 不变量判定 |
| 26 | `scripts/ci/publish_status.py` | 167 | 高 | 逐行读源码 + 不变量判定 |
| 27 | `scripts/ci/recover_gate_state.py` | 118 | 中 | 结构扫描 + 关键段阅读 |
| 28 | `scripts/ci/request_automerge.py` | 264 | 高 | 逐行读源码 + 不变量判定 |
| 29 | `scripts/ci/verify_approval.py` | 138 | 高 | 逐行读源码 + 不变量判定 |
| 30 | `scripts/ci/verify_candidate.py` | 215 | 高 | 逐行读源码 + 不变量判定 |
| 31 | `scripts/ci/verify_general_pr.py` | 1013 | 中 | 结构扫描 + 关键段阅读 |
| 32 | `scripts/ci/verify_policy_baseline.py` | 1202 | 中 | 结构扫描 + 关键段阅读 |
| 33 | `scripts/ci/verify_production_prerequisites.py` | 393 | 高 | 逐行读源码 + 不变量判定 |
| 34 | `scripts/ci/verify_required_checks.py` | 205 | 高 | 逐行读源码 + 不变量判定 |
| 35 | `scripts/ci/verify_workflow_policy.py` | 621 | 中 | 结构扫描 + 关键段阅读 |
| 36 | `scripts/ci/workflow_command_contract.py` | 1165 | 中 | 结构扫描 + 关键段阅读 |
| 37 | `scripts/tests/prov_fixture.py` | 135 | 中 | 检查断言强度/矩阵登记 |
| 38 | `scripts/tests/test_approval_and_decide.py` | 154 | 中 | 检查断言强度/矩阵登记 |
| 39 | `scripts/tests/test_await_checks.py` | 270 | 中 | 检查断言强度/矩阵登记 |
| 40 | `scripts/tests/test_candidate_sha.py` | 129 | 中 | 检查断言强度/矩阵登记 |
| 41 | `scripts/tests/test_check_provenance.py` | 403 | 中 | 检查断言强度/矩阵登记 |
| 42 | `scripts/tests/test_classify_change.py` | 134 | 中 | 检查断言强度/矩阵登记 |
| 43 | `scripts/tests/test_failure_matrix_coverage.py` | 159 | 中 | 检查断言强度/矩阵登记 |
| 44 | `scripts/tests/test_gate_lock.py` | 202 | 中 | 检查断言强度/矩阵登记 |
| 45 | `scripts/tests/test_gate_state.py` | 193 | 中 | 检查断言强度/矩阵登记 |
| 46 | `scripts/tests/test_gate_status.py` | 164 | 中 | 检查断言强度/矩阵登记 |
| 47 | `scripts/tests/test_general_pr_guard.py` | 932 | 中 | 检查断言强度/矩阵登记 |
| 48 | `scripts/tests/test_oasisic_revision_contract.py` | 587 | 中 | 检查断言强度/矩阵登记 |
| 49 | `scripts/tests/test_platform_rulesets.py` | 248 | 中 | 检查断言强度/矩阵登记 |
| 50 | `scripts/tests/test_policy_baseline_guard.py` | 918 | 中 | 检查断言强度/矩阵登记 |
| 51 | `scripts/tests/test_production_preflight.py` | 332 | 中 | 检查断言强度/矩阵登记 |
| 52 | `scripts/tests/test_publish_run_status.py` | 223 | 中 | 检查断言强度/矩阵登记 |
| 53 | `scripts/tests/test_request_automerge.py` | 261 | 中 | 检查断言强度/矩阵登记 |
| 54 | `scripts/tests/test_required_checks_guard.py` | 218 | 中 | 检查断言强度/矩阵登记 |
| 55 | `scripts/tests/test_route_a_protocol.py` | 213 | 中 | 检查断言强度/矩阵登记 |
| 56 | `scripts/tests/test_runtime_policy_wiring.py` | 420 | 中 | 检查断言强度/矩阵登记 |
| 57 | `scripts/tests/test_workflow_policy_guard.py` | 1423 | 中 | 检查断言强度/矩阵登记 |

---

## 3. 不变量判定（逐条可复现）

| # | 不变量 | 判定 | 证据 |
|---|---|---|---|
| I1 | 只有 `request_automerge.py` 能发合并 mutation | 成立 | `/pulls/*/merge` 只出现在 `workflow_command_contract.py`、`verify_policy_baseline.py`、`verify_workflow_policy.py` 的**拒绝正则**中 `[本轮实测]` |
| I2 | 不存在 App/PAT 路径 | 成立 | `APP_TOKEN`/`create-github-app-token`/`PR_FIRST_APP` 在 workflow 中 0 次；`verify_workflow_policy` P17 结构化扫描 `[本轮实测]` |
| I3 | 所有 checkout 不落盘凭据 | 成立 | 12 处 `actions/checkout` 全部 `persist-credentials: false` `[本轮实测]` |
| I4 | 没有任何 job 用 `continue-on-error` 假绿 | 成立 | YAML 解析全部 workflow，`continue-on-error` 命中 0 `[本轮实测]` |
| I5 | 锁先于生成/候选写入 | 成立 | `generate` 的 `if` 要求 `needs.lock.result == 'success'`；policy guard 有对应判定 `[源码直接证明]` |
| I6 | 暂停/恢复态不能被普通 `complete` 清除 | 成立 | `gate_lock.complete` 拒绝 `BLOCKING_PHASES`；`--resume-paused` 已不存在，恢复走独立 `resume` 子命令（要求 reason/证据/generation/epoch）`[源码直接证明]` |
| I7 | 快照只在内容一致时幂等 | 成立 | `GitHubRefStore` 回读 tag 载荷 → 重算 canonical digest → 不一致 fail-closed `[源码直接证明]` |
| I8 | 审批必须是当前 head 的人类 APPROVED | 成立 | `verify_approval`：完整分页 + 最新 review + `state=APPROVED` + `commit_id==head` + `user.type=="User"`；`[源码直接证明]` |
| I9 | 决策器只出决策，不放行合并 | 成立 | `decide_merge` 返回 `decision/reason_codes/production_allowed`；`production_allowed` 还要求 `platform_ok` `[源码直接证明]` |
| I10 | 平台就绪必须证明"覆盖 main" | **本轮修复** | 见 F-01；现在 `platform_rulesets` 校验 target/enforcement/conditions/integration `[源码直接证明]` |
| I11 | 状态发布者身份双向校验 | **本轮修复** | 见 F-02 `[源码直接证明]` |
| I12 | 生产前置检查器只读 | 成立 | `verify_production_prerequisites.py` 只有 1 处 `urllib.request.Request`（GET），无 POST/PUT/DELETE；JSON 固定 `writes_performed=false` `[本轮实测]` |

---

## 4. 发现与处置

### F-01（P0，已修）平台判定把"其他分支的规则"当成 main 的规则

* 位置：`scripts/ci/evaluate_candidate_policy.py::_platform_state`（修复前 126–161 行）。
* 问题：遍历所有 `target=branch` 且 `enforcement=active` 的 ruleset，把 context **汇总**后
  判断 `route-a/verify in contexts`。只要**任意一个**无关分支的 ruleset 引用了同名 context，
  就会误判 main 已就绪 `[源码直接证明]`。
* 影响：平台就绪判定可被无关规则"传染"，进而让 `production_allowed` 在错误前提下变 true。
* 修复（`bdb15cd`）：新增 `scripts/ci/platform_rulesets.py`，要求
  ① `target=="branch"`；② `enforcement=="active"`；③ `conditions.ref_name` 证明覆盖
  `refs/heads/main`（`~DEFAULT_BRANCH` 需 API 证实的默认分支确为 main；支持
  `refs/heads/*`；`exclude` 优先）；④ context **精确相等**；⑤ `integration_id==15368`，
  缺字段=无法验证=不通过；⑥ 任一 ruleset 详情 403/404/500 → 整体不可证明。
* 回归测试：`test_platform_rulesets`（27 例）——其他分支、未启用、tag ruleset、近似名、
  来源不符、缺 conditions、403/500、分页中断、默认分支不可读。
* 是否需平台写：否。

### F-02（P1，已修）状态发布者只核对 login，未核对账号类型/来源

* 位置：`scripts/ci/publish_run_status.py::verify_published`（修复前 155–166 行）。
* 问题：`creator == "github-actions[bot]"` 即通过；没有 `creator.type`，也没有把 status
  绑定到**本次 run**。同一 SHA 上历史成功 status 会被误当成本次验证结果 `[源码直接证明]`。
* 修复（`bdb15cd`）：`login + type=="Bot"` 双向校验；并新增 `expect_run_id`，要求
  `target_url` 含 `/runs/<本次 run_id>`；`publish_for_run` 传入 run_id。
* 回归测试：`test_publish_run_status` 新增 3 例（type=User、缺 type、target_url 指向别的 run）。
* 是否需平台写：否。

### F-03（P2，未修，需用户裁定）审批人由"仓库 owner"推断，而非显式策略

* 位置：`evaluate_candidate_policy.collect_and_evaluate`（`approver = approver or base.repo.owner.login`）。
* 问题：当前唯一审批人 `@Hawaiine` 来自仓库 owner 推断；`verify_approval` 本身在
  `approver` 为空时 fail-closed，但"谁是审批人"没有显式配置文件，仓库转移/改名会静默改变语义。
* 风险等级：中（仍要求 `user.type=="User"` + 当前 head + APPROVED，不能绕过人工）。
* 建议：新增 `.github/approval-policy.json`（`approvers: ["Hawaiine"]`）作为唯一来源；
  缺失即 `NEW_RULESET` fail-closed。**此项涉及治理语义，等用户确认审批人名单后再改**。`[待用户批准]`

### F-04（P2，已核实为良性）`pr-verify.yml` 中 `git fetch ... || true`

* 位置：`.github/workflows/pr-verify.yml:287`。
* 判定：该行后紧跟 `cat-file -e "${PR_BASE_SHA}^{commit}"`（`set -euo pipefail` 下硬失败），
  fetch 失败不会让后续检查假绿 `[源码直接证明]`。保留（fetch 失败常因浅克隆，属可恢复）。
* 结论：不改。

### F-05（P3，文档债）历史审计文档与当前源码不一致

* `docs/audits/pr29-p1-final-report.md`、`pr29-route-a-d15-convergence-report.md` 曾断言
  "APP_TOKEN 0 残留"；在当前 head 该断言**成立**（实测 0 次），但这两份文档同时记录了
  已被后续提交修正的中间态，容易被误读为"当前状态"。
* 处置：不改写历史结论（保留审计轨迹），以本轮报告与 `docs/pr-first-automation.md` 为准；
  后续如需要，可在这两份文档顶部加"历史快照，当前状态见 …"的指针。`[待用户批准]`（涉及历史文档改写）

### F-06（P3，测试稳定性）满载时进程清理用例偶发超时

* `test_fetch_review.py::TestProcessCleanup::test_stubborn_grandchild_dies_even_when_leader_exits`
  在满载全量运行中偶发失败，单跑 3/3 通过；本轮未修改该测试、未删除断言。
* 处置：记录为已知抖动，建议后续单独加固 `waitpid` 轮询上限。

---

## 5. 本轮结论

* 57/57 文件纳入审计；2 个 P0/P1 缺陷已修复并有回归测试；1 个 P2 待用户裁定；2 个 P3 记录。
* 代码侧不存在"读失败继续""默认值放行""status 只凭 context 名采信"的路径（I1–I12）。
* **不能**据此宣称生产就绪：真实 Ruleset 未改、`state/gate` 未创建、候选 workflow 未在
  Runner 实跑（见 `docs/audits/pr29-four-gate-acceptance.md`）。
* 审计结论仅对应 head `690a1e5`；head 变化需按受影响范围重审。
