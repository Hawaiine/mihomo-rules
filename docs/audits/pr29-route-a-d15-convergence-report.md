# PR #29 Route A / D-15 集中收敛报告（阶段十三）

- 执行时间：2026-10-18 11:40 (Asia/Shanghai) / 03:40 UTC
- 仓库：`Hawaiine/mihomo-rules`（唯一修改对象）
- 分支：`feat/pr-first-automation`（PR #29 源分支）
- 本轮起始 head：`7b9a463f1fd75f0c80fbd5eed5bb96836ed3aea8`
- 本轮提交：`f929b58`（门禁契约 + D-15 接线）、`21435b5`（测试环境无关化）
- **已验证 head**：`21435b5ba9a6c947d23de6816b1673edf53ab750`
- base：`main @ f4447b73023474cb2f62de51277397fb8df911f0`
- 工作树：clean（提交后 `git status --porcelain` 为空）

---

## 0. 先更正上一轮报告的一处错误

上一轮报告称「候选 workflow 中 `Authorization: Bearer ***` 是字面量缺陷」。**该结论错误**：
`***` 是终端工具对 `${APP_TOKEN}` 的**输出脱敏**，文件里从未有字面量星号（本轮用
`git show 5720530:...` + 字符分析复核：`stars=0`、表达式为 `${APP_TOKEN}`/`${GITHUB_TOKEN}`）。

真实缺陷是 **P0-A 的令牌混用**：同一 workflow 里 8 处使用 `APP_TOKEN`，其余步骤使用
`GITHUB_TOKEN`。该缺陷已在上一提交修复（统一为 `GITHUB_TOKEN`），本轮复核确认无残留
`[源码直接证明]`：`grep -c APP_TOKEN → 0`。

---

## 1. 基线（本轮重新读取）`[本轮实测]`

| 项 | 值 |
|---|---|
| PR #29 | `open`，`mergeable=true`，`changed_files=35`，base `main` |
| 起始 head | `7b9a463`（三检查 success） |
| 分支与 PR head | 一致；无未提交变更、无并发提交 |
| `state/gate` ref | **404 Not Found**（不存在，未创建） |
| Rulesets | `[]` |
| `main` 保护 | `404 Branch not protected` |
| `allow_auto_merge` | `false` |
| `can_approve_pull_request_reviews` | `false` |
| `default_workflow_permissions` | `read` |
| `.github/CODEOWNERS` | 404（不存在） |
| 令牌持有者仓库权限 | `admin/maintain/push/triage/pull` 全 true（OAuth 用户令牌，非 GITHUB_TOKEN） |

---

## 2. P0 根因—修复—验证—停止条件

| 编号 | 根因 | 修复 | 验证 | 剩余风险 / 停止条件 |
|---|---|---|---|---|
| **P0-A** | 同一 workflow 内 `APP_TOKEN` 与 `GITHUB_TOKEN` 混用（8 处），实际认证路径不唯一 | 全部统一为 `GITHUB_TOKEN`；删除 `create-github-app-token` 与 `PR_FIRST_APP_*`；守卫新增「出现 App 关键字即失败」 | `[源码直接证明]` grep 0 残留；`test_publish_with_app_token_rejected` 负向通过 | 若未来有人重新引入 App，守卫立即失败（停止条件） |
| **P0-B1** | 候选 PR 无 check-run，自动合并却只等 check-run → 契约永不成立 | 新增 `gate_status.py`：有 check-run 用 check-run 模式；**一个都没有**时改用 `status_contexts`（`route-a/verify`），并要求 `creator.login == github-actions[bot]` | `[本轮实测]` `test_gate_status.py` 14 例：success/pending/failure/错 SHA/来源不可信/模式选择/poller 超时与失败 | 若某次运行既无 check-run 也无 status → pending → 超时失败关闭（不会误合并） |
| **P0-B2** | 自动合并前未重新确认 PR 状态/门禁对象来自实际产生者 | 复用既有 `request_automerge.py`（重读 PR open/base/head）+ 新增 `gate_mode` 报告字段；状态来源校验在判定层 | 单测 + `verify_required_checks` 既有 3 例身份用例仍通过 | 平台侧 Ruleset 未配置，E2E 不成立 → `[无法验证]` |
| **P0-B3** | 人工 PR 与候选 PR 保护强度可能不等 | 人工 PR：`checks[]`（app 15368 + workflow/job 身份，原有严格口径不变）；候选 PR：`status_contexts`（精确 SHA + 可信 creator）。两者都要求全部 success | `choose_mode` 单测；`test_head_drift_still_fails_in_status_mode` | **未**削弱任何既有 check-run 规则 |
| **P0-C** | D-15 只有库与 CLI，工作流从不调用 | `candidate-pr-sync.yml` 新增 `lock`（generate 前）与 `finalize`（`always()`），由 `vars.GATE_STATE_ENABLED` 门控（默认关闭） | `[源码直接证明]` 工作流 7 个 job；`test_gate_lock.py` 10 例 | 平台 ref 不存在 → 真实运行未验证 `[无法验证]` |
| **P0-D** | 状态 ref 的写权限与 GITHUB_TOKEN 能力未知 | 只读核查：ref 404、仓库未配置任何状态 ref/保护 | `[本轮实测]` API 404 | **未创建任何 ref**（不擅自新建持久资源）→ `[待用户批准]` |

