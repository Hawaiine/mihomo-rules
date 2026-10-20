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

## 2.1 显式审批策略（新增）

审批人**不再**从仓库 owner 推断。唯一来源是 `.github/approval-policy.json`
（`scripts/ci/approval_policy.py` 解析）：

```json
{
  "schema": "mihomo-rules-approval-policy",
  "version": 1,
  "approvers": ["Hawaiine"],
  "approver_ids": [152380613],
  "required_approvals": 1,
  "allow_bot_approvers": false,
  "head_binding": "exact",
  "on_missing": "fail-closed"
}
```

* `approver_ids` 是唯一授权依据；当前只允许用户本人 `Hawaiine`（GitHub user ID `152380613`）。
  `approvers` 中的 login 只用于审计展示，不单独授予权限；策略校验器会拒绝名单扩展或替换。
* 空 `approvers` 或空 `approver_ids` = **未配置**，不是“无需审批”：新增规则集 PR 一律 BLOCKED
  （`APPROVAL_POLICY_UNCONFIGURED`），并在 preflight 中作为独立检查项暴露；
* 未知字段、schema/version 不符、`allow_bot_approvers=true`、`head_binding!=exact`、
  `on_missing!=fail-closed` 全部拒绝；
* 多人审批：`required_approvals` 个不同授权 user ID 的最新 review 必须是 `APPROVED`，
  `user.type == "User"`，且 `commit_id == 当前 head`；任何人当前 head 上 `CHANGES_REQUESTED` 都阻断；
* `approver_ids` 是唯一授权依据；login 仅用于审计展示，不单独授权。
* `--approver` login 覆盖已禁止；CLI 使用 `--policy` 读取可信策略，不接收用户自行注入的 login、ID 或 inline review 授权。
* CLI 参数、命令成功或 workflow 输出不是人工审批；新增 Ruleset / 治理变更仍须授权用户对当前 head SHA 的真实 `APPROVED` review。
* `EXISTING_DATA_ONLY` 日常纯数据更新保持全自动，不要求每次人工审批。


## 2.2 认证来源与逐 job 最小权限（本轮实测对账）

全仓 workflow 只允许**内置 `GITHUB_TOKEN`**：不存在 GitHub App、不存在 PAT，
`secrets.*` 的引用集合就是 `{GITHUB_TOKEN}`（`candidate-pr-sync.yml` 与
`route-a-status.yml` 均如此）。按 job 收敛如下：

| job | permissions | 用途 | 凭据注入 |
|---|---|---|---|
| gate | 继承顶层 `contents: read` | 只读 `vars.CANDIDATE_SYNC_ENABLED` | 无 |
| lock | `contents: write` | D-15 状态 ref 的 CAS 写入 | `secrets.GITHUB_TOKEN` |
| generate | `contents: read` | 生成候选（不推送） | 无 |
| publish | `contents: write` + `pull-requests: write` + `statuses: write` | 推候选分支、建/更新 PR | `GITHUB_TOKEN`（GIT_ASKPASS 注入，不进 argv/URL/日志） |
| verify | `contents: read` + `pull-requests: read` + `statuses: write` | 受信验证 + 发布 `route-a/verify` | `GITHUB_TOKEN` |
| finalize | `contents: write` | 写终态/释放锁 | `GITHUB_TOKEN` |
| request-auto-merge | `contents: read` + `pull-requests: write` + `checks: read` + `actions: read` | 判定后请求 auto-merge | `GITHUB_TOKEN` |

规则：写权限只出现在确实需要写的 job；`verify` 与 `finalize` 不接触候选代码；
`publish` 在应用候选补丁之后不再执行仓库脚本。任何新增 job 都必须先在此表登记
权限与用途，并由 `verify_workflow_policy.py` 的结构化判定守住。

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
* **U-09 通知确认（B4）**：确认请求只接受 `<run_id>-<attempt>` 形态的 `notice_run_id`（与
  `candidate-pr-sync.yml` 写入 `GateState.run_id` 的生产者一致）；纯 run ID 一律拒绝。身份只来自
  GitHub API 的 `run.actor` + `/users/{id}`（不可变 ID、login、`type=User` 三者交叉校验），
  不接受 CLI/输入/制品中的身份声明；`confirm-notice` CLI 固定 fail-closed。
* **三态区分**：`graphql_request_ok` / `auto_merge_enabled` / `merged` 分开报告。

## 4. 门禁与防自证循环

| 门禁 | 作用 |
|---|---|
| `verify_general_pr.py` | 树自洽、pin、Oracle 独立、图标基线、workflow 安全（纯委派 workflow 免 checkout 契约） |
| `verify_workflow_policy.py` | **结构化**判定：门控必须是真实 `if`；必需 job 集合/命名一致；auto-merge 先验证后 mutation；publish 权限恰为 `{contents: write, pull-requests: write, statuses: write}`；**禁止 App/PAT 认证路径**；禁止直推 main；`daily-sync.yml` 必须是唯一入口且自身零写入；全仓只允许一个 `schedule` |
| `verify_policy_baseline.py` | 用 **PR base 提交** 的 guard 逻辑校验候选树；删除必需检查/削弱来源绑定/删除或改名 job/新增 `pull_request_target`/`write-all`/直推 main/删除测试 均硬失败 |
| `verify_candidate.py` | Route A 运行期受信验证（防自我削弱 + 规则集/配置自洽），结果发布为 `route-a/verify` |
| `verify_production_prerequisites.py` | **生产前置只读检查器**：main Ruleset 是否真的覆盖默认分支、是否含 `route-a/verify` 且来源为 GitHub Actions、必需检查清单与 workflow 是否一致、Actions 默认权限、三个生产开关、`state/gate` 与快照可恢复性。任何一项读不到即 `UNVERIFIABLE`（exit 3），绝不 READY |

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

* **平台契约与设计存在已知差异（2026-10-19 只读实测）**：`protect-main` 目前只有
  `deletion` / `non_fast_forward` / `required_status_checks` 三条规则，**没有 `pull_request`
  规则**（即不强制人工 review），也没有 `route-a/verify` context。前者是"是否要求人工审阅"
  的治理选择（需用户裁定），后者会让候选 bot PR 永久无法满足门禁（R8）。
  两者都必须通过平台写入修正，`verify_production_prerequisites.py` 会把它们判为未就绪。
* Ruleset 未创建、auto-merge 未开启、"Actions 创建 PR" 开关未开启、定时更新关闭。
* `verify_candidate` / `publish_status` / 状态锁的协议语义由**进程内 stub + 单测**覆盖，
  **不代表真实平台端到端成功**。
* **Ruleset 不能约束所有者权限本身**：admin 身份仍可修改 Ruleset、分支保护与仓库设置。
* 其他写身份无法枚举；新 workflow 仍可自行声明 `contents: write`，约束来自必需状态、
  可信基线 guard 与人工审阅。
