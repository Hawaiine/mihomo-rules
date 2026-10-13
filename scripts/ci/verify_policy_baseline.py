#!/usr/bin/env python3
"""可信基线 guard（只读）：用 PR **base** 版本的 guard 逻辑校验候选版本，并对「削弱」硬失败。

动机：同一个 PR 既改检查规则又靠改后的规则通过，会形成自证循环。因此：
  1. 若变更触及安全敏感路径，则把 **base 版本** 的 `scripts/ci/*` 取出来，
     用 base 逻辑去校验 **候选树** 的 workflow（候选无法用自己的新规则给自己放行）。
  2. 再对「削弱」做硬失败断言（删除必需检查 / 新增提权触发 / 新增直推 main /
     测试范围缩小 / 移除 guard 调用 等）。

只读：不写仓库、不访问网络。base 版本通过 `git archive` 取出到临时目录。

用法：
  python3 scripts/ci/verify_policy_baseline.py --base-sha <sha> --head-sha <sha> \
      [--repo-root .] [--json out.json]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

SENSITIVE_PREFIXES = (
    ".github/workflows/",
    ".github/required-checks.json",
    "scripts/ci/",
    "scripts/verify_configs.py",
    "scripts/verify_rulesets.py",
    "scripts/readme_stats.py",
    "scripts/lib/policy.py",
    "scripts/lib/validators.py",
    "scripts/batch_update.py",
    "scripts/commit_writer.py",
    "scripts/tests/",
)
REQUIRED_CHECKS_PATH = ".github/required-checks.json"
PR_VERIFY = ".github/workflows/pr-verify.yml"
CANDIDATE_SYNC = ".github/workflows/candidate-pr-sync.yml"
LEGACY_DIRECT_WRITE = ".github/workflows/daily-sync.yml"
BASE_GUARD = "scripts/ci/verify_workflow_policy.py"
PUSH_MAIN_RE = re.compile(r"git\s+push[^\n]*(?:\bmain\b|\$\{\{\s*github\.ref_name\s*\}\}|HEAD:main)")
TEST_DEF_RE = re.compile(r"^\s*def test_", re.MULTILINE)


class BaselineError(Exception):
    """无法判定 → fail-closed。"""


def git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BaselineError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def git_grep(pattern: str, revision: str, path: str, cwd: Path) -> str:
    """git grep 的容错封装：退出码 1 = 无匹配（返回空串），其他非 0 仍视为失败。"""
    proc = subprocess.run(["git", "grep", "-h", "-E", pattern, revision, "--", path],
                          cwd=cwd, capture_output=True, text=True)
    if proc.returncode == 0:
        return proc.stdout
    if proc.returncode == 1:
        return ""
    raise BaselineError(f"git grep failed: {proc.stderr.strip()[:300]}")


def changed_files(base: str, head: str, root: Path) -> list[str]:
    out = git(["diff", "--name-only", "--diff-filter=ACDMRT", f"{base}..{head}"], root)
    if not out.strip():
        out = git(["diff", "--name-only", "--diff-filter=ACDMRT", f"{base}...{head}"], root)
    return [line.strip() for line in out.splitlines() if line.strip()]


def sensitive(files: list[str]) -> list[str]:
    return [f for f in files if f.startswith(SENSITIVE_PREFIXES)]


def file_at(revision: str, path: str, root: Path) -> str | None:
    proc = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def extract_base_tree(base: str, root: Path, dest: Path) -> bool:
    """取出 base 版本的 scripts/ci 与 scripts/lib 到临时目录；guard 不存在则返回 False。

    逐路径归档：某个路径在 base 不存在（例如 scripts/lib 曾改名）不应导致整体失败。
    """
    for path in ("scripts/ci", "scripts/lib"):
        proc = subprocess.run(["git", "archive", "--format=tar", base, path],
                              cwd=root, capture_output=True)
        if proc.returncode != 0:
            continue
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            tar.extractall(dest / "base")
    return (dest / "base" / BASE_GUARD).exists()


def extract_tree(revision: str, root: Path, dest: Path) -> None:
    """把某个 revision 的完整树取出到 dest（用于用 base 逻辑校验候选树）。"""
    proc = subprocess.run(["git", "archive", "--format=tar", revision], cwd=root, capture_output=True)
    if proc.returncode != 0:
        raise BaselineError(f"git archive {revision} failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    archive = dest / f"tree-{revision[:12]}.tar"
    archive.write_bytes(proc.stdout)
    with tarfile.open(archive) as tar:
        tar.extractall(dest)


def run_base_guard(dest: Path, candidate_root: Path) -> tuple[bool, str]:
    """用 base 版本的 guard 校验**候选树**的 workflow。"""
    script = dest / "base" / BASE_GUARD
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(candidate_root)],
        capture_output=True, text=True, cwd=str(candidate_root),
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def check_entries(text: str) -> dict[str, dict]:
    """把必需检查清单解析成 {name: source}，兼容 checks[] 与旧 required[]。

    解析失败一律 fail-closed（BaselineError），不静默降级为「无检查」。
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"required-checks manifest unreadable: {exc}") from exc
    if isinstance(doc, dict) and isinstance(doc.get("checks"), list):
        out: dict[str, dict] = {}
        for item in doc["checks"]:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = dict(item.get("source") or {})
            elif isinstance(item, str):
                out[item] = {}
        return out
    return {str(n): {} for n in ((doc or {}).get("required") or [])}


