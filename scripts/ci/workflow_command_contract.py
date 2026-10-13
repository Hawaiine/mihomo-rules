#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr-verify.yml 的**必需命令合同**（Fix B）：验证验证命令**确实执行**，而不只是脚本名出现。

为什么需要它：仅用正则搜索 `run` 字符串里是否出现某个脚本路径，会被下面这些手法绕过：
  * `run: echo "NOT EXECUTED: python3 scripts/ci/verify_workflow_policy.py"`
  * 用 `printf` / `true` / `:` / 空命令等占位语句代替真实执行；
  * 把命令写进注释、引号里的说明文本、here-doc 内容等**不会执行**的字符串；
  * 追加 `|| true` / `|| :` 吞掉失败，使验证失败不传递到步骤退出状态；
  * 只保留脚本路径字符串，实际 shell 命令没有调用它；
  * 删除合同里的必要命令，或把必须执行的验证移进不会执行的条件分支。

因此本模块采用**严格解析**：把 `run` 脚本切成真实的逻辑命令行（去注释、合并反斜杠续行、
排除 here-doc 正文），解析出「命令词 + 参数」，并要求必需命令是**单一简单命令**
（不含 `|` / `||` / `&&` / `;` / `&` 等 shell 操作符），命令词必须是解释器
（python3/bash/sh…）或脚本自身——`echo`/`printf`/`true`/`:` 等一律不算执行。

合同（对 `pr-verify.yml` 三个必需 job 逐项声明；与工作流实际内容一致，改动必须同步）：
  * general-pr-verify：通用 PR guard、config 校验、ruleset 校验、图标 URL 可达性、
    README 统计口径、全量单元测试；辅助步骤：安装 pyyaml、解析并校验 PR base/head SHA。
  * workflow-policy-guard：实际调用 verify_workflow_policy.py；辅助步骤同上。
  * trusted-baseline-guard：实际调用 verify_policy_baseline.py（带 --base-sha/--head-sha），
    且**先**确认 base/head 提交可用（git cat-file -e）；辅助步骤同上。
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

PINNED_ICONS_SHA = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"
ICONS_REPO = "Hawaiine/Oasisic-Icons"

# 解释器（真正执行文件的命令词）
INTERPRETERS = {"python", "python3", "bash", "sh", "dash", "zsh", "node", "ruby", "perl"}
# 包装命令（可跳过，不改变「实际执行者」判定）
WRAPPERS = {"env", "sudo", "time", "command", "exec", "nohup"}
# 空操作/不执行命令
NOOP_COMMANDS = {"echo", "printf", "true", "false", ":", "cat", "tee", "test", "[", "exit",
                 "set", "return", "sleep", "ls", "pwd", "which", "type", "help"}
# shell 操作符（出现即说明该行不是单一简单命令）
OPERATORS = {"|", "||", "&&", ";", "&", ">>", ">", "<", "<<", ";;"}

