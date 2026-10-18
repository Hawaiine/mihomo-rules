# P1 九项历史风险复核（本轮实测）

- 复核时间：2026-10-18 13:05 (Asia/Shanghai)
- 复核对象：`feat/pr-first-automation` @ 工作树（提交前）+ 本轮修复
- 口径：**每项都读当前源码**，历史报告不作为结论依据

| # | 历史风险 | 本轮结论 | 证据 | 处理 |
|---|---|---|---|---|
| R1 | `candidate-pr-sync.yml` 有多个 `APP_TOKEN`，与 Route A 冲突 | **已不存在** | `grep -rn "APP_TOKEN\|PR_FIRST_APP\|create-github-app-token" .github scripts`（排除测试与守卫关键字）→ 0 处实际引用；只有注释提到 App/PAT | 守卫 `verify_workflow_policy.py` 对这三个 token 出现即失败（含负向测试） |
| R2 | `lock` 与 `generate` 各自依赖 `gate`，generate 可能在锁前执行 | **本轮实测确认存在** | 修复前：`generate.needs = gate`；`publish` 虽要求 `lock.result==success`，但生成阶段已无锁运行 | **已修**：`generate.needs=[gate,lock]` 且 `if` 要求 `lock.result=='success'` |
| R3 | `gate_status.py` 允许缺失 SHA 绕过 SHA 不一致检查 | **本轮实测确认存在** | 修复前 `if item.get("sha") and item["sha"] != expected_head:` → `sha` 缺失即跳过整个比较 | **已修**：`sha` 必须是 40 位且等于 expected，否则 `failed`；新增测试 `test_missing_sha_rejected` |
| R4 | 机器人身份只比较 login，未核实账号类型 | **本轮实测确认存在** | 修复前 `expected_mode` 仅 `login == "github-actions[bot]"`；status 来源同样只看 login | **已修**：PR 作者要求 `type=="Bot"`（人类要求 `type=="User"`，其它直接失败）；status creator 要求 `type=="Bot"`；新增 `test_expected_mode_rejects_login_only_spoof`、`test_creator_user_type_rejected` |
| R5 | `publish_run_status.py` 的 run 识别条件过宽 | **本轮实测确认存在** | 修复前只校验 repo/event/name/head_sha/status/conclusion + jobs | **已修**：新增 `path == .github/workflows/pr-verify.yml`（防同名冒充）、`head_repository` 必须是本仓库（防 fork）、`pull_requests` 若存在必须包含目标 PR；新增 3 例拒绝测试 |
| R6 | `gate_lock._snapshot` 把快照错误当"已存在"，且写状态后快照失败不阻断 | **本轮实测确认存在** | 修复前 `except gs.StateError: return "existing:..."`；`acquire` 先 `_write` 再 `_snapshot`，返回值被忽略 | **已修**：新增 `SnapshotExists` 子类，只有它被视为已存在；改为**先写快照再 CAS 写状态**（`_commit`），快照失败则状态不变；新增 `test_snapshot_failure_blocks_state_change` |
| R7 | 文档声称只用 GITHUB_TOKEN，可能与源码不一致 | **一致** | `docs/pr-first-automation.md` 与 `candidate-pr-sync.yml` / `route-a-status.yml` 均只引用 `secrets.GITHUB_TOKEN`；守卫强制 | 无需改动（文档已同步 Route A） |
| R8 | required checks / GITHUB_TOKEN 事件限制 / Ruleset 兼容性风险 | **风险仍在，已定量** | 平台 Ruleset `protect-main` 要求 3 个 check-run；候选 PR 由 bot 创建 ⇒ 无 check-run ⇒ **永远不满足** | 这是"自动化未启用 + 生产关闭"下的**惰性阻塞**：不会误合并，但一旦开启 sync 就会卡住。解决顺序：合并本 PR → 让 `route-a-status.yml` 在 main 生效 → 把 `route-a/verify` 加进 Ruleset。**本轮不改平台**（用户范围：只读复核 + 生产关闭） |
| R9 | D-15 过期租约接管、CAS、暂停恢复、快照、故障恢复 | **部分存在** | 修复前：租约过期即被静默接管（无 `--takeover`、无 epoch 推进、无原持有者记录）；其余（CAS、暂停不可清除、恢复拒绝矩阵）已正确 | **已修**：过期租约默认 `EXIT_NEEDS_HUMAN`，`--takeover` 才接管并 `epoch+1` + 记录 `previous_owner`；新增 2 例测试 |

## 未验证项（[无法验证]）

- 真实 `state/gate` ref 上的并发 CAS（两个 runner 同时写）—— 需 M3 授权创建 ref；
- `workflow_run` 真实触发（文件须先在 main）；
- 平台是否接受 `github-actions[bot]` 作为 required status 的发布者（需把 `route-a/verify` 加入 Ruleset 后才能验证）。

## 本轮新增/修改测试

| 文件 | 新增用例 |
|---|---|
| `test_gate_status.py` | 缺失 SHA、creator 类型非 Bot、login-only 冒充、作者类型未知 |
| `test_gate_lock.py` | 过期租约需显式 `--takeover`、快照失败阻断状态变更、`require-enabled` 6 组取值 |
| `test_publish_run_status.py` | workflow path 冒充、fork head_repository、run 关联其它 PR |

全量：`python3 -m unittest discover -s scripts/tests` → **1107 tests OK (skipped=1)**。