def _strip_comment(line: str) -> str:
    """去掉行尾注释（考虑引号状态）。"""
    out, quote = [], None
    for ch in line:
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
    return "".join(out)


def _logical_lines(script: str) -> list[str]:
    """把 run 脚本切成逻辑命令行（去注释、合并续行、排除 here-doc 正文）。"""
    import shlex

    raw = script.split("\n")
    logical: list[str] = []
    buf = ""
    for line in raw:
        stripped = _strip_comment(line)
        if not stripped.strip():
            continue
        if stripped.rstrip().endswith("\\"):
            buf += stripped.rstrip()[:-1] + " "
            continue
        logical.append(buf + stripped)
        buf = ""
    if buf:
        logical.append(buf)
    out: list[str] = []
    heredoc: str | None = None
    for text in logical:
        if heredoc is not None:
            if text.strip() == heredoc:
                heredoc = None
            continue
        try:
            tokens = shlex.split(text, comments=False, posix=True)
        except ValueError:
            tokens = text.split()
        hd = next((t for t in tokens if t.startswith("<<")), None)
        if hd is not None:
            heredoc = hd.lstrip("<").lstrip("-").strip("'\"") or "EOF"
            continue
        out.append(text)
    return out


def _segments(text: str) -> list[list[str]]:
    """按 shell 操作符切成简单命令段。"""
    import shlex

    ops = {"|", "||", "&&", ";", "&", ">>", ">", "<", "<<"}
    try:
        tokens = shlex.split(text, comments=False, posix=True)
    except ValueError:
        tokens = text.split()
    segs: list[list[str]] = [[]]
    for tok in tokens:
        if tok in ops:
            if segs[-1]:
                segs.append([])
            continue
        segs[-1].append(tok)
    return [s for s in segs if s]


NOOP_COMMANDS = {"echo", "printf", "true", "false", ":", "cat", "tee", "test", "[", "exit",
                 "set", "return", "sleep", "ls", "pwd", "which", "type", "help"}
BLOCK_OPENERS = {"if", "for", "while", "until", "case", "select"}
BLOCK_CLOSERS = {"fi", "done", "esac"}


def _opens_block(tokens: list[str]) -> bool:
    if not tokens:
        return False
    first = tokens[0]
    if first in BLOCK_OPENERS or first in ("{", "(", "function"):
        return True
    return "()" in first or (len(tokens) > 1 and tokens[1] == "()")