# 必需命令合同
CONTRACT: dict[str, dict[str, Any]] = {
    "general-pr-verify": {
        "required_commands": [
            {"script": "verify_general_pr.py", "env": ["MIHOMO_ICON_REPO"], "desc": "通用 PR 只读 guard"},
            {"script": "verify_configs.py", "env": ["MIHOMO_ICON_REPO"], "desc": "config 树校验"},
            {"script": "verify_rulesets.py", "desc": "ruleset 校验"},
            {"script": "icon_urls.py", "desc": "生产图标 URL 可达性"},
            {"script": "readme_stats.py", "args": ["--check"], "desc": "README 统计口径"},
            {"tokens": ["-m", "unittest", "discover", "-s", "scripts/tests"], "command_word": "python3",
             "env": ["MIHOMO_ICON_REPO"], "desc": "全量单元测试"},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": "verify_general_pr.py"},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": "verify_general_pr.py"},
        ],
        "checkouts": [{"repository": ICONS_REPO, "ref": PINNED_ICONS_SHA, "desc": "固定版本图标仓库"},
                      {"pr_head": True, "desc": "检出 PR head"}],
    },
    "workflow-policy-guard": {
        "required_commands": [
            {"script": "verify_workflow_policy.py", "desc": "workflow 策略 guard"},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": "verify_workflow_policy.py"},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": "verify_workflow_policy.py"},
        ],
        "checkouts": [{"pr_head": True, "desc": "检出 PR head"}],
    },
    "trusted-baseline-guard": {
        "required_commands": [
            {"script": "verify_policy_baseline.py", "args": ["--base-sha", "--head-sha"],
             "desc": "可信基线 guard（候选树校验）"},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": "verify_policy_baseline.py"},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": "verify_policy_baseline.py"},
            {"tokens": ["git", "cat-file", "-e"], "before": "verify_policy_baseline.py",
             "desc": "确认 base/head 提交可用（必须在基线 guard 之前）"},
        ],
        "checkouts": [{"pr_head": True, "desc": "检出 PR head"}],
    },
}


@dataclass
class CommandLine:
    """一条**逻辑**命令行（已去注释、合并续行、排除 here-doc 正文）。"""
    text: str
    tokens: list[str]
    segments: list[list[str]]
    line_no: int
    index: int = 0            # 该 job 内所有逻辑命令行的**全局**序号（用于跨步骤顺序判定）
    from_heredoc: bool = False

    @property
    def clean(self) -> bool:
        """是否为单一简单命令（无 shell 操作符）。"""
        return len(self.segments) == 1

    def command_word(self) -> str | None:
        return command_word(self.tokens)


def _strip_comment(line: str) -> str:
    """去掉行尾注释（考虑引号状态）。"""
    out, quote = [], None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1].isspace()):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def split_command_lines(script: str) -> list[CommandLine]:
    """把 `run` 脚本切分为逻辑命令行；排除注释行与 here-doc 正文。"""
    raw_lines = script.split("\n")
    logical: list[tuple[str, int]] = []
    buf, start = "", 0
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        stripped = _strip_comment(line)
        if not stripped.strip():
            i += 1
            continue
        if not buf:
            start = i + 1
        if stripped.rstrip().endswith("\\"):
            buf += stripped.rstrip()[:-1] + " "
            i += 1
            continue
        logical.append((buf + stripped, start))
        buf = ""
        i += 1
    if buf:
        logical.append((buf, start))

    out: list[CommandLine] = []
    heredoc_until: str | None = None
    for text, line_no in logical:
        if heredoc_until is not None:
            if text.strip() == heredoc_until:
                heredoc_until = None
            continue
        try:
            tokens = shlex.split(text, comments=False, posix=True)
        except ValueError:
            tokens = text.split()
        hd = next((t for t in tokens if t.startswith("<<")), None)
        if hd is not None:
            term = hd.lstrip("<").lstrip("-").strip("'\"")
            if not term:
                idx = tokens.index(hd)
                if idx + 1 < len(tokens):
                    term = tokens[idx + 1].strip("'\"")
            heredoc_until = term or "EOF"
            continue
        out.append(CommandLine(text=text.strip(), tokens=tokens,
                               segments=_split_segments(tokens), line_no=line_no))
    return out


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segs: list[list[str]] = [[]]
    for tok in tokens:
        if tok in OPERATORS:
            if segs[-1]:
                segs.append([])
            continue
        segs[-1].append(tok)
    return [s for s in segs if s]


def command_word(tokens: list[str]) -> str | None:
    """解析真正的命令词（跳过 env/sudo/time/command 等包装）。"""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in WRAPPERS:
            i += 1
            while i < len(tokens) and ("=" in tokens[i] and not tokens[i].startswith("-")):
                i += 1          # env VAR=val
            if i < len(tokens) and tokens[i].startswith("-"):
                i += 1
                if i < len(tokens) and not tokens[i].startswith("-") and tok in ("sudo",):
                    i += 1
            continue
        return tok
    return None


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _is_path_token(token: str) -> bool:
    """像脚本路径的 token：含 `/`（或本身就是脚本名）、无空白。"""
    return bool(token) and " " not in token and "\t" not in token