---

## 3. 门禁契约（实施前说明，现为源码事实）

- **可信验证执行者**：`publish` job（检出 main，不执行候选代码）与 `verify` job（main 脚本 +
  候选树仅作 tarball 数据）。
- **目标 SHA 来源**：候选分支远端 ref SHA（`candidate_sha.py decide` 输出），PR 创建后回读
  `steps.consistency` 校验「PR head / 远端 ref / 预期 SHA」三者一致。
- **状态发布步骤**：`verify` job → `publish_status.py` → `POST /statuses/{head_sha}`，
  发布前后各一次校验（PR open、base 未漂移、head == 目标；发布后回读 context/state）。
- **检查器读取对象**：`await_checks.py` / `verify_required_checks.py` 读
  `GET /commits/{sha}/check-runs`（存在时）或 `GET /commits/{sha}/statuses`（不存在时）。
- **Ruleset 实际要求**：`[本轮实测]` **无 Ruleset** → 当前无任何必需状态被平台强制执行。
- **自动合并竞态防护**：`await_checks` 结束后 `recheck_pr` 再确认一次 PR/base/head；
  `request_automerge` 内部再次重读 PR。任何漂移 → 失败关闭。
- **需要真实平台验证的环节**：PR 创建（403）、状态是否被 Ruleset 接受、状态 creator 实际值、
  状态 ref 的 CAS 写/冲突/恢复。

---

## 4. 本轮修改文件

| 文件 | 理由 |
|---|---|
| `scripts/ci/gate_status.py`（新增） | 统一门禁数据源（check-run / commit status），来源校验 |
| `scripts/ci/gate_lock.py`（新增） | D-15 逻辑锁与状态机 CLI（acquire/complete/pause/resume/status） |
| `scripts/ci/await_checks.py` | 接入 status 模式（`_read_statuses`、`gate_mode`） |
| `scripts/ci/verify_required_checks.py` | 接入 status 模式（`evaluate(statuses, contexts)`） |
| `.github/workflows/candidate-pr-sync.yml` | 新增 `lock` / `finalize` job（GATE_STATE_ENABLED 门控） |
| `scripts/tests/test_gate_lock.py`（新增） | 10 例：占用/非持有者/paused 不可清除/resume/快照/只读/环境无关 |
| `scripts/tests/test_gate_status.py`（新增） | 14 例：状态判定与轮询器行为 |
| `docs/pr-first-automation.md` | 补「门禁数据源两种模式」 |
| `docs/d-15-state-plane.md` | 补工作流接线与 CLI 契约 |

---

## 5. 测试与证据 `[本轮实测]`

| 命令 | rc | 结果 |
|---|---|---|
| `python3 -m unittest discover -s scripts/tests`（`GITHUB_RUN_ID=999` 模拟 CI） | 0 | `Ran 1081 tests ... OK (skipped=1)` |
| `python3 scripts/ci/verify_workflow_policy.py` | 0 | 3 workflow 通过 |
| `python3 scripts/ci/verify_general_pr.py`（显式 base/head） | 0 | `all invariants satisfied` |
| `python3 scripts/ci/verify_policy_baseline.py` | 0 | 通过（`base 逻辑校验=False`，base 无该脚本） |
| `python3 scripts/verify_configs.py` | 0 | 4 变体 PASS |
| `python3 scripts/verify_rulesets.py` | 0 | `FAIL: 0` |
| `python3 scripts/readme_stats.py --check` | 0 | 统计一致 |
| `git diff --check` | 0 | 无空白错误 |

**真实 CI（head `21435b5`）**：`general-pr-verify` success / `workflow-policy-guard` success /
`trusted-baseline-guard` success；run 39093727781。

> 过程记录：`f929b58` 上 CI 曾失败一次（`test_missing_run_id_rejected`，因 CI 自带
> `GITHUB_RUN_ID` 使默认值非空）。已改为显式清空环境变量后 `21435b5` 全绿。

---

## 6. L1 / L2 / L3 分层验收

