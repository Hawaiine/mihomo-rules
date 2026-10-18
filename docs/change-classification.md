# 变更分类 / 人工审批 / 合并决策（可信代码契约）

三个模块都只做**判定**，不做写入：

| 模块 | 输入 | 输出 | 关键约束 |
|---|---|---|---|
| `scripts/ci/classify_change.py` | changed files + base/head SHA + tree 路径集 | `GOVERNANCE_CHANGE` / `NEW_RULESET` / `EXISTING_DATA_ONLY` / `MIXED_OR_UNKNOWN` | 证据不完整永不降级为纯数据 |
| `scripts/ci/verify_approval.py` | GitHub reviews（完整分页） | `(ok, problems, evidence)` | 必须 APPROVED + `commit_id == head` + `type=User` + 取最新一条 |
| `scripts/ci/decide_merge.py` | 分类 + 检查 + 审批 + 锁 + PR/平台状态 | `decision` + 稳定 reason code | 只出决策，**从不**调用合并 |

## 分类优先级

1. `GOVERNANCE_CHANGE`：`.github/**` 或 `scripts/**` 或治理文档/生成器；同时保留 `ALSO_NEW_RULESET` 证据标签。
2. `MIXED_OR_UNKNOWN`：diff 截断、SHA 非法/相同、空 diff、删除/重命名/类型变化、未知路径、路径遍历、大小写冲突、重复路径、缺少 tree 证据。
3. `NEW_RULESET`：由 base/head tree 证明新增（或修改了 base 中不存在的对象）。
4. `EXISTING_DATA_ONLY`：仅 `ruleset/<brand>/<brand>.yaml`（base 中已存在）与四份 config / README / CHANGELOG。

## 决策矩阵（`decide_merge`）

| 条件 | 结果 |
|---|---|
| PR 非 open / head 漂移 / base 漂移 / 检查未过 | BLOCKED（`PR_NOT_OPEN` / `HEAD_SHA_MISMATCH` / `BASE_SHA_DRIFT` / `CHECKS_NOT_OK`） |
| 锁 idle / paused / recovery_required / snapshot_failed | BLOCKED（`LOCK_NOT_HELD` / `LOCK_PAUSED` / `RECOVERY_REQUIRED` / `SNAPSHOT_FAILED`） |
| 治理变更 | BLOCKED（`CHANGE_GOVERNANCE`） |
| 混合/未知 | BLOCKED（`CHANGE_UNKNOWN`） |
| 新 ruleset 无当前 head 审批 | BLOCKED（`APPROVAL_*`） |
| 新 ruleset + 审批 | `ELIGIBLE_NEW_RULESET_AFTER_APPROVAL` |
| 纯数据 + 全绿 + 持锁 | `ELIGIBLE_EXISTING_DATA`（`production_allowed` 仍由生产开关决定） |

`production_mode=False` 时 `production_allowed=False`，并附 `PRODUCTION_DISABLED` reason；
**eligible ≠ 已合并**。
