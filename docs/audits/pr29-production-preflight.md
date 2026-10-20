# PR #29 生产前置检查（preflight）：定义、实测与阻塞

> 脚本：`scripts/ci/verify_production_prerequisites.py`（新增，`690a1e5`）
> 实测时间：2026-10-19（Asia/Shanghai）｜实测 head：`690a1e5`
> 结论：**BLOCKED**（3 项未就绪），`writes_performed=false`
> 标签：`[源码直接证明]` `[本轮实测]` `[无法验证]` `[待用户批准]`

---

## 1. 它回答什么问题

不是"代码写完了吗"，而是：**现在能不能开始真实的 Route A / D-15 生产运行？**
因此每一项都要求平台侧**可证明**，而不是仓库里的声明。

硬约束 `[源码直接证明]`：

* 只发 GET（唯一一处 `urllib.request.Request`，无 POST/PUT/DELETE/PATCH）；
* JSON 里固定 `writes_performed: false`，不依赖调用方自觉；
* 任何读不到/结构异常 → 不得 READY（`UNVERIFIABLE`，exit 3）。

## 2. CLI 与退出码

```bash
GITHUB_TOKEN=*** python3 scripts/ci/verify_production_prerequisites.py \
  --repo Hawaiine/mihomo-rules --target-branch main \
  --json /tmp/production-preflight.json
```

| exit | decision | 含义 |
|---|---|---|
| 0 | `READY` | 七项全部可证明通过 |
| 1 | `BLOCKED` | 有明确不满足项（可读、可定位） |
| 2 | — | 输入/配置错误（repo 格式、缺清单、缺 token） |
| 3 | `UNVERIFIABLE` | 外部 API 不可读/超时/非 JSON，无法确定 |

`decision` 与 exit code 由同一分支计算，不会互相矛盾 `[源码直接证明]`。

## 3. 七项检查与判定口径

| 检查 | 通过条件 | 不通过原因码（示例） |
|---|---|---|
| `ruleset_active` | 至少 1 个 `target=branch` + `enforcement=active` 且 conditions 覆盖 `refs/heads/main` 的 ruleset | `NO_RULESET_FOUND`、`RULESET_NOT_ACTIVE`、`RULESET_NOT_COVERING_TARGET`、`RULESET_CONDITIONS_MISSING` |
| `route_a_required_status` | 覆盖 main 的 ruleset 里 `route-a/verify` **精确存在**且 `integration_id==15368` | `REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify`、`REQUIRED_STATUS_SOURCE_UNVERIFIED:route-a/verify` |
| `human_pr_required_checks` | `.github/required-checks.json` 与 `pr-verify.yml` 的 job 集合/名称/来源一致；workflow 由 `pull_request` 触发；无 `continue-on-error`；manifest 含 `status_contexts=[route-a/verify]` | `REQUIRED_CHECKS_CONTRACT_MISMATCH`、`REQUIRED_CHECK_NAME_DRIFT:*`、`PR_VERIFY_WORKFLOW_NOT_PULL_REQUEST_TRIGGERED`、`MANIFEST_MISSING_ROUTE_A_STATUS_CONTEXT` |
| `actions_permissions` | `default_workflow_permissions == "read"` | `ACTIONS_DEFAULT_PERMISSIONS_NOT_READ`、`ACTIONS_PERMISSION_UNREADABLE_HTTP_403` |
| `production_switches` | 三个开关（`CANDIDATE_SYNC_ENABLED`/`GATE_STATE_ENABLED`/`CANDIDATE_AUTOMERGE_ENABLED`）缺失或显式为 `false`/`0`/空 | `PRODUCTION_SWITCH_ENABLED:*`、`PRODUCTION_SWITCH_VALUE_INVALID:*` |
| `gate_state` | `refs/heads/state/gate` 存在、tag 载荷是合法 JSON、schema 合法、digest 自洽、phase 非 paused/recovery-required | `GATE_STATE_NOT_INITIALIZED`、`GATE_STATE_SCHEMA_INVALID`、`GATE_STATE_BLOCKING_PHASE:paused` |
| `snapshot_recovery` | 至少 1 个快照 tag 可解析、schema 合法，且含当前 generation | `GATE_SNAPSHOT_ABSENT`、`SNAPSHOT_SCHEMA_INVALID:*`、`SNAPSHOT_MISSING_FOR_CURRENT_GENERATION` |

变量语义特别注意：`"yes"`/`"TRUE"`/`"1"`/`"on"`/`"maybe"` 等**未知字符串一律判失败**，
不会被"看起来像 false"解释成关闭 `[源码直接证明]`（测试 `ActionsAndSwitchesTest`）。

