# PR 作者 × 审批人矩阵（Route A 治理约束）

本文件把「谁能审谁的 PR」写成可执行契约。审批不是「存在一条 `APPROVED` review」，
而是 **作者身份 × 审批人身份 × 精确 head** 的函数。

> 认证路径固定：只用内置 `GITHUB_TOKEN`，不引入 GitHub App / PAT。
> 审批人固定：`Hawaiine`（不可变 user.id `152380613`，见 `.github/approval-policy.json`）。
> `allow_bot_approvers=false` 保持不变，任何机器人 review 都不构成审批。

## 1. 平台语义（不是本仓库的选择）

| 事实 | 来源 | 影响 |
|---|---|---|
| PR 作者不能批准自己创建的 PR | GitHub 原生规则 | 受信审批人自己开的 PR **在结构上无法获得有效审批** |
| `GITHUB_TOKEN` 触发的 workflow 事件不会启动新的 workflow run | GitHub Actions 事件递归抑制 | **机器人创建的 PR 不会触发 `pull_request` 工作流** |
| `can_approve_pull_request_reviews=true` | 仓库 Actions 设置（实测） | 平台**允许** `GITHUB_TOKEN` 创建 PR（不等于允许审批） |
| `default_workflow_permissions=read` | 同上 | 需要写权限的 job 必须显式声明 |

第二条是硬约束：机器人 PR 的 `route-a/verify` 只能由**同一个受信运行内的发布器**
（`candidate-pr-sync.yml` 的 verify job → `publish_run_status.py --publish-mode=dual`）
发布，不能指望 `pr-verify.yml` 自然触发。

## 2. 三类作者的行为矩阵

| PR 作者 | 合法审批人 | 闸门判定 | 备注 |
|---|---|---|---|
| **机器人**（`github-actions[bot]`） | `Hawaiine`（152380613） | `BOT_AUTHOR` → 需要 1 条精确绑定当前 head 的 `APPROVED` | 机器人 PR 可被人类审批，这是唯一不需要第二个人类审批人的合规路径 |
| **受信审批人本人**（`Hawaiine`） | 无（不能自审） | `TRUSTED_AUTHOR` → `SELF_APPROVAL_BY_TRUSTED_AUTHOR`，恒 fail-closed | 必须改由机器人身份创建 PR，或新增第二位受信审批人（需显式策略变更） |
| **其他人类** | `Hawaiine` | `OTHER_HUMAN_AUTHOR` → 需要 1 条精确绑定当前 head 的 `APPROVED` | 第三方 PR 正常流程 |

作者身份读不到（字段缺失、`user.id` 非法、PR 对象不可读）→ `UNKNOWN_AUTHOR`，审批不可用。

## 3. 复核项（每次审批/合并前）

1. `GET /repos/{repo}/pulls/{n}`：`state == "open"`、`merged is not True`、`merged_at is None`；
2. `head.sha == 预期 head`（40 位小写十六进制），否则 `PR_HEAD_MOVED`；
3. `base.sha == 预期 base`，否则 `PR_BASE_MOVED`；
4. `user.id` 可读，且不等于任何受信审批人 ID（否则 `SELF_APPROVAL_BY_TRUSTED_AUTHOR`）；
5. 全部 review 分页读完（未读完 ⇒ `REVIEWS_PAGINATION_INCOMPLETE`）；
6. 受信 ID 的**最新** review 为 `APPROVED`、`user.type == "User"`、`commit_id == head`；
7. 任何人在当前 head 上的最新 review 为 `CHANGES_REQUESTED` ⇒ 阻断；
8. `route-a/verify` 必须是对当前 head 的 Check Run（`app.id=15368`），
   旧的 success commit status 不能替代当前 head 的权威 Check Run。

## 4. 可执行入口

```bash
# 组合闸门（快照 + 作者类别 + 审批有效性）
GITHUB_TOKEN=... python3 scripts/ci/verify_pr_approval_gate.py \
  --repo Hawaiine/mihomo-rules --pr <N> \
  --head-sha <40 hex> --base-sha <40 hex> --json /tmp/gate.json

# 纯校验函数（库层，供受信脚本调用）
verify_approval.verify_approvals(reviews, head_sha=..., approver_ids=...,
                                 required=1, complete=True, pr_author_id=<author id>)
```

退出码 `0` 仅表示 `APPROVAL_GATE_PASS`；任何 reason code 都表示不可合并。

## 5. 路径 2（受信审批人自己写代码）的合规流程

GitHub 不允许自批，因此「Hawaiine 写代码 + Hawaiine 审批」在平台层不存在。可选流程按优先级：

1. **交回机器人身份创建 PR**：把分支推到目标仓库的专用分支，由现有自动化
   （`candidate-pr-sync.yml` 的 publish job，`pull-requests: write`）创建 PR；
   PR 作者变成 `github-actions[bot]`，随后由 `Hawaiine` 正式审批。
   *限制*：该 PR 不会触发 `pull_request` 工作流，`route-a/verify` 必须由同一运行内的
   双发发布器产生，`general-pr-verify` / `workflow-policy-guard` / `trusted-baseline-guard`
   不会自动出现。
2. **新增第二位受信审批人**：需要修改 `.github/approval-policy.json` 的 `approver_ids`
   与 `required_approvals`，属治理决策，必须先获授权。
3. **保持 PR 打开等待**：不做任何绕过（不自批、不冒充机器人、不放宽 Ruleset）。

禁止：把审批人改成机器人、用 issue 评论/commit status 代替正式 review、
让机器人审批、以普通 CI 成功替代人工审批。
