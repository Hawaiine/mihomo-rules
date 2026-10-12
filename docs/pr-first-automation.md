# PR-first 全自动更新（设计与运行契约）

本文件描述 `Hawaiine/mihomo-rules` 的日常更新如何在不依赖任何人工/ChatGPT 审批的前提下，
通过 GitHub 原生 Ruleset + Actions + 自动 PR + Auto-merge 安全运行。

> 一句话：**自动更新只能提出候选 PR；能不能合并由 GitHub Ruleset 与真实 required checks 决定。**

## 1. 运行时流程

```
schedule/workflow_dispatch (候选更新, 默认关闭)
        │  vars.CANDIDATE_SYNC_ENABLED == 'true'
        ▼
generate  (contents: read)      ← 只读：拉上游、生成规则、跑全量 guard 与单测
        │  产出 candidate.patch（纯数据）+ changed-files.txt
        ▼
publish   (contents/pull-requests/actions: write)  ← 不执行候选内容，只应用补丁
        │  1) 建/更新 candidate/rules-update（断言：绝不推 main）
        │  2) 创建或更新 PR（目标 main，幂等）
        │  3) workflow_dispatch 显式触发 pr-verify（ref=main）
        ▼
pr-verify (contents: read)      ← 三个必需检查：general-pr-verify / workflow-policy-guard / trusted-baseline-guard
        │
        ▼
request-auto-merge (默认关闭, vars.CANDIDATE_AUTOMERGE_ENABLED == 'true')
        │  1) verify_required_checks.py：真实 check run ↔ PR head SHA 绑定验证
        │  2) 通过后才调用 GraphQL enablePullRequestAutoMerge
        ▼
GitHub Ruleset 判定 → 合并（或保持 PR 待诊断）
        │
        ▼
合并后回读：PR 状态 / merge commit / main HEAD / 必需检查
```

**关键点**：ChatGPT 不在上述任何一步中；日常运行不要求人工或 ChatGPT 审阅、回复、批准。
ChatGPT 只在本系统**首次实现阶段**做一次性独立审计（即本 PR）。

## 2. 必需检查（Ruleset 精确引用）

以 `.github/required-checks.json` 为单一来源：

| check 名 | 作用 |
|---|---|
| `general-pr-verify` | 通用只读 guard、`verify_configs`、`verify_rulesets`、生产图标 URL 可达性、README 统计口径、全量单测 |
| `workflow-policy-guard` | workflow 权限/触发/目标分支/必需检查/防绕过/候选分支约束 |
| `trusted-baseline-guard` | 用 **base 版本** guard 逻辑校验候选树 + 削弱硬失败断言 |

约束：job id 与 job name 必须一致（否则 check 名漂移、ruleset 引用失效）；
`scripts/ci/verify_workflow_policy.py` 会校验本清单与 `pr-verify.yml` 的 job 集合完全一致。

## 3. 可信验证基线（防「自证循环」）

同一个 PR 若既改检查规则又靠改后的规则通过，就形成自证循环。因此：

1. 变更触及安全敏感路径（`.github/workflows/**`、`.github/required-checks.json`、`scripts/ci/**`、
   `scripts/verify_configs.py`、`scripts/verify_rulesets.py`、`scripts/readme_stats.py`、
   `scripts/lib/policy.py`、`scripts/lib/validators.py`、`scripts/batch_update.py`、
   `scripts/commit_writer.py`、`scripts/tests/**`）时，`trusted-baseline-guard` 会：
   - 从 **base 提交** 取出 `scripts/ci`、`scripts/lib`，用 base 逻辑校验 **候选树**；
   - 执行削弱硬失败断言：必需检查被删除、`pr-verify` job 被删除或改名、
     新增 `pull_request_target`、新增 `permissions: write-all`、新增直推 `main`、
     旧 `daily-sync.yml` 被重新武装、测试文件被删除、测试用例数缩小、guard 调用被移除。
2. 任何一条命中即失败，PR 无法合并。
3. **一次性限制**：本 PR 首次引入该 guard，因此 main 上还没有可信版本可用，
   `trusted-baseline-guard` 会在 main 缺该脚本时临时改用 PR head 版本运行并在日志中声明；
   该 PR 合并后，main 版本即接管，后续 PR 一律由 main 版本判定。

## 4. 触发策略（GITHUB_TOKEN 创建的 PR）

使用 `GITHUB_TOKEN` 创建/更新的 PR **不会自动触发** `pull_request` workflow。
因此候选流程在创建 PR 后显式 `workflow_dispatch` 触发 `pr-verify.yml`：

- `workflow_dispatch` 的 **workflow 定义取自 `main`**（可信），避免候选分支自带弱化版检查；
- 被检内容按 `pr_number` 经 REST 解析为 PR 的 base/head SHA；
- 三个必需检查都在 **PR head SHA** 上产生 check run。

