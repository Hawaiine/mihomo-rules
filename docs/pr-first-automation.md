# PR-first 全自动更新系统（运行契约）

本文件描述 `Hawaiine/mihomo-rules` 的 PR-first 自动化：**自动更新只能创建候选分支与 PR，
`main` 由 Ruleset 与真实必需检查把关，日常运行不需要 ChatGPT 或人工审批**。

## 1. 运行路径

```
[定时/手动] candidate-pr-sync
  gate      : vars.CANDIDATE_SYNC_ENABLED == 'true'（默认关闭）
  generate  : 只读 —— 拉上游、生成规则、跑全部 guard 与单测，产出 candidate.patch
  publish   : 用专用最小权限 GitHub App installation token
              → 推送候选分支 candidate/rules-update（硬断言 ≠ main）
              → 创建/更新 PR（App 身份 ⇒ 正常触发 pull_request 验证）
              → 回读 PR head SHA 与候选分支 ref SHA 并做一致性校验
  request-auto-merge : vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'（默认关闭）
              → await_checks.py    有界轮询（只读）
              → verify_required_checks.py  严格 success + 来源绑定 + head SHA 绑定
              → request_automerge.py       请求 auto-merge 并区分三种状态

[pull_request] pr-verify  →  三个必需检查（由 pull_request 事件产生）
  general-pr-verify / workflow-policy-guard / trusted-baseline-guard
GitHub 按 Ruleset 合并（必需检查 + 禁止 force push / 删除分支，bypass 为空）
```

## 2. 为什么必须用 App token 触发验证

GitHub 官方文档明确保证：**workflow_dispatch 触发的 workflow 产生的检查不会出现在 PR 的
checks 中，也不满足 branch ruleset 的必需状态检查**，即使该检查运行在 PR head SHA 上。
同时，用 `GITHUB_TOKEN` 创建/更新的 PR 不会触发 `pull_request` 工作流（防止递归）。

因此：
* 候选分支与 PR 一律由**专用最小权限 GitHub App installation token** 创建/更新；
* `pr-verify.yml` **不声明** `workflow_dispatch`，必需检查只由 `pull_request` 事件产生；
* 不新增 `pull_request_target`（不允许在持写权限的上下文执行不可信 PR 代码）。

App 最小权限：`contents: write`（推候选分支）+ `pull-requests: write`（建/更新 PR），
仅限本仓库。动作固定到完整 commit SHA：`actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349`（v2）。

**待配置的秘密（本轮未创建、未配置）**：`PR_FIRST_APP_ID`、`PR_FIRST_APP_PRIVATE_KEY`。
缺失时 `publish` 只记录「保持关闭」并正常结束：**不写入、不失败、不触发任何远端变更**。

## 3. 严格口径（fail-closed）

* **必需检查**：只有 `success` 视为满足。`skipped` / `neutral` / `cancelled` / `timed_out` /
  `failure` / `action_required` / `stale` / queued / in-progress / 未找到 **一律禁止合并**
  （GitHub 把 `success`、`skipped`、`neutral` 都算作成功状态，因此不能把「检查存在」
  等同于「检查真正成功」）。
* **来源绑定**：每个必需检查必须来自预期 integration
  （`app_slug: github-actions`、`app_id: 15368`，经 `GET /apps/github-actions` 独立核实）。
  同名但来源不符的 check run 被拒绝采信。
* **head SHA 绑定**：检查必须发生在 PR **当前** head SHA 上；PR 仍 open、目标仍是 `main`、
  head 未漂移。
* **候选 head SHA 取自真实远端**：候选树未变化而跳过推送时，head 取**远端 ref SHA**，
  而不是本地新建的 commit SHA；创建/更新 PR 后再对
  「PR 真实 head / 候选分支 ref / 预期 SHA」做一致性校验，任一不一致即 fail-closed。
* **有界等待**：`request-auto-merge` 先只读轮询（递增间隔、总超时 900s），明确失败立即终止；
  超时、漂移、403/429/5xx、响应不明一律禁止合并；超时后保留 PR 与诊断证据，不制造 PASS。
* **三态区分**：`graphql_request_ok`（mutation 成功，且响应含
  `data.enablePullRequestAutoMerge`）/ `auto_merge_enabled`（回读 `auto_merge` 非空）/
  `merged`（真实合并）。只有真实合并后才报告 merge commit SHA 与 `main` HEAD。

## 4. 门禁与防自证循环

| 门禁 | 作用 |
|---|---|
| `verify_general_pr.py` | 树自洽、pin、Oracle 独立、图标基线、**workflow 安全**（pr-verify 权限恰为 `contents: read`、无浮动 ref、无写命令） |
| `verify_workflow_policy.py` | **结构化**判定：门控必须是真实 `if` 表达式（注释不算）、必需检查 job 集合/命名一致、必需 job 不得有可跳过的 `if`、auto-merge 必须先验证后 mutation、publish 不得执行候选内容、禁止直推 main 路径、图标 pin、`persist-credentials: false`、旧 `daily-sync.yml` 逐字节 pin |
| `verify_policy_baseline.py` | 用 **PR base 提交** 的 guard 逻辑校验候选树；对删除必需检查/削弱来源绑定/删除或改名 job/新增可跳过 `if`/新增 `pull_request_target`/`write-all`/直推 main/测试删除或数量缩小 硬失败 |

旧直推 workflow `.github/workflows/daily-sync.yml`：**平台侧保持 `disabled_manually`**，
内容按 sha256 固定（`da5c128f…`）；任何改动都会同时触发 pin 失败与内容规则，不可能被悄悄重新武装。

## 5. 上线顺序（本轮**未**执行）

1. 本 PR 通过独立审阅。
2. 创建 `main` Ruleset：`pull_request` + 三个必需检查（strict）+ `non_fast_forward` + `deletion`，
   bypass 列表为空；每个必需检查绑定 `github-actions` integration；GET 回读 `enforcement=active`。
3. 配置 App（仅本仓库安装）与上述两个秘密，跑一次真实候选流程，确认 PR 由 App 身份创建、
   `pull_request` 验证被触发、三个检查在新 head SHA 上真实 success。
4. 开启仓库 auto-merge（`allow_auto_merge`），执行含失败门禁的端到端演练。
5. 全部通过后，才设置 `CANDIDATE_SYNC_ENABLED=true`；`CANDIDATE_AUTOMERGE_ENABLED` 单独评估。

## 6. 尚未验证 / 治理边界

* Ruleset 未创建、auto-merge 未开启、App 未创建、秘密未配置、定时更新关闭。
* 轮询/SHA 一致性/来源绑定等逻辑由 **mock 测试**覆盖，**不代表真实平台端到端成功**。
* **Ruleset 不能约束所有者权限本身**：具备 admin 权限的身份仍可修改 Ruleset、分支保护与仓库设置；
  规则集约束的是**合并路径**。
* 其他写身份（其他 App / 凭据）无法枚举；任何新 workflow 仍可自行声明 `contents: write`，
  约束来自必需检查、可信基线 guard 与人工审阅。
* 一次性限制：本 PR 首次引入 `verify_policy_baseline.py`，base 上尚无该脚本，
  `trusted-baseline-guard` 临时使用 PR head 版本并在日志中声明；合并后由 base/main 版本接管。