## 4. 本轮真实执行结果

```
✅ ruleset_active: ok
❌ route_a_required_status: REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify
✅ human_pr_required_checks: ok
✅ actions_permissions: ok
✅ production_switches: ok
❌ gate_state: GATE_STATE_NOT_INITIALIZED
❌ snapshot_recovery: GATE_SNAPSHOT_ABSENT

决策：BLOCKED（writes_performed=False）
```

exit code = **1**（`[本轮实测]`，与 `decision=BLOCKED` 一致）。

证据文件（本机）：
* `/opt/data/work/mihomo-roadmap/evidence/phase13-p1.2-preflight/production-preflight-690a1e5.json`
* `/opt/data/work/mihomo-roadmap/evidence/phase13-p1.2-preflight/preflight-terminal.txt`

关键证据字段（`[本轮实测]`）：

```json
"route_a_required_status": {
  "ok": false,
  "reason_codes": ["REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify"],
  "evidence": {
    "required_context": "route-a/verify",
    "expected_integration_id": 15368,
    "found_contexts": ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"],
    "near_miss_contexts": [],
    "covering_rulesets": [25330782]
  }
}
```

即：`protect-main`(25330782) **确实覆盖 main 且是 active**，但它的必需 context 里
没有 `route-a/verify` —— 这正是 R8 的平台侧成因，而不是"判定器读错了"。

## 5. 负向测试（28 例，全部通过）

| 组 | 覆盖的失败模式 |
|---|---|
| `RulesetNegativeTest` | 只有其他分支、inactive、缺 `route-a/verify`、来源不是 15368、详情 403、列表 500 |
| `HumanChecksTest` | 少一项必需检查、check 名漂移、app 来源错、manifest 缺 status context、job `continue-on-error` |
| `ActionsAndSwitchesTest` | Actions 设置 403、默认权限 write、开关 true、未知字符串、显式 false/空/0、变量不可读 |
| `GateStateTest` | ref 不存在、paused、recovery-required、载荷非 JSON、digest 不一致、快照缺失、快照 generation 不匹配 |
| `TransportTest` | 传输异常 → `UNVERIFIABLE` + JSON 仍写出、`--repo` 缺失 → exit 2、三种 decision 的 exit code 一致 |
| `ReadyTest` | 全部满足 → `READY` 且 `writes_performed=false`、schema 字段完整 |

运行：`python3 -m unittest scripts.tests.test_production_preflight` → **28 tests OK** `[本轮实测]`

## 6. 它**不**证明什么（防止过度解读）

1. 不证明候选 workflow 能跑通：预检是离线只读判定，`CANDIDATE_SYNC_ENABLED` 仍关闭 `[无法验证]`。
2. 不证明真实 D-15 并发/CAS/恢复可用：`state/gate` 未创建，快照不存在 `[无法验证]`。
3. 不证明 `route-a/verify` 的**真实发布**能被 Ruleset 接受：需要平台写入后再实测 `[无法验证]`。
4. 不证明 GitHub Actions 会自动批准/合并：`can_approve_pull_request_reviews=true` 只是平台事实。

## 7. 就绪所需的最小平台操作（**本轮未执行，需授权**）

| 序 | 操作 | 前置 | 验证方式 | 回滚/停手 |
|---|---|---|---|---|
| 1 | 合并 PR #29（人工） | G1/G2 通过 | 合并后 main CI 三 job success | 出现红即停，不重试 |
| 2 | 确认 `route-a-status.yml` 在 main 生效 | 步骤 1 | `GET /contents/.github/workflows/route-a-status.yml?ref=main` | 404 则停 |
| 3 | Ruleset 增加 `route-a/verify`（绑定 integration 15368） | 步骤 2 | 重跑 preflight：`route_a_required_status.ok=true` | 未通过则回退 ruleset 编辑 |
| 4 | 初始化 `state/gate`（M3，单独授权） | 步骤 3 + runbook | preflight `gate_state.ok=true` + 快照 `snapshot_recovery.ok=true` | 出现 CAS 冲突即停，不重试 |
| 5 | 开启 `CANDIDATE_SYNC_ENABLED` | 步骤 4 | 首个候选 PR 端到端（含 `route-a/verify` 发布） | 异常即关开关 |
| 6 | 最后才评估 `CANDIDATE_AUTOMERGE_ENABLED` | 步骤 5 稳定若干轮 | 合并后回读 PR/merge commit | 单独开关，不与 5 联动 |

每一步都会改变平台状态，均需**单独授权** `[待用户批准]`。
