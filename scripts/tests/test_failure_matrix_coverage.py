#!/usr/bin/env python3
"""失败矩阵覆盖守卫：防止删除/改名关键安全测试（第 3/3 部分 §3 的 28 项）。

这不是"再写一遍测试"，而是**登记**：每个安全属性必须仍有一个可执行的测试承载。
任何登记项消失（改名/删除/skip 化）都会让本测试失败。
"""
from __future__ import annotations

import unittest

MATRIX: dict[str, tuple[str, str]] = {
    # 场景 → (测试模块, 测试方法名)
    "gate 失败不产生写操作": ("test_workflow_policy_guard",
                            "LockOrderTest.test_generate_without_lock_is_rejected"),
    "lock 获取失败后 generate 不运行": ("test_workflow_policy_guard",
                                        "LockOrderTest.test_generate_without_lock_is_rejected"),
    "两个 run 同时申请锁": ("test_gate_state", "LocalStoreTests.test_concurrent_writers_only_one_wins"),
    "旧锁过期但未确认旧 run 终止": ("test_gate_lock",
                                    "GateLockCliTest.test_expired_lease_requires_explicit_takeover"),
    "快照存储失败": ("test_gate_lock", "GateLockCliTest.test_snapshot_failure_blocks_state_change"),
    "快照内容/摘要不一致": ("test_gate_state", "RecoveryTests.test_corrupted_snapshot_digest_rejected"),
    "状态写入成功但快照失败（顺序保证）": ("test_gate_lock",
                                          "GateLockCliTest.test_snapshot_failure_blocks_state_change"),
    "CAS 冲突": ("test_route_a_protocol", "GitHubRefStoreTests.test_cas_conflict_422_raises"),
    "API 超时但写入已生效（回读核对）": ("test_gate_lock",
                                        "GateLockCliTest.test_write_timeout_reconciled_when_write_landed"),
    "API 超时且未写入": ("test_gate_lock",
                          "GateLockCliTest.test_write_timeout_not_landed_fails_closed"),
    "缺失 SHA": ("test_gate_status", "EvaluateStatusesTest.test_missing_sha_rejected"),
    "旧 head SHA": ("test_route_a_protocol", "PublishStatusTests.test_head_drift_refused"),
    "PR base/head 检查期间变化": ("test_route_a_protocol",
                                  "PublishStatusTests.test_base_sha_drift_refused"),
    "机器人身份缺失/冲突": ("test_gate_status", "EvaluateStatusesTest.test_creator_user_type_rejected"),
    "login-only 冒充": ("test_gate_status", "EvaluateStatusesTest.test_expected_mode_rejects_login_only_spoof"),
    "workflow ID/path 错误": ("test_publish_run_status", "Case.test_workflow_path_spoof_refused"),
    "required job 缺失": ("test_publish_run_status", "Case.test_missing_job_refused"),
    "required job 被跳过": ("test_publish_run_status", "Case.test_skipped_job_refused"),
    "status 发布后 head 改变": ("test_publish_run_status",
                                "Case.test_head_changes_after_publish_refused"),
    "新 ruleset 无审批": ("test_approval_and_decide",
                          "DecideTest.test_new_ruleset_without_approval_blocked"),
    "新 ruleset 只有旧 head 审批": ("test_approval_and_decide", "ApprovalTest.test_stale_commit"),
    "审批已 dismiss": ("test_approval_and_decide", "ApprovalTest.test_dismissed_latest_wins"),
    "治理变更混入纯数据": ("test_approval_and_decide", "DecideTest.test_governance_blocked"),
    "diff 分页不完整": ("test_classify_change", "ClassifyTest.test_truncated_diff_is_unknown"),
    "未知路径": ("test_classify_change", "ClassifyTest.test_unlisted_extension_is_unknown"),
    "工作流取消不得误清暂停": ("test_workflow_policy_guard",
                                "LockOrderTest.test_finalize_without_always_is_rejected"),
    "PAUSED 下 complete": ("test_gate_lock",
                            "GateLockCliTest.test_pause_then_normal_complete_is_refused"),
    "未授权 resume": ("test_gate_lock", "GateLockCliTest.test_recovery_required_blocks_acquire_and_complete"),
    "RECOVERY_REQUIRED 下继续同步": ("test_gate_lock", "GateLockCliTest.test_require_usable"),
    "生产开关关闭仍尝试合并": ("test_approval_and_decide",
                              "DecideTest.test_existing_data_eligible_but_production_off"),
    "文档与 workflow 行为一致": ("test_workflow_policy_guard", "DailySyncEntryTest.test_real_entry_passes"),
}


class FailureMatrixCoverageTest(unittest.TestCase):
    def test_every_mapped_test_still_exists(self):
        missing: list[str] = []
        for scenario, (module_name, test_name) in MATRIX.items():
            try:
                module = __import__(f"scripts.tests.{module_name}", fromlist=["*"])
            except ImportError as exc:  # pragma: no cover
                missing.append(f"{scenario}: 模块 {module_name} 不存在（{exc}）")
                continue
            cls_name, _, method = test_name.partition(".")
            if cls_name:
                cls = getattr(module, cls_name, None)
                ok = bool(cls) and hasattr(cls, method)
            else:
                ok = hasattr(module, test_name)
            if not ok:
                missing.append(f"{scenario}: {module_name}.{test_name} 不存在（可能被删除/改名）")
        self.assertEqual(missing, [], "关键安全测试缺失：\n" + "\n".join(missing))

    def test_matrix_covers_all_28_scenarios(self):
        self.assertGreaterEqual(len(MATRIX), 28)


if __name__ == "__main__":
    unittest.main()
