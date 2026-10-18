# PR-first 全自动更新系统（运行契约 · Route A）

本文件描述 `Hawaiine/mihomo-rules` 的 PR-first 自动化：**自动更新只能创建候选分支与 PR，
`main` 由 Ruleset 与真实必需状态把关，日常运行不需要 ChatGPT 或人工审批**。

> 认证路径（用户已确认的 Route A）：**只用内置 `GITHUB_TOKEN`，不使用 GitHub App / PAT**。

## 1. 运行路径

```
[定时 22:00 UTC / 手动] daily-sync.yml   ← 唯一入口（自身零写入，只做 workflow_call）
  └─ candidate-pr-sync.yml
       gate      : vars.CANDIDATE_SYNC_ENABLED == 'true'（默认关闭）
       lock      : **硬前置** —— require-enabled（GATE_STATE_ENABLED 非 true 即失败）→ acquire（CAS）
       generate  : 只读 —— 依赖 lock 成功；拉上游、生成规则、跑全部 guard 与单测，产出 candidate.patch
       publish   : GITHUB_TOKEN（contents: write / pull-requests: write / statuses: write）
                   → 推送候选分支 candidate/rules-update（硬断言 ≠ main）
                   → 创建/更新 PR（幂等；无真实 diff 不产生空提交）
                   → 回读 PR head SHA 与候选分支 ref SHA 做一致性校验
       verify    : 受信脚本（来自 main）+ 候选树（只作数据）
                   → verify_candidate.py（范围/防自我削弱/规则集/配置）
                   → publish_status.py 在**精确 head SHA** 上发布 `route-a/verify`
       request-auto-merge : vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'（默认关闭）

[pull_request] pr-verify → 三个 check-run（仅人工 PR 会触发）
  general-pr-verify / workflow-policy-guard / trusted-baseline-guard
GitHub 按 Ruleset 合并（必需状态 + 禁止 force push / 删除分支，bypass 为空）
```

## 2. 为什么是 commit status 而不是 check-run

GitHub 的两条硬事实：

1. 用 `GITHUB_TOKEN` 创建/更新的 PR **不会触发** `pull_request` workflow（防递归）。
2. 因此这条 PR 上不会出现 `pr-verify.yml` 的三个 check-run —— 不是"能力不足"，
   而是**事件没有被触发**。Route A 的选择是不引入 App/PAT 去换事件，
   只把可信验证放进候选运行本身。

因此 Route A 的候选 PR 拿不到 `pr-verify` 的三个 check-run。可行且不需 App 的路径是
**commit status**：`POST /statuses/{head_sha}` 只需 `statuses: write`，而 Ruleset 的
"Require status checks to pass" 同时接受 check-run 与 commit status。

于是：

* `route-a/verify`（由 `verify` job 发布）是**候选 PR 的唯一必需门槛**；
* 三个 check-run 继续服务人工 PR（保证人工改动能被完整校验）；
* 两者都在 `.github/required-checks.json` 登记（`checks[]` + `status_contexts[]`）。

**平台前提（未配置，属阻塞项）**：仓库必须开启 *Settings → Actions → General →
"Allow GitHub Actions to create and approve pull requests"*，否则 `publish` 建 PR 返回 403。
此时流程 fail-closed 并报 BLOCKED，**绝不回退到 App/PAT**。

## 2.1 门禁数据源：两种模式，同等强度

| 模式 | 触发条件 | 数据源 | 适用 |
|---|---|---|---|
| check-run | head SHA 上存在 `checks[]` 声明的 check-run | `GET /commits/{sha}/check-runs`（app 15368） | 人工 PR（`pull_request` 触发 pr-verify） |
| commit status | 该 SHA 上**没有任何** check-run | `GET /commits/{sha}/statuses`，要求 `creator.login == github-actions[bot]` 且 state=success | 候选 PR（GITHUB_TOKEN 创建，无 pull_request 事件、无法产生 check-run） |

`await_checks.py` / `verify_required_checks.py` 按上述规则自动选择模式（`gate_mode` 字段），
两者都要求：精确 head SHA、全部必需项 success、PR 仍 open、base 未漂移。
模式选择**只允许** check-run → status 的单向回退，且仅在「一个 check-run 都没有」时生效；
出现同名但来源不符的 check-run 仍按原严格口径失败。

