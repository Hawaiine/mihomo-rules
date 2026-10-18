# PR #29 阶段十三 P1 收敛报告（Route A / D-15 / 平台验收）

- 执行时间：2026-10-18 12:35 (Asia/Shanghai) / 04:35 UTC
- 仓库：`Hawaiine/mihomo-rules`（唯一修改对象）
- 分支：`feat/pr-first-automation`（PR #29）
- **本轮被测代码提交**：`99101d2cbebbbae9ffad173807812826f2845629`
- **被测代码树**：`54acd9dff7d4a5c149f395f42181fa5f35ebb7bf`
- **测试运行**：本地 `python3 -m unittest discover -s scripts/tests`（1099 tests）；CI run `39096723867`
- 报告提交：`<见 §13，本文件所在提交>`；最终 PR head 由 §13 给出
- 起始 head：`60e8273fb5829272eb1c94f8136662b57df735ac`（三个 check 均 success）
- base：`main @ f4447b73023474cb2f62de51277397fb8df911f0`
- 工作树：提交后 clean

> 说明：代码提交 `99101d2` 与报告提交分离。报告只增加 `docs/audits/` 下的 Markdown，
> 不改变代码树 —— 依据是 `git diff --stat 99101d2 <报告提交>` 只列出 docs 文件（§13 附命令）。

---

## 1. 当前 GitHub 状态基线 `[本轮实测]`

| 项 | 值 |
|---|---|
| PR #29 | `open`，`mergeable=true`，`mergeable_state=clean`，`changed_files=36`（提交前） |
| head / base | `60e8273…` → 本轮推送后 `99101d2…`；base `f4447b7…` |
| 检查（`99101d2`） | `general-pr-verify` success、`workflow-policy-guard` success、`trusted-baseline-guard` success |
| Rulesets（改动前） | `[]` |
| `main` 保护（改动前） | `404 Branch not protected` |
| `can_approve_pull_request_reviews`（改动前） | `false` |
| `state/gate` ref | `404`（不存在，本轮未创建） |
| `.github/CODEOWNERS`（改动前） | `404` |
| `@Hawaiine` | `200 {"login":"Hawaiine","type":"User"}` |

---

## 2. 根因台账（摘要，全文见 `docs/audits/root-cause-and-preflight.md`）

| 编号 | 根因（源码级） | 修复 | 反例预演 | 测试 |
|---|---|---|---|---|
| **P0-1** | 只有候选 workflow 发状态；人工 PR 的 `pull_request` 运行只产生 check-run，无人转成 status | 新增 `route-a-status.yml`（`workflow_run`，默认分支上下文）+ `publish_run_status.py`：核验 run 归属/事件/名称/head/全部 job，再核验 PR，发布后回读并复核 head | 事件非 PR、run 名不符、job skipped、缺 job、head/base 漂移、403、来源不可信、发布后漂移 | 13 例 |
| **P0-2** | 门控写成 `if: vars.GATE_STATE_ENABLED == 'true'` ⇒ 关闭时**跳过**安全前置而不是**阻止**同步 | `lock` job 常跑，首步 `require-enabled`（非 true 即失败）；`publish` 仅接受 `lock.result == success`；`finalize` 同样 | 未设置/false/空/非法；ref 缺失；锁占用；CAS 冲突；取消；finalize 失败 | 6 组取值 + 工作流结构 |
| **P0-3** | `choose_mode(check_runs, contexts)` 由**数据**决定模式 ⇒ 去掉 check-run 即可降级 | `expected_mode(pr_obj)` 由**作者身份**决定：bot→status，人工→check-run，未知→失败；无任何运行期回退 | 人工 PR 无 check-run 但有 status；bot 未声明 contexts；错 SHA；部分通过 | +3 例负向 |
| **P0-4** | GitHub ref 的"读-改-写"非原子；`force=false` 只在**协议层**近似 CAS；真实原子性未验证 | 保留 `generation + expected_old_sha + force=false` 契约；暂停/恢复/重放拒绝已实现；**明确标注 L3 未验证** | 并发/陈旧 SHA/暂停被清除/重放/摘要不符 | 17 + 10 例 |
| P1-5 | 自动合并不读 gate 状态 | `request-auto-merge` 依赖 `finalize`（fail-closed） | 暂停态合并 | 工作流 `needs` + 暂停用例 |
| P1-6 | 报告混用被测 SHA 与报告 SHA；进度公式与数字不一致 | 本报告 §13 分列 code/tree/report/PR head；§14 公开公式与结果 | 旧 SHA 绿色被当最终证据 | 本节 |
| P1-7 | 无 CODEOWNERS；Ruleset 不要求审查 | 新增 CODEOWNERS（@Hawaiine）；**不**开启 required_pull_request_reviews（理由见 §11） | owner 不存在/路径未覆盖/审查未强制被误报 | 文件 + API 核实 |
| P1-8 | `can_approve_pull_request_reviews=false` ⇒ Route A 建 PR 必 403 | 已按授权置 `true` 并回读 | 设置回读不一致 | API 回读 |