def invokes_script(cmd: CommandLine, script: str) -> bool:
    """该行是否**真正执行**了 `script`（basename 匹配 + 命令词是解释器或脚本自身）。"""
    if not cmd.clean or cmd.from_heredoc:
        return False
    cw = cmd.command_word()
    if cw is None or cw in NOOP_COMMANDS:
        return False
    tokens = cmd.segments[0]
    matched = [t for t in tokens if _is_path_token(t) and _basename(t) == script]
    if not matched:
        return False
    if _basename(cw) == script:
        return True                      # 直接执行 ./scripts/...
    return _basename(cw) in INTERPRETERS


def matches_tokens(cmd: CommandLine, tokens: list[str], want_cw: str | None = None) -> bool:
    """该行（任一简单命令段）是否**真实执行**了指定命令。

    判定：该段的命令词不是空操作命令，且段内**精确包含**全部指定 token
    （精确匹配，不做子串匹配——`echo "pip install pyyaml"` 不算执行）。
    若给出 `want_cw`，还要求该段命令词的 basename 等于它。
    """
    for seg in cmd.segments:
        cw = command_word(seg)
        if cw is None or _basename(cw) in NOOP_COMMANDS:
            continue
        if want_cw is not None and _basename(cw) != want_cw:
            continue
        if all(r in seg for r in tokens):
            return True
    return False