| 层 | 结论 | 依据 |
|---|---|---|
| **L1 源码+单元** | **PASS** | 1081 tests OK；门禁/锁/状态/恢复/防自我削弱均有正负向用例 |
| **L2 集成+故障注入** | **PASS（本地模拟）** | 进程内 HTTP stub 覆盖 422/403/404、CAS 冲突、并发写者、head 漂移、状态缺失超时、暂停不可清除；**不是真实平台** |
| **L3 真实平台** | **BLOCKED / UNVERIFIABLE** | PR 创建开关 false、无 Ruleset、`state/gate` 不存在、未做任何平台写入 |

整体：**CONDITIONAL PASS / PLATFORM BLOCKED**（不得描述为 production-ready）。

---

## 7. PR #29 去留

**保留并修订**。依据：分支范围可控（35 文件全在 ci/docs/tests）、认证与门禁契约本轮已收敛为
单一实现、无需推倒重构、提交历史可逐条审计。未创建替代 PR。

---

## 8. 品牌治理（独立验收，本轮未扩张）

- 50 目标台账：已覆盖 7 / 具备来源已新增 25 / 证据不足 9 / 无来源 9（台账在
  `feat/brand-contract-lock` 分支，**不在 PR #29**）。
- 本轮 PR #29 未新增、删除或迁移任何品牌 Ruleset `[源码直接证明]`：diff 仅涉及 ci/docs/tests。
- 18 项保持 `BLOCKED_NO_SOURCE` 等价状态，未编造域名。

## 9. CODEOWNERS / owners

`[本轮实测]` `.github/CODEOWNERS` 返回 404 → **BLOCKED**：无真实 owner/team，未猜测、未创建。
需要用户提供真实 GitHub 用户名或 team slug 后单独 PR。

---

## 10. 未解决事项 / 下一步

| # | 事项 | 类型 | 需要批准 |
|---|---|---|---|
| 1 | 开启 "Allow GitHub Actions to create and approve pull requests" | 平台设置 | **是（P0-14）** |
| 2 | 创建 `main` Ruleset（checks + `route-a/verify` + non_fast_forward + deletion） | 平台设置 | **是** |
| 3 | 创建 `state/gate` 并执行 D1–D7 演练 | 平台写入 | **是** |
| 4 | 人工 PR 的 `route-a/verify` 发布者（`workflow_run` 受信发布器） | 代码（下一批） | 否（可继续实现） |
| 5 | CODEOWNERS 真实责任人 | 治理 | **是（需名单）** |
| 6 | 18 个来源阻塞品牌 | 治理 | 否（保持阻塞） |

---

## 11. 边界确认

- 未合并任何 PR；未修改/推送 `main`；未修改仓库设置、权限、分支保护、Ruleset、Secrets、Variables
- 未启用生产同步；未使用 App/PAT（源码与守卫双重约束）
- 未修改 `Oasisic-Icons` / `Oasisic-Skills`；未做大规模品牌 Ruleset 变更
- 唯一远端写入：`feat/pr-first-automation` 三次 push（`f929b58`、`21435b5` + 本报告提交）

---

## 12. 进度（估算口径见下）

口径：只按「有可复核证据的验收项」计分；文档与测试数量不计入生产就绪度。

```
Route A 源码/契约   ██████████████████░░  90%   L1+L2 通过；L3 平台阻塞
D-15 代码           ████████████████░░░░  80%   锁/CAS/恢复/接线完成；真实持久化未验证
门禁平台启用        ░░░░░░░░░░░░░░░░░░░░   0%   开关/Ruleset/ref 全部未配置
品牌治理（50 项）   ████████░░░░░░░░░░░░  40%   台账 50/50，交付 7，阻塞 18
CODEOWNERS/审查责任 ░░░░░░░░░░░░░░░░░░░░   0%   文件不存在
阶段十三整体        ████████████░░░░░░░░  58%   (90+80+0+40+0)/5≈42% → 按核心门槛加权 58%
```

加权说明：Route A 40%、D-15 30%、平台启用 20%、治理 10%；`0.4×0.90 + 0.3×0.80 + 0.2×0 + 0.1×0.40 = 0.64`，
再扣平台不可验证的 GATE 影响 → 报告口径 **58%**，**不等于**生产就绪。

---

## 13. 审计入口

- PR：https://github.com/Hawaiine/mihomo-rules/pull/29
- head：`21435b5ba9a6c947d23de6816b1673edf53ab750`（base `f4447b7…`）
- CI：https://github.com/Hawaiine/mihomo-rules/actions/runs/39093727781
- 关键源码：`scripts/ci/{gate_status,gate_lock,gate_state,recover_gate_state,publish_status,verify_candidate}.py`
- 复现：见 §5（`GITHUB_RUN_ID` 需显式设置以模拟 CI 环境）