def _closes_block(tokens: list[str]) -> bool:
    return bool(tokens) and (tokens[0] in BLOCK_CLOSERS or tokens[0] in ("}", ")"))


def _annotate(texts: list[str]) -> list[dict]:
    """给逻辑命令行标注 (text, tokens, depth, top_level) —— Fix A2 的独立实现。

    出现在 if/for/while/case/子 shell/函数体/单行块内的命令**不算顶层执行**。
    """
    import shlex

    out: list[dict] = []
    depth = 0
    for text in texts:
        try:
            tokens = shlex.split(text, comments=False, posix=True)
        except ValueError:
            tokens = text.split()
        header = _opens_block(tokens)
        if _closes_block(tokens) and not header:
            depth = max(0, depth - 1)
        out.append({"text": text, "tokens": tokens, "depth": depth,
                    "top_level": depth == 0 and not header})
        if header:
            depth += 1
            if tokens and tokens[-1] in (BLOCK_CLOSERS | {"}", ")"}):
                depth = max(0, depth - 1)
    return out


def _interpreter_target(seg: list[str]) -> str | None:
    """解释器真正执行的目标（第一个位置参数）；`-m`/`-c` 形态返回 None。"""
    idx = _command_word_index(seg)
    if idx is None:
        return None
    i = idx + 1
    while i < len(seg):
        tok = seg[i]
        if tok == "--":
            return seg[i + 1] if i + 1 < len(seg) else None
        if tok in ("-m", "-c"):
            return None
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        return tok
    return None


def _command_word_index(seg: list[str]) -> int | None:
    i = 0
    while i < len(seg):
        tok = seg[i]
        if tok in WRAPPERS:
            i += 1
            while i < len(seg) and "=" in seg[i] and not seg[i].startswith("-"):
                i += 1
            if i < len(seg) and seg[i].startswith("-"):
                i += 1
                if i < len(seg) and not seg[i].startswith("-") and tok == "sudo":
                    i += 1
            continue
        return i
    return None
INTERPRETERS = {"python", "python3", "bash", "sh", "dash", "zsh", "node", "ruby", "perl"}
WRAPPERS = {"env", "sudo", "time", "command", "exec", "nohup"}


def _command_word(seg: list[str]) -> str | None:
    i = 0
    while i < len(seg):
        tok = seg[i]
        if tok in WRAPPERS:
            i += 1
            while i < len(seg) and "=" in seg[i] and not seg[i].startswith("-"):
                i += 1
            continue
        return tok
    return None


def executed_commands(doc: dict) -> list[tuple[str, list[str]]]:
    """返回该 workflow 中**真正执行**的 (命令词, 参数) 列表。

    严格口径：只接受单一简单命令（无 shell 操作符）、命令词不是空操作命令、
    参数中不含带空白的「整段字符串」（防止把命令塞进引号/echo 文本）。
    """
    out: list[tuple[str, list[str]]] = []
    for job in (doc.get("jobs") or {}).values():
        for step in ((job or {}).get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for line in _logical_lines(run):
                segs = _segments(line)
                if len(segs) != 1:
                    continue
                cw = _command_word(segs[0])
                if cw is None:
                    continue
                base_cw = cw.rsplit("/", 1)[-1]
                if base_cw in NOOP_COMMANDS:
                    continue
                out.append((base_cw, segs[0][1:]))
    return out


# 必需命令合同（与 scripts/ci/workflow_command_contract.py 的集合保持一致；此处是
# **独立实现**，不 import 候选树代码，以保证用 base 可信逻辑判定候选树）。
REQUIRED_JOB_COMMANDS: dict[str, dict] = {
    "general-pr-verify": {
        "required_commands": [
            {"script": "verify_general_pr.py"},
            {"script": "verify_configs.py"},
            {"script": "verify_rulesets.py"},
            {"script": "icon_urls.py"},
            {"script": "readme_stats.py", "args": ["--check"]},
            {"tokens": ["-m", "unittest", "discover", "-s", "scripts/tests"]},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": "verify_general_pr.py"},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": "verify_general_pr.py"},
        ],
    },
    "workflow-policy-guard": {
        "required_commands": [{"script": "verify_workflow_policy.py"}],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": "verify_workflow_policy.py"},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": "verify_workflow_policy.py"},
        ],
    },
    "trusted-baseline-guard": {
        "required_commands": [
            {"script": "verify_policy_baseline.py", "args": ["--base-sha", "--head-sha"]},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": "verify_policy_baseline.py"},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": "verify_policy_baseline.py"},
            {"tokens": ["git", "cat-file", "-e"], "desc": "base/head 提交可用性检查",
             "before": "verify_policy_baseline.py"},
        ],
    },
}
EXECUTABLE_SCRIPT_COMMANDS = {"verify_general_pr.py", "verify_configs.py", "verify_rulesets.py",
                              "icon_urls.py", "readme_stats.py", "verify_workflow_policy.py",
                              "verify_policy_baseline.py"}