## 3. 严格口径（fail-closed）

* **状态**：只有 `success` 视为满足；`pending` / `failure` / `error` 一律禁止合并。
* **head SHA 绑定**：发布前校验 PR 仍 open、base 分支与 base SHA 未变、**PR head == 目标 SHA**；
  发布后回读该 SHA 上确有本 context 且 state 一致。旧 SHA 的结论绝不套用到新提交。
* **身份严格性**：候选 PR 必须由 `type=Bot` 的 `github-actions[bot]` 创建才走 status 模式；
  人工 PR 必须是 `type=User`；commit status 的 `creator.type` 必须是 `Bot`。
  只比对 login 的一律拒绝（防同名冒充）。
* **防自我削弱**：`verify_candidate.py` 用受信脚本检查候选 diff，**禁止**修改
  `.github/**`、`scripts/**`、`tests/**`、`required-checks.json`；超出 `ruleset/`、`configs/`、
  `docs/`、`README.md`、`CHANGELOG.md` 的改动同样失败（机器人 PR 只能改规则数据）。
* **候选 head SHA 取自真实远端**：候选树未变化而跳过推送时，head 取**远端 ref SHA**；
  创建/更新 PR 后再对「PR 真实 head / 候选分支 ref / 预期 SHA」做一致性校验。
* **有界等待**：`request-auto-merge` 仅只读轮询（递增间隔、总超时 900s）；超时、漂移、
  403/429/5xx、响应不明一律禁止合并，并保留 PR 与诊断证据。
* **三态区分**：`graphql_request_ok` / `auto_merge_enabled` / `merged` 分开报告。

## 4. 门禁与防自证循环

| 门禁 | 作用 |
|---|---|
| `verify_general_pr.py` | 树自洽、pin、Oracle 独立、图标基线、workflow 安全（纯委派 workflow 免 checkout 契约） |
| `verify_workflow_policy.py` | **结构化**判定：门控必须是真实 `if`；必需 job 集合/命名一致；auto-merge 先验证后 mutation；publish 权限恰为 `{contents: write, pull-requests: write, statuses: write}`；**禁止 App/PAT 认证路径**；禁止直推 main；`daily-sync.yml` 必须是唯一入口且自身零写入；全仓只允许一个 `schedule` |
| `verify_policy_baseline.py` | 用 **PR base 提交** 的 guard 逻辑校验候选树；删除必需检查/削弱来源绑定/删除或改名 job/新增 `pull_request_target`/`write-all`/直推 main/删除测试 均硬失败 |
| `verify_candidate.py` | Route A 运行期受信验证（防自我削弱 + 规则集/配置自洽），结果发布为 `route-a/verify` |

`daily-sync.yml` 的历史直推实现（直接推送默认分支）**已删除**；现在它只有
`workflow_call` 委派，守卫逐行扫描该文件，出现任何写操作即失败。

## 5. 上线顺序（本轮**未**执行）

1. 本 PR 通过独立审阅。
2. 创建 `main` Ruleset：`pull_request` + 三个 check-run（strict）+ **`route-a/verify` 状态**
   + `non_fast_forward` + `deletion`，bypass 列表为空；GET 回读 `enforcement=active`。
3. 打开 "Allow GitHub Actions to create and approve pull requests"（P0-14，**需用户批准**）。
4. 开启仓库 auto-merge（`allow_auto_merge`），在隔离分支上跑一次真实候选流程，
   确认 `route-a/verify` 落在候选 PR 的精确 head SHA 上。
5. 全部通过后设置 `CANDIDATE_SYNC_ENABLED=true`；`CANDIDATE_AUTOMERGE_ENABLED` 单独评估。

## 6. 尚未验证 / 治理边界

* Ruleset 未创建、auto-merge 未开启、"Actions 创建 PR" 开关未开启、定时更新关闭。
* `verify_candidate` / `publish_status` / 状态锁的协议语义由**进程内 stub + 单测**覆盖，
  **不代表真实平台端到端成功**。
* **Ruleset 不能约束所有者权限本身**：admin 身份仍可修改 Ruleset、分支保护与仓库设置。
* 其他写身份无法枚举；新 workflow 仍可自行声明 `contents: write`，约束来自必需状态、
  可信基线 guard 与人工审阅。