---

## 3. 本轮修改文件

| 文件 | 变更 | 理由 |
|---|---|---|
| `scripts/ci/publish_run_status.py` | 新增（~230 行） | P0-1 受信发布器 |
| `.github/workflows/route-a-status.yml` | 新增 | P0-1 `workflow_run` 触发 |
| `scripts/ci/gate_lock.py` | 新增 `require-enabled` | P0-2 硬前置 |
| `.github/workflows/candidate-pr-sync.yml` | `lock` 常跑 + `require-enabled`；`publish`/`finalize` 只认 lock success | P0-2 |
| `scripts/ci/gate_status.py` | `choose_mode` → `expected_mode`（作者驱动，无降级） | P0-3 |
| `scripts/ci/await_checks.py` / `verify_required_checks.py` | 接入显式模式；缺 check-run 不再回退 | P0-3 |
| `.github/CODEOWNERS` | 新增 | P1-7 |
| `docs/audits/root-cause-and-preflight.md` | 新增 | 本轮要求的预演台账 |
| `scripts/tests/{test_gate_lock,test_gate_status,test_publish_run_status}.py` | 新增/扩充 | 故障注入 |
| `scripts/tests/{prov_fixture,test_request_automerge}.py` | 补 `user` 字段 | 适配作者驱动模式 |

---

## 4. 关键实现说明

**P0-1**：`verify_run()` → `verify_pr()` → `publish()` → `verify_published()` → `verify_pr()`。
每一步失败即 `Refused`，**没有**"发布失败但继续"的路径；状态只发布 `success`，失败场景由 workflow
本身 failure 表达（不发布虚假 success）。

**P0-2**：安全前置从"可跳过"改为"必需"。`GATE_STATE_ENABLED` 非 `"true"`（大小写不敏感、去空白）
时 `require-enabled` 退出 1，`generate` 因 `needs.lock.result != success` 不会运行 ⇒ 不产生候选补丁、
不写分支、不建 PR。

**P0-3**：模式由 PR 作者决定（`github-actions[bot]` → status；其他 → check-run；缺失 → 失败）。
`verify_required_checks` 在 check-run 模式且 `check_runs == []` 时直接判失败（"拒绝降级"）。

---

## 5. 测试结果（精确 SHA）`[本轮实测]`

| 命令 | rc | 结果 | SHA |
|---|---|---|---|
| `python3 -m unittest discover -s scripts/tests`（`GITHUB_RUN_ID=999`） | 0 | `Ran 1099 tests ... OK (skipped=1)` | `99101d2` / tree `54acd9d` |
| `verify_workflow_policy.py` | 0 | 4 个 workflow 通过 | 同上 |
| `verify_general_pr.py` | 0 | `all invariants satisfied` | 同上 |
| `verify_policy_baseline.py` | 0 | 通过（`base 逻辑校验=False`） | 同上 |
| `verify_configs.py` | 0 | 4 变体 PASS | 同上 |
| `verify_rulesets.py` | 0 | `FAIL: 0` | 同上 |
| `readme_stats.py --check` | 0 | 一致 | 同上 |
| `git diff --check` | 0 | 无空白错误 | 同上 |
| CI（`99101d2`） | — | 3 checks success，run `39096723867` | `99101d2` |

