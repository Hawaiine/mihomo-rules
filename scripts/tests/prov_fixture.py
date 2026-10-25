#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provenance 测试夹具：严格复刻真实平台数据（2026-10-13 实测值）。

实测事实（用于构造夹具，使 mock 与真实 API 形状一致）：
  * check run 的 `check_suite.id` = 103844138851（三个必需检查同属一个 suite）；
  * workflow run id = 38327515358，`path` = `.github/workflows/pr-verify.yml`，`event` = `pull_request`；
  * **check run id 恰好等于 job id**：general-pr-verify=115069523720、
    workflow-policy-guard=115069523482、trusted-baseline-guard=115069523714；
  * 三个 check run 的 `app` = {"slug": "github-actions", "id": 15368}。
"""
from __future__ import annotations

PR_VERIFY_PATH = ".github/workflows/pr-verify.yml"
OTHER_WORKFLOW_PATH = ".github/workflows/other-verify.yml"
SUITE = 103844138851
OTHER_SUITE = 999999999999
RUN_ID = 38327515358
OTHER_RUN_ID = 38327519999
JOB_IDS = {
    "general-pr-verify": 115069523720,
    "workflow-policy-guard": 115069523482,
    "trusted-baseline-guard": 115069523714,
}
NAMES = list(JOB_IDS)
HEAD = "d129e4526b0dac83297cb98df1e8c6b7ebba1b6c"
PR_NUMBER = 29
OTHER_SHA = "b" * 40
MAIN_SHA = "f4447b73023474cb2f62de51277397fb8df911f0"
APP = {"slug": "github-actions", "id": 15368}


def check_run(name, conclusion="success", status="completed", sha=HEAD, app=None, suite=SUITE,
              rid=None, started="2026-10-13T01:00:00Z", completed_at="2026-10-13T01:05:00Z"):
    return {"id": rid if rid is not None else JOB_IDS.get(name, 1), "name": name,
            "status": status, "conclusion": conclusion, "head_sha": sha,
            "app": dict(app or APP), "check_suite": {"id": suite}, "started_at": started,
            "completed_at": completed_at,
            "details_url": f"https://github.com/o/r/runs/{rid if rid is not None else JOB_IDS.get(name, 1)}"}


def workflow_run(rid=RUN_ID, path=PR_VERIFY_PATH, event="pull_request", sha=HEAD, suite=SUITE,
                 pr_number=PR_NUMBER, pull_requests="default", created="2026-10-13T00:00:00Z",
                 attempt=1):
    """真实形状：run 携带 `pull_requests[]`（来源认证据此绑定当前 PR）。

    `pull_requests="default"` 生成一条属于 `pr_number` 的关联；传 None 表示字段缺失，
    传列表则原样使用（用于负例）。
    """
    run = {"id": rid, "path": path, "event": event, "head_sha": sha, "check_suite_id": suite,
           "status": "completed", "conclusion": "success",
           "created_at": created, "run_attempt": attempt}
    if pull_requests == "default":
        run["pull_requests"] = [{"number": pr_number, "head": {"sha": sha},
                                 "base": {"ref": "main"}}]
    elif pull_requests is not None:
        run["pull_requests"] = pull_requests
    return run


def job(name, job_id=None, sha=HEAD, status="completed", conclusion="success"):
    return {"id": job_id if job_id is not None else JOB_IDS[name], "name": name, "head_sha": sha,
            "status": status, "conclusion": conclusion}


def all_checks(**kw):
    return [check_run(n, **kw) for n in NAMES]


def all_jobs(**kw):
    return [job(n, **kw) for n in NAMES]


def jobs_for(checks):
    """由 check run 派生对应 job 记录（真实平台：check run id == job id，结论一致）。"""
    return [job(c["name"], job_id=c["id"], sha=c["head_sha"], status=c["status"],
                conclusion=c["conclusion"]) for c in checks]


def jobs_index(jobs):
    return {int(j["id"]): j for j in jobs}


def pr(head=HEAD, state="open", base="main", base_sha=MAIN_SHA, node_id="PR_node_1", merged=False):
    return {"state": state, "merged": merged, "number": 29, "node_id": node_id,
            "base": {"ref": base, "sha": base_sha}, "head": {"sha": head},
            "user": {"login": "Hawaiine", "type": "User"}}


class FakeApi:
    """可注入的只读 API 假实现（按真实 URL 形状路由）。"""

    def __init__(self, *, pr_before=None, pr_after=None, check_runs_seq=None, workflow_runs=None,
                 jobs_by_run=None, main_sha=MAIN_SHA, main_sha_seq=None, errors=None,
                 runs_body_override=None):
        self.pr_before = pr_before if pr_before is not None else pr()
        self.pr_after = pr_after if pr_after is not None else self.pr_before
        self.check_runs_seq = list(check_runs_seq) if check_runs_seq else [all_checks()]
        self.workflow_runs = workflow_runs if workflow_runs is not None else [workflow_run()]
        self.jobs_by_run = jobs_by_run
        self.last_checks: list[dict] = []
        self.main_sha = main_sha
        self.main_sha_seq = list(main_sha_seq) if main_sha_seq else None
        self.errors = {k: list(v) for k, v in (errors or {}).items()}
        self.runs_body_override = runs_body_override
        self.calls: list[str] = []

    def __call__(self, url, token):
        self.calls.append(url)
        for key, codes in self.errors.items():
            if key in url and codes:
                return codes.pop(0), {"_error": "simulated failure"}
        if "/pulls/" in url:
            seen = sum(1 for c in self.calls if "/pulls/" in c)
            return 200, (self.pr_before if seen == 1 else self.pr_after)
        if "/check-runs" in url:
            runs = self.check_runs_seq[0] if len(self.check_runs_seq) == 1 else self.check_runs_seq.pop(0)
            self.last_checks = runs
            return 200, {"check_runs": runs}
        if "/actions/runs/" in url and url.endswith("/jobs?per_page=100"):
            rid = int(url.split("/actions/runs/")[1].split("/")[0])
            if self.jobs_by_run is not None:
                return 200, {"jobs": self.jobs_by_run.get(rid, [])}
            suites = {r["id"]: r["check_suite_id"] for r in self.workflow_runs}
            suite = suites.get(rid)
            return 200, {"jobs": jobs_for([c for c in self.last_checks
                                           if c.get("check_suite", {}).get("id") == suite])}
        if "/actions/runs?" in url or "/actions/runs" in url:
            if self.runs_body_override is not None:
                return 200, self.runs_body_override
            return 200, {"workflow_runs": self.workflow_runs}
        if "/git/ref/heads/" in url:
            sha = self.main_sha
            if self.main_sha_seq:
                sha = self.main_sha_seq[0] if len(self.main_sha_seq) == 1 else self.main_sha_seq.pop(0)
            return 200, {"object": {"sha": sha}}
        raise AssertionError("unexpected URL: " + url)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.waits: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.t += seconds