def check_job_commands(job: dict, job_id: str) -> tuple[list[str], dict]:
    """校验单个必需 job 的命令合同。返回 (problems, details)。"""
    spec = CONTRACT[job_id]
    problems: list[str] = []
    steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
    lines: list[CommandLine] = []
    for si, step in enumerate(steps):
        run = step.get("run")
        if isinstance(run, str):
            for cmd in split_command_lines(run):
                cmd.step = step.get("name")  # type: ignore[attr-defined]
                cmd.step_index = si            # type: ignore[attr-defined]
                cmd.index = len(lines) + 1
                lines.append(cmd)
    details: dict[str, Any] = {"steps": len(steps), "logical_command_lines": len(lines),
                               "required_commands": [], "required_aux": []}

    # 必需命令：必须被**真正执行**
    for req in spec["required_commands"]:
        desc = req.get("desc", "")
        executed: list[CommandLine] = []
        for cmd in lines:
            if "script" in req:
                if invokes_script(cmd, req["script"]):
                    if all(any(a in t for t in cmd.segments[0]) for a in req.get("args", [])):
                        executed.append(cmd)
            elif "tokens" in req:
                if matches_tokens(cmd, req["tokens"], want_cw=req.get("command_word")):
                    executed.append(cmd)
        entry = {"desc": desc, "script": req.get("script"), "tokens": req.get("tokens"),
                 "executed_lines": [c.line_no for c in executed], "ok": bool(executed)}
        if not executed:
            needle = req.get("script") or req["tokens"][0]
            present = any(needle in c.text for c in lines) or any(
                needle in str(s.get("run") or "") for s in steps)
            if present:
                problems.append(f"{job_id}: {desc} —— 脚本/命令名出现但**没有被实际执行**"
                                f"（疑似 echo/注释/引号文本/占位空跑）：{needle}")
            else:
                problems.append(f"{job_id}: 缺少必需命令 —— {desc}（{needle}）")
            details["required_commands"].append(entry)
            continue
        # 必需命令所在步骤必须声明所需 env
        for key in req.get("env", []):
            ok_env = any(isinstance(s.get("env"), dict) and key in s["env"] for s in steps
                         if isinstance(s.get("run"), str) and needle_in_run(s, req))
            entry.setdefault("env_required", []).append({"key": key, "ok": ok_env})
            if not ok_env:
                problems.append(f"{job_id}: {desc} 所在步骤必须声明 env {key}")
        details["required_commands"].append(entry)

    # 辅助命令：必须真实执行（可为流水线/条件的一部分，但命令词不能是空操作）
    aux_lines: dict[str, int] = {}
    for aux in spec["required_aux"]:
        desc = aux.get("desc", "")
        hit = next((c for c in lines if matches_tokens(c, aux["tokens"])), None)
        if hit is None:   # 允许解释器包装形态（如 python3 -m pip install pyyaml）
            hit = next((c for c in lines if matches_tokens(c, aux["tokens"][1:] if len(aux["tokens"]) > 1
                                                           else aux["tokens"], want_cw=None)
                        and any(_basename(command_word(seg) or "") in INTERPRETERS
                                for seg in c.segments)), None)
        entry = {"desc": desc, "tokens": aux["tokens"], "ok": hit is not None,
                 "line_no": hit.line_no if hit else None,
                 "index": hit.index if hit else None}
        if hit is None:
            problems.append(f"{job_id}: 缺少必需辅助命令 —— {desc}（{' '.join(aux['tokens'])}）")
        elif "before" in aux:
            target = next((c for c in lines if invokes_script(c, aux["before"])), None)
            if target is None:
                problems.append(f"{job_id}: {desc} 要求 {aux['before']} 存在，但未找到该命令")
            else:
                aux_key = (getattr(hit, "step_index", 0), hit.index)
                tgt_key = (getattr(target, "step_index", 0), target.index)
                if aux_key >= tgt_key:
                    problems.append(f"{job_id}: {desc} 必须位于 {aux['before']} 之前"
                                    f"（实际 step {aux_key[0]}/line {aux_key[1]} ≥ "
                                    f"step {tgt_key[0]}/line {tgt_key[1]}）")
                entry["order_ok"] = aux_key < tgt_key
            aux_lines[aux["tokens"][0]] = hit.index
        details["required_aux"].append(entry)

    # 检出步骤合同
    checkouts = [s for s in steps if str(s.get("uses") or "").startswith("actions/checkout")]
    details["checkouts"] = []
    for want in spec.get("checkouts", []):
        if want.get("pr_head"):
            ok = any(_is_pr_head_checkout(s) for s in checkouts)
            problems.append(f"{job_id}: 必须检出 PR head（ref: ${{{{ steps.pr.outputs.head }}}}）") if not ok else None
        else:
            ok = any((s.get("with") or {}).get("repository") == want["repository"]
                     and str((s.get("with") or {}).get("ref") or "") == want["ref"] for s in checkouts)
            if not ok:
                problems.append(f"{job_id}: 必须检出 {want['repository']}@{want['ref']}（{want.get('desc', '')}）")
        details["checkouts"].append({**want, "ok": ok})
    return problems, details


def _is_pr_head_checkout(step: dict) -> bool:
    ref = str((step.get("with") or {}).get("ref") or "")
    return "pull_request.head.sha" in ref or "steps.pr.outputs.head" in ref


def needle_in_run(step: dict, req: dict) -> bool:
    run = str(step.get("run") or "")
    if "script" in req:
        return req["script"] in run
    return all(t in run for t in req["tokens"])


def check_required_jobs(doc: dict) -> tuple[list[str], dict]:
    """对 pr-verify.yml 的三个必需 job 执行命令合同校验。"""
    problems: list[str] = []
    jobs = doc.get("jobs") or {}
    report: dict[str, Any] = {}
    for job_id in CONTRACT:
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            problems.append(f"pr-verify.yml: 缺少必需 job '{job_id}'")
            continue
        probs, details = check_job_commands(job, job_id)
        problems.extend(f"pr-verify.yml: {p}" for p in probs)
        report[job_id] = details
    return problems, report