**故障注入覆盖**：403（发布器/状态发布）、422（CAS 冲突）、404（ref/快照缺失）、并发写者、
陈旧 expected SHA、暂停被普通 complete 清除、恢复重放/回退/摘要不符、head/base 漂移、
job skipped、缺 job、来源不可信、重复发布。

---

## 6. 真实平台验收

| 项 | 结果 | 证据 |
|---|---|---|
| Actions 创建 PR（M1） | **已启用** | `PUT …/actions/permissions/workflow` → 204；回读 `can_approve_pull_request_reviews=true`，`default_workflow_permissions=read` |
| 建 PR 端到端 | **NOT TESTED** | 需运行 daily-sync；生产同步未启用（禁止），故未跑 |
| 状态发布（人工 PR） | **NOT TESTED** | `route-a-status.yml` 需先在 main 才生效（`workflow_run` 只从默认分支运行） |
| 状态被 Ruleset 识别 | **UNVERIFIABLE** | Ruleset 目前**未**要求 `route-a/verify`（见 §7 顺序原因） |
| `state/gate` 真实写入/CAS/恢复 | **UNVERIFIABLE** | M3 未授权；未创建 ref |

---

## 7. M2 Ruleset 实际配置 `[本轮实测]`

`POST /repos/Hawaiine/mihomo-rules/rulesets` → `201`，id **25330782**，`name=protect-main`：

```json
{"target":"branch","enforcement":"active",
 "conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},
 "rules":[{"type":"deletion"},{"type":"non_fast_forward"},
          {"type":"required_status_checks","parameters":{
            "strict_required_status_checks_policy":true,
            "required_status_checks":[
              {"context":"general-pr-verify","integration_id":15368},
              {"context":"workflow-policy-guard","integration_id":15368},
              {"context":"trusted-baseline-guard","integration_id":15368}]}}],
 "bypass_actors":[],"current_user_can_bypass":"never"}
```

`GET /rules/branches/main` → `["deletion","non_fast_forward","required_status_checks"]`；
PR #29 `mergeable_state=clean`（说明必需检查确实被平台识别）。

**为什么暂不把 `route-a/verify` 加入必需状态**：该 context 的两个发布者中，`route-a-status.yml`
只有在**合并进 main 之后**才会运行（`workflow_run` 的固定行为）。若现在就要求它，任何人（包括
本 PR）都无法合并，形成死锁。正确顺序：合并本 PR → 确认发布器在 main 上可用 → 再追加该 context。

---

## 8. D-15 分层验收

| 层 | 结论 | 依据 |
|---|---|---|
| L1 状态机/不变量 | **PASS** | 17 例（并发、摘要、非法组合）+ 10 例锁 CLI |
| L2 故障注入（stub/线程） | **PASS** | CAS 422/403/404、暂停不可清除、恢复拒绝矩阵 |
| L3 真实持久化/并发/恢复 | **PLATFORM BLOCKED** | `state/gate` 不存在；M3 明确"先设计后实施" |

**D-15 尚缺的设计要点（M3 提交前必须补）**：
1. `RECOVERY_REQUIRED` 显式状态与"未知来源锁不自动清除"的判定条件；
2. 真实 ref 上"两个 runner 同时 CAS"的实测方案（谁持有 expected_old_sha、如何证明只有一个成功）；
3. 快照保留策略（30 代 + final-review 永久）与删除器审计；
4. 恢复操作者身份记录（当前 `operator` 未写入状态）。

---

## 9. M4 CODEOWNERS

- 文件：`.github/CODEOWNERS`（本轮新增，兜底 `*` + `.github/**` + `scripts/ci/**` + `configs/**` + `ruleset/**` 均为 `@Hawaiine`）。
- 账户核实：`GET /users/Hawaiine` → `type=User`，存在。
- **生效条件**：CODEOWNERS 只在默认分支上生效 ⇒ 本文件合并后生效。
- **审查是否强制**：Ruleset `protect-main` **未**配置 `required_pull_request_reviews`。
  原因：强制审查会让机器人候选 PR 永久等待人工批准，与 PR-first 自动化的目标直接冲突；
  且当前只有单一维护者，自我批准需要额外权限。**因此"审查保护"目前只是责任人声明，不是强制门禁**。

---

## 10. PR #29 去留