def executed_commands_indexed(doc: dict) -> list[dict]:
    """逐**简单命令段**记录实际执行命令：{step, line, seg, cw, args, clean}。

    `clean=True` 表示该行是**单一简单命令**（不含 `|`/`||`/`&&`/`;` 等操作符）——
    必需命令要求 clean（防止 `|| true` 吞掉失败）；辅助命令允许出现在流水线/条件中。
    """
    out: list[dict] = []
    for job in (doc.get("jobs") or {}).values():
        for si, step in enumerate((job or {}).get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for ci, ann in enumerate(_annotate(_logical_lines(run))):
                segs = _segments(ann["text"])
                clean = len(segs) == 1
                for gi, seg in enumerate(segs):
                    cw = _command_word(seg)
                    if cw is None:
                        continue
                    base_cw = cw.rsplit("/", 1)[-1]
                    if base_cw in NOOP_COMMANDS:
                        continue
                    out.append({"step": si, "line": ci, "seg": gi, "cw": base_cw,
                                "args": seg[1:], "seg_tokens": seg, "clean": clean,
                                "top_level": ann["top_level"], "depth": ann["depth"]})
    return out


def _invokes(entry: dict, script: str) -> bool:
    """Fix A1/A2（独立实现）：必须**顶层**单一简单命令，且脚本是解释器的第一个位置参数。

    `python3 -m py_compile x.py`、`python3 -c "..." x.py`、函数体/条件分支里的调用、
    echo/注释/引号文本中的脚本名，一律不算「正在执行」。
    """
    if not entry["clean"] or not entry.get("top_level"):
        return False
    cw = entry["cw"]
    if _basename(cw) == script:
        return " " not in cw and "\t" not in cw
    if _basename(cw) not in INTERPRETERS:
        return False
    target = _interpreter_target(entry.get("seg_tokens") or [])
    return target is not None and _basename(target) == script and " " not in target and "\t" not in target


def check_command_contract(doc: dict) -> list[str]:
    """Fix B：候选 pr-verify.yml 的必要命令必须**真正执行**（不是只出现脚本名）。"""
    problems: list[str] = []
    jobs = doc.get("jobs") or {}
    for job_id, spec in REQUIRED_JOB_COMMANDS.items():
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            problems.append(f"{PR_VERIFY}: required job {job_id!r} missing")
            continue
        indexed = executed_commands_indexed({"jobs": {job_id: job}})
        text = " ".join(str(s.get("run") or "") for s in ((job.get("steps") or [])))
        for want in spec["required_commands"]:
            if "script" in want:
                hits = [e for e in indexed if e["clean"] and _invokes(e, want["script"])]
            else:
                hits = [e for e in indexed if e["clean"]
                        and all(t in ([e["cw"]] + e["args"]) for t in want["tokens"])]
            need = want.get("args", [])
            ok = any(all(any(a in t for t in e["args"]) for a in need) for e in hits)
            if not ok:
                label = want.get("script") or " ".join(want["tokens"])
                if label in text:
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— {label} 只出现脚本名"
                                    f"但**没有被真正执行**（echo/注释/引号文本/here-doc/条件分支/"
                                    f"|| true 等空跑或执行语义绕过）")
                else:
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— 缺少必需命令 {label}")
        for aux in spec["required_aux"]:
            hits = [e for e in indexed if all(t in ([e["cw"]] + e["args"]) for t in aux["tokens"])]
            if not hits:
                problems.append(f"{PR_VERIFY}: job {job_id!r} —— 缺少必需辅助命令 "
                                f"{' '.join(aux['tokens'])}（{aux.get('desc', '')}）")
                continue
            if "before" in aux:
                targets = [e for e in indexed if _invokes(e, aux["before"])]
                if not targets:
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— {aux.get('desc', '')} 要求 "
                                    f"{aux['before']} 存在，但未找到该命令")
                elif min((e["step"], e["line"]) for e in hits) >= min((e["step"], e["line"])
                                                                      for e in targets):
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— {aux.get('desc', '')} 必须位于 "
                                    f"{aux['before']} 之前（步骤级顺序合同）")
    return problems


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