**尚未验证**：`workflow_dispatch` 产生的 check run 是否被 Ruleset 视为满足
「require status checks to pass」的必需检查（需要真实 Ruleset 才能验证）。
在该绑定被真实回读验证之前，**不得**启用 `CANDIDATE_AUTOMERGE_ENABLED`。

## 5. 自动合并契约

`request-auto-merge` 在启用前必须满足：

1. `vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'`（默认未设置 → 关闭）；
2. `scripts/ci/verify_required_checks.py` 通过：
   - PR 仍为 `open`、未合并、base 为 `main`；
   - PR head SHA 与候选流程记录的 head SHA 一致（无漂移）；
   - 每个必需检查在**该 head SHA** 上有完成的 check run，且结论为成功；
3. 任一条件不满足 → 不合并，保留 PR 与诊断证据（fail-closed）。

自动合并本身通过 GraphQL `enablePullRequestAutoMerge` 请求（REST API 无对应端点，
已核验 `github/rest-api-description` 的 OpenAPI 描述：不存在 `auto_merge` 路径）。
是否真正合并仍由 GitHub 按 Ruleset 决定。

## 6. Ruleset 计划（尚未创建）

> 当前实际状态：`main` **无分支保护**（`GET /branches/main/protection` → 404）、
> **Rulesets 为空**、仓库 **auto-merge 关闭**（`allow_auto_merge=false`）。
> 本轮**不创建** Ruleset、不开启 auto-merge —— 按上线顺序，二者在实现 PR 通过审计与 CI 之后进行。

计划（`POST /repos/Hawaiine/mihomo-rules/rulesets`，target `refs/heads/main`，enforcement `active`）：

- `pull_request` 规则：需要 PR 才能合并（`required_approving_review_count: 0`，不强制人工审批）；
- `required_status_checks`：`general-pr-verify`、`workflow-policy-guard`、`trusted-baseline-guard`
  （`strict_required_status_checks_policy: true`，避免检查落后于 head）；
- `non_fast_forward`（禁止 force push）、`deletion`（禁止删除分支）；
- `bypass_actors: []`（空 —— 不授予任何日常自动化身份、普通 App 或管理员常规绕过）；
- **不采用 merge queue**，因此不要求 `merge_group` 检查（若未来启用 merge queue，必须同时处理该事件）；
- 创建后必须 GET 回读：`enforcement` 必须为 `active`（不是 `evaluate`/`disabled`），
  bypass 列表为空，必需检查名与实际 check run 名一致。

## 7. 尚未验证 / 已知边界（如实记录）

| 项 | 状态 |
|---|---|
| Ruleset 创建与实际生效 | **未执行**（本 PR 只实现流程） |
| 仓库 auto-merge 开关 | **未开启**（当前 `allow_auto_merge=false`） |
| `workflow_dispatch` check 是否满足 Ruleset 必需检查 | **未验证**（需真实 Ruleset） |
| Auto-merge 端到端（真实合并） | **未验证**（需 Ruleset + auto-merge 开关 + 端到端演练） |
| 定时候选更新 | **关闭**（`CANDIDATE_SYNC_ENABLED` 未设置） |
| `daily-sync.yml` | **保持禁用**（`disabled_manually`，未重新启用） |
| 仓库所有者/管理员修改自身设置的能力 | **无法由规则集约束**：GitHub 允许具有 admin 权限的身份修改 Ruleset、分支保护与仓库设置。规则集约束的是"合并路径"，不是"所有者权限"本身；该边界必须如实记录，不能声称规则集绝对约束所有者。 |
| 其他写身份 | GitHub App / 其他凭据无法枚举（`/installation` 需 JWT） |

## 8. 故障处理

| 情况 | 处理 |
|---|---|
| 检查失败 / 未启动 / 状态缺失 | 不合并，保留 PR |
| head SHA 漂移 | `verify_required_checks.py` 失败 → 不合并 |
| 必需检查名不匹配 / Ruleset 状态未知 | 不合并 |
| 重复定时运行 / PR 已存在 / 分支已同步 | 幂等：复用分支与 PR，无变更则不推送 |
| 写入响应未知 | 先只读对账（GET），不盲目重复写请求 |
| 任何异常 | **不得**回退为直推 `main`，不得绕过检查 |

## 9. 本地/CI 自检命令

```bash
python3 scripts/ci/verify_workflow_policy.py                 # workflow 策略
python3 scripts/ci/verify_general_pr.py                      # 通用 guard（需 PR_BASE_SHA/PR_HEAD_SHA）
python3 scripts/verify_configs.py
python3 scripts/verify_rulesets.py
python3 scripts/readme_stats.py --check
python3 -m unittest discover -s scripts/tests
python3 scripts/ci/verify_policy_baseline.py --base-sha <base> --head-sha <head> --repo-root .
python3 scripts/ci/verify_required_checks.py --repo <owner/repo> --pr <n> --head-sha <sha>
```