**保留**。依据：36 文件全部落在 ci/docs/tests 与 CODEOWNERS；认证与门禁已收敛为单一实现；
P0-1/2/3 已在同一分支修复并有负向测试；无结构性耦合需要推倒重来。未创建替代 PR。

---

## 11. 未解决 / 需用户决策

| # | 事项 | 类型 | 需要 |
|---|---|---|---|
| 1 | `route-a/verify` 何时加入 Ruleset 必需状态 | 平台时序 | 合并本 PR 后由我追加（无需再授权，属既定契约） |
| 2 | 是否强制 PR 审查（required_pull_request_reviews） | 治理 | **需决策**：强制会阻塞机器人 PR |
| 3 | M3：`state/gate` 创建 + D1–D7 | 平台写入 | **需批准**（本轮按 2.5 未实施） |
| 4 | 真实 E2E（daily-sync 跑一次） | 生产 | **需批准**启用 `CANDIDATE_SYNC_ENABLED` |
| 5 | 18 个来源阻塞品牌 | 治理 | 保持阻塞（不在本轮） |

---

## 12. 边界确认

- ✅ 未合并任何 PR；未写入/推送 `main`
- ✅ 未使用 App/PAT（`grep` 与守卫双重证明）
- ✅ 未修改只读仓库；未做品牌 Ruleset 大规模变更
- ✅ 平台变更**仅限**用户明确授权的两项：M1 设置、M2 `protect-main` Ruleset
- ✅ 未创建 `state/gate`（M3 未授权）
- ✅ 未启用生产同步（`CANDIDATE_SYNC_ENABLED` / `CANDIDATE_AUTOMERGE_ENABLED` 未设置）
- ⚠️ 本轮确有平台写入（用户已授权）：`PUT actions/permissions/workflow`、`POST rulesets`

---

## 13. SHA 溯源表

| 名称 | 值 |
|---|---|
| 被测代码提交 | `99101d2cbebbbae9ffad173807812826f2845629` |
| 被测代码树 | `54acd9dff7d4a5c149f395f42181fa5f35ebb7bf` |
| 本地测试 | `1099 tests OK (skipped=1)`，Python 3.14.7 |
| CI 运行 | `https://github.com/Hawaiine/mihomo-rules/actions/runs/39096723867` |
| 报告提交 | 本文件所在提交（仅 docs；`git diff --stat 99101d2 HEAD` 应只列 `docs/audits/*`） |
| 最终 PR head | 由本轮最后一次 push 决定（见交付消息） |
| base | `f4447b73023474cb2f62de51277397fb8df911f0` |

---

## 14. 进度（公开公式，可复算）

权重：设计开发 40% / 代码开发 30% / 独立审计覆盖 20% / 生产准备度 10%。

| 维度 | 完成度 | 依据 |
|---|---:|---|
| 设计开发 | 0.85 | P0-1/2/3 设计定稿并落地；M3 状态机设计仍待审查 |
| 代码开发 | 0.90 | 1099 tests、4 workflow、P0 全修；L3 相关代码未实测 |
| 独立审计覆盖 | 0.25 | 仅 ChatGPT 复审 + 自建测试；无第三方复核 |
| 生产准备度 | 0.35 | M1+M2 已生效；无真实 E2E、无 state ref、无生产启用 |

```
0.4×0.85 + 0.3×0.90 + 0.2×0.25 + 0.1×0.35
= 0.340 + 0.270 + 0.050 + 0.035
= 0.695 → 69.5% ≈ 70%
```

分项：阶段十三 P1 **70%**；全项目 **约 62%**（含品牌治理 40%、D-15 L3 0%、平台 70%、审计 25% 加权）；
**生产准备度 35%**（不等于代码完成度）。

---

## 15. 结论

**CONDITIONAL PASS**

- 代码层：P0-1 / P0-2 / P0-3 / P0-4(L1-L2) 全部修复并有负向测试，全量 1099 tests 通过，CI 三检查在
  `99101d2` 上 success；
- 平台层：M1、M2 已实际生效并回读；但 **Route A 端到端未验证**（`workflow_run` 需在 main、生产未启用），
  D-15 真实持久化未验证（M3 未授权）；
- **不得**表述为 production-ready，也不得把 CONDITIONAL PASS 用于掩盖上述未验证项。