# ---------------------------------------------------- Fix B（独立实现）：auto-merge 合同
AUTOMERGE_JOB_ID = "request-auto-merge"
AUTOMERGE_ENV = "GITHUB_TOKEN"
AUTOMERGE_REQUIRED_STEPS: list[dict] = [
    {"script": "await_checks.py", "args": ["--repo", "--pr", "--expected-head-sha"],
     "desc": "有界等待必需检查完成（只读轮询）"},
    {"script": "verify_required_checks.py", "args": ["--repo", "--pr", "--head-sha"],
     "desc": "严格必需检查验证（来源/身份/状态复核）"},
    {"script": "request_automerge.py", "args": ["--repo", "--pr", "--head-sha"],
     "desc": "请求 auto-merge（唯一允许的 mutation 路径）"},
]
# 只认「写」形态：GraphQL mutation 名 / REST merge 端点 / gh pr merge / 直接 graphql 请求
DIRECT_MUTATION_MARKERS = ("enablePullRequestAutoMerge", "disablePullRequestAutoMerge")
DIRECT_MUTATION_PATTERNS = (
    (re.compile(r"\bgh\s+pr\s+merge\b"), "gh pr merge（绕过 helper 的合并路径）"),
    (re.compile(r"\bgh\s+api\b[^\n]*\bgraphql\b"), "gh api graphql（workflow 内直接 GraphQL）"),
    (re.compile(r"\bgh\s+api\b[^\n]*(-X|--method)\s*(PUT|POST|PATCH|DELETE)[^\n]*\bmerge\b"),
     "gh api 写请求命中 merge"),
    (re.compile(r"/(?:repos/[^\s\"\']+/)?pulls/[^\s\"\']*/merge\b"), "REST merge 端点"),
    (re.compile(r"\b(?:curl|wget|http|httpie)\b[^\n]*\bgraphql\b"), "HTTP 客户端直接发 GraphQL"),
)


def workflow_files_at(revision: str, root: Path) -> list[str]:
    out = git(["ls-tree", "-r", "--name-only", revision, "--", ".github/workflows"], root)
    return [ln.strip() for ln in out.splitlines() if ln.strip().endswith((".yml", ".yaml"))]


