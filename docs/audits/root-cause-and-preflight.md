# PR #29 根因台账与预演计划（阶段十三 P1）

- 生成时间：2026-10-18 12:20 (Asia/Shanghai)
- 被测代码 head：`60e8273`（本轮改动前）；本文件随修复提交一起进入分支
- 结论口径：每个 P0 都按「现象 → 根因 → 方案 → 反例预演 → 测试 → 平台依赖 → 回滚」记录

| 编号 | 严重性 | 现象（修复前） | 根因（源码级） | 修复方案 | 关键反例预演 | 测试 | 平台依赖 | 回滚 |
|---|---|---|---|---|---|---|---|---|
| **P0-1** | 高 | 人工 PR 永远不会得到 `route-a/verify`；候选 PR 的状态发布者与人类 PR 路径不一致 | 只有候选 workflow 内的 `verify` job 会发状态；`pr-verify` 由 `pull_request` 触发，产物是 check-run，没有人把它转成 state | 新增 `route-a-status.yml`（`workflow_run` → 默认分支上下文）+ `publish_run_status.py`：核验 run 归属/事件/名称/head SHA/全部 job 成功，再核验 PR open/base/head，发布后回读并再次核验 head | run 事件非 pull_request；run 名称不符；job skipped；缺 job；发布前后 head 漂移；403；重复发布来源不可信 | `test_publish_run_status.py` 13 例 | **workflow_run 文件必须先合并到 main 才生效** | 删除该 workflow 文件即可，无状态残留 |
| **P0-2** | 高 | `GATE_STATE_ENABLED` 未设置时 `lock` job 被 `if` 跳过，`generate` 仍会跑 ⇒ 无锁同步 | 门控写成 `if: vars.GATE_STATE_ENABLED == 'true'`，"禁用"等于"跳过安全前置"，而不是"阻止同步" | `lock` job 改为**始终运行**，首步 `gate_lock.py require-enabled`（非 `true` 即退出 1）；`publish` 要求 `needs.lock.result == 'success'`（去掉 `skipped` 豁免）；`finalize` 同样只认 success | 未设置/false/空/非法值；状态 ref 不存在；锁占用；CAS 冲突；acquire 后 generate 失败；取消；finalize 失败 | `test_gate_lock.py::test_require_enabled_*`（6 组取值）+ 工作流结构断言 | 需要 `state/gate` ref 才能真实运行（M3 未授权） | 回滚即恢复 `if` 门控（但会重新引入该 P0，不建议） |
| **P0-3** | 高 | 模式按"有没有 check-run"在运行期自动切换 ⇒ 移除 check-run 即可降级到较弱的 status 判定 | `choose_mode(check_runs, contexts)` 是**数据驱动**而非**身份驱动** | 改为 `expected_mode(pr_obj)`：作者为 `github-actions[bot]` ⇒ status 模式；其他作者 ⇒ check-run 模式；作者未知 ⇒ 直接失败；不再有任何自动降级 | 人工 PR 无 check-run 但存在成功 status；bot PR 未声明 contexts；来源不可信；错 SHA；只有部分检查通过 | `test_gate_status.py` 新增 3 例（降级拒绝/作者未知/未声明 contexts） | 无 | 恢复旧函数即重新引入降级风险，禁止 |
| **P0-4** | 高 | 状态机缺 `IDLE/RECOVERY_REQUIRED` 显式状态；CAS 原子性只在本地模拟 | GitHub ref 上"读-改-写"不是原子；代码用 `force=false` 的 ref 更新近似 CAS，但**未在真实 ref 上验证** | 保留 `generation + expected_old_sha + force=false` 作为 CAS 契约；引入暂停态与恢复 CLI；**明确标注真实原子性未验证** | 并发写者、陈旧 SHA、暂停被普通 complete 清除、恢复重放、快照摘要不符 | 17 + 10 例 | **需真实 ref 与写权限（M3 未授权）** | 删除状态 ref（不可逆，需审计） |
| **P1-5** | 中 | 合并前置只校验 PR/base/head，未检查暂停态与锁归属 | `request_automerge.py` 不读 gate 状态 | 由 `finalize`/`verify` 门控自动合并 job（`needs.finalize`），并在暂停时 `pause` 状态 | 暂停态触发自动合并、finalize 失败仍合并 | 工作流 `needs` 断言 + `test_gate_lock` 暂停用例 | 同上 | 移除 `needs.finalize` |
| **P1-6** | 中 | 报告混用"被测 SHA / 报告提交 SHA"；进度公式与百分比不一致 | 报告生成时未记录代码树哈希 | 报告分列 `verified_code_sha` / `report_commit_sha` / `tree_sha`；进度按公开公式重算 | 代码变更后沿用旧 SHA 的绿色结果 | 报告 §13 + `git rev-parse HEAD^{tree}` | 无 | 重新出报告 |
| **P1-7** | 中 | `CODEOWNERS` 不存在；Ruleset 不要求审查 | 从未创建 | 新增 `.github/CODEOWNERS`（@Hawaiine，已核实账户存在）；Ruleset **不**开启 required_pull_request_reviews（否则机器人 PR 永远等待人工批准，与 PR-first 自动化冲突），并在报告标注"审查未强制" | owner 不存在；路径未覆盖；审查未强制被误报为已生效 | 文件存在性 + `GET /users/Hawaiine` + ruleset 回读 | CODEOWNERS 合并后才生效 | 删除文件 |
| **P1-8** | 中 | Actions 不允许创建 PR（`can_approve_pull_request_reviews=false`），Route A 端到端必然 403 | 仓库设置 | 已按授权改为 `true`（PUT 返回 204，回读确认）；仍保持 `default_workflow_permissions=read` | 设置回读不一致；开启后被误认为 E2E 通过 | 设置回读 | 平台设置（已执行） | PUT 回 false |

## 预演结论（本轮已验证 / 未验证）

- 已验证（L1/L2，模拟或纯函数）：P0-1 的 13 个拒绝路径、P0-2 的 6 组取值、P0-3 的降级拒绝、P0-4 的锁/暂停/恢复、工作流 `needs` 结构。
- 未验证（L3，平台）：`workflow_run` 真实触发（文件需在 main）、真实 `POST /statuses` 被 Ruleset 接受、
  真实 `state/gate` 的 CAS 与恢复、Actions 创建 PR 的真实 403→200 变化。

## 重试纪律

同一失败第二次出现必须先做根因复核；第三次必须更换实现或认定为平台阻塞。
本轮不重复"重跑 CI 看是否变绿"这类无效动作：每次失败都定位到具体断言并修复根因
（例：`test_missing_run_id_rejected` 因 CI 自带 `GITHUB_RUN_ID` 失败 → 修测试的环境依赖，而非放宽断言）。