def direct_mutation_problems(doc: dict, name: str) -> list[str]:
    """Fix B3（独立实现）：workflow 内禁止直接发送 auto-merge / 合并 mutation。"""
    problems: list[str] = []
    for jid, job in (doc.get("jobs") or {}).items():
        for step in ((job or {}).get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for line in _logical_lines(run):
                marks = [m for m in DIRECT_MUTATION_MARKERS if m in line]
                if marks:
                    problems.append(f"{name}: job '{jid}' 出现直接 auto-merge mutation 标记 {marks}"
                                    f" —— mutation 只能由 scripts/ci/request_automerge.py 执行")
                    continue
                for pattern, why in DIRECT_MUTATION_PATTERNS:
                    if pattern.search(line):
                        problems.append(f"{name}: job '{jid}' 命中直接合并路径（{why}）：{line.strip()[:120]!r}")
    return problems


def check_automerge_contract(doc: dict) -> list[str]:
    """Fix B1/B2（独立实现）：三个前置命令必须真正执行、顺序正确、独立步骤、带 env。"""
    problems: list[str] = []
    job = (doc.get("jobs") or {}).get(AUTOMERGE_JOB_ID)
    if not isinstance(job, dict):
        return [f"{CANDIDATE_SYNC}: 缺少 auto-merge job（无法验证前置命令合同）"]
    steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
    indexed = executed_commands_indexed({"jobs": {AUTOMERGE_JOB_ID: job}})
    found: list[int] = []
    for want in AUTOMERGE_REQUIRED_STEPS:
        label = want["script"]
        hits = [e for e in indexed if _invokes(e, label)
                and all(any(a in t for t in e["seg_tokens"]) for a in want["args"])]
        if not hits:
            present = any(label in str(s.get("run") or "") for s in steps)
            problems.append(f"{CANDIDATE_SYNC}: {want['desc']} —— {label} "
                            + ("出现但**没有被实际执行**（echo/注释/引号文本/非顶层上下文/缺参数）"
                               if present else f"缺少必需命令（{' '.join(want['args'])}）"))
            continue
        hit = hits[0]
        if AUTOMERGE_ENV not in (steps[hit["step"]].get("env") or {}):
            problems.append(f"{CANDIDATE_SYNC}: {want['desc']} 所在步骤必须声明 env {AUTOMERGE_ENV}")
        found.append(hit["step"])
    if len(found) == len(AUTOMERGE_REQUIRED_STEPS) and (len(set(found)) != len(found)
                                                        or found != sorted(found)):
        problems.append(f"{CANDIDATE_SYNC}: 三个前置命令必须按 await_checks → verify_required_checks → "
                        f"request_automerge 的顺序位于**独立步骤**中（实际 step={found}）")
    return problems


def weakening_problems(base: str, head: str, root: Path) -> list[str]:
    problems: list[str] = []

    # 结构化辅助（一律基于解析结果，不认注释里的关键词）
    def triggers_of(text: str) -> set[str]:
        doc = yaml.safe_load(text) or {}
        raw = doc.get("on", doc.get(True))
        if isinstance(raw, str):
            return {raw}
        if isinstance(raw, list):
            return {str(x) for x in raw}
        if isinstance(raw, dict):
            return {str(k) for k in raw}
        return set()

    def write_all(text: str) -> bool:
        doc = yaml.safe_load(text) or {}
        return doc.get("permissions") == "write-all" or any(
            (job or {}).get("permissions") == "write-all" for job in (doc.get("jobs") or {}).values())

    def run_scripts(text: str) -> list[str]:
        doc = yaml.safe_load(text) or {}
        return [str(step.get("run")) for job in (doc.get("jobs") or {}).values()
                for step in ((job or {}).get("steps") or []) if isinstance(step.get("run"), str)]

    def pushes_main(text: str) -> bool:
        return any(PUSH_MAIN_RE.search(line) for run in run_scripts(text) for line in run.splitlines())

    # 必需检查集合与**来源绑定**不得被削弱
    base_req = file_at(base, REQUIRED_CHECKS_PATH, root)
    head_req = file_at(head, REQUIRED_CHECKS_PATH, root)
    if base_req is not None:
        base_checks = check_entries(base_req)
        if head_req is None:
            problems.append(f"{REQUIRED_CHECKS_PATH}: removed (required checks must not disappear)")
        else:
            head_checks = check_entries(head_req)
            for missing in sorted(set(base_checks) - set(head_checks)):
                problems.append(f"{REQUIRED_CHECKS_PATH}: required check {missing!r} removed")
            for name, base_src in sorted(base_checks.items()):
                if name not in head_checks:
                    continue
                head_src = head_checks[name]
                if base_src.get("app_id") is not None:
                    if head_src.get("app_id") != base_src.get("app_id") or \
                            head_src.get("app_slug") != base_src.get("app_slug"):
                        problems.append(
                            f"{REQUIRED_CHECKS_PATH}: {name} 的预期来源被改变/移除"
                            f"（base={base_src} head={head_src}）——来源绑定不得削弱")
                elif not head_src:
                    problems.append(f"{REQUIRED_CHECKS_PATH}: {name} 缺少来源绑定（source）")

    # pr-verify job 不得删除；job 名必须稳定；必需 job 不得获得可跳过的 job-level if
    base_prv = file_at(base, PR_VERIFY, root)
    head_prv = file_at(head, PR_VERIFY, root)
    if base_prv is not None and head_prv is not None:
        def job_ids(text: str) -> dict[str, object]:
            doc = yaml.safe_load(text) or {}
            return {str(k): (v or {}).get("name") for k, v in (doc.get("jobs") or {}).items()}

        def job_conditions(text: str) -> dict[str, str]:
            doc = yaml.safe_load(text) or {}
            out: dict[str, str] = {}
            for key, job in (doc.get("jobs") or {}).items():
                value = (job or {}).get("if")
                # 注意：`if: false` 会被 YAML 解析成布尔 False，`False or ""` 会丢信息，
                # 因此必须显式判 None。
                out[str(key)] = "" if value is None else str(value)
            return out

        base_jobs, head_jobs = job_ids(base_prv), job_ids(head_prv)
        for job in sorted(set(base_jobs) - set(head_jobs)):
            problems.append(f"{PR_VERIFY}: job {job!r} removed")
        for job, name in head_jobs.items():
            if name != job:
                problems.append(f"{PR_VERIFY}: job {job!r} name changed to {name!r} (check name must stay stable)")
        base_conds, head_conds = job_conditions(base_prv), job_conditions(head_prv)
        for job, cond in head_conds.items():
            if cond and not base_conds.get(job):
                problems.append(f"{PR_VERIFY}: job {job!r} 新增 job-level if ({cond!r}) —— "
                                f"必需检查不得变得可被跳过（跳过的检查可能被算作成功）")
        if "workflow_dispatch" not in triggers_of(base_prv) and "workflow_dispatch" in triggers_of(head_prv):
            problems.append(f"{PR_VERIFY}: 新增 workflow_dispatch —— dispatch 触发的检查不满足 "
                            f"ruleset 必需检查（官方文档）")

    # 禁止新增提权触发 / write-all / 直推 main —— 一律基于**解析后的结构**判定，
    # 不接受「注释里出现关键词」这种脆弱信号。
    for path in changed_files(base, head, root):
        if not path.endswith((".yml", ".yaml")):
            continue
        before = file_at(base, path, root) or ""
        after = file_at(head, path, root)
        if after is None:
            continue
        if path == LEGACY_DIRECT_WRITE:
            # 旧直推 workflow：任何修改都视为「重新武装」风险（内容同时被策略 guard 的
            # sha256 pin 锁定），不依赖关键词出现。
            problems.append(f"{path}: legacy direct-write workflow must never be modified or re-armed")
            continue
        if "pull_request_target" not in triggers_of(before) and "pull_request_target" in triggers_of(after):
            problems.append(f"{path}: adds pull_request_target trigger (forbidden)")
        if not write_all(before) and write_all(after):
            problems.append(f"{path}: adds permissions: write-all (forbidden)")
        if not pushes_main(before) and pushes_main(after):
            problems.append(f"{path}: adds a push to main (forbidden)")

    # 测试范围不得缩小
    base_tests = sorted(p for p in changed_files(base, head, root) if p.startswith("scripts/tests/"))
    deleted_tests = [p for p in base_tests if file_at(head, p, root) is None]
    if deleted_tests:
        problems.append(f"scripts/tests: test files deleted: {sorted(deleted_tests)}")
    base_count = len(TEST_DEF_RE.findall(git_grep(r"^\s*def test_", base, "scripts/tests", root)))
    head_count = len(TEST_DEF_RE.findall(git_grep(r"^\s*def test_", head, "scripts/tests", root)))
    if head_count < base_count:
        problems.append(f"scripts/tests: test case count shrank ({base_count} -> {head_count})")

    # Fix B5：候选树中所有 workflow 的直接 mutation 与 auto-merge 命令合同，
    # 由本 guard 用**独立实现**判定（base 上存在该 guard 后即持续生效）。
    for wf in workflow_files_at(head, root):
        text = file_at(head, wf, root)
        if text is None:
            continue
        try:
            wf_doc = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{wf}: 无法解析候选 workflow YAML（fail-closed）：{exc}")
            continue
        problems.extend(direct_mutation_problems(wf_doc, wf))
        if wf == CANDIDATE_SYNC:
            problems.extend(check_automerge_contract(wf_doc))

    # guard 调用不得被移除（同时由 check_command_contract 做「真正执行」判定）
    if head_prv is not None:
        for script in ("scripts/ci/verify_general_pr.py", BASE_GUARD):
            if script not in head_prv:
                problems.append(f"{PR_VERIFY}: guard invocation {script} missing")
        # Fix B：候选 pr-verify.yml 的必要命令必须**真正执行**
        # （本 guard 自带独立严格解析，不 import 候选树代码，避免候选自证安全）
        problems.extend(check_command_contract(yaml.safe_load(head_prv) or {}))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="可信基线 guard（只读）")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    problems: list[str] = []
    info: dict = {}
    try:
        files = changed_files(args.base_sha, args.head_sha, root)
        touched = sensitive(files)
        info = {"base_sha": args.base_sha, "head_sha": args.head_sha,
                "changed_files": len(files), "sensitive_files": touched}
        if not touched:
            info["base_guard_applied"] = False
            info["note"] = "无安全敏感路径变更；仅执行削弱断言"
        else:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp)
                have_base_guard = extract_base_tree(args.base_sha, root, dest)
                info["base_guard_applied"] = have_base_guard
                if have_base_guard:
                    candidate = dest / "candidate"
                    candidate.mkdir()
                    extract_tree(args.head_sha, root, candidate)
                    ok, output = run_base_guard(dest, candidate)
                    info["base_guard_output_tail"] = output[-2000:]
                    if not ok:
                        problems.append(
                            "base 版本 guard 判定候选树不合规（候选不得用自身新规则给自己放行）：\n" + output[-1200:]
                        )
                else:
                    info["base_guard_note"] = "base 版本尚无该 guard（本轮首次引入），改用内置削弱断言"
        problems.extend(weakening_problems(args.base_sha, args.head_sha, root))
    except BaselineError as exc:
        problems.append(f"fail-closed: {exc}")
    result = {"ok": not problems, "problems": problems, **info}
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print("❌ 可信基线 guard 失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"✅ 可信基线 guard 通过（变更 {info.get('changed_files', 0)} 个文件，"
          f"安全敏感 {len(info.get('sensitive_files', []))} 个；base 逻辑校验={info.get('base_guard_applied')}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
