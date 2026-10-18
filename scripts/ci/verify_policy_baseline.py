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
_WRITE_PATTERNS = tuple(re.compile(p) for p in (
    r"git\s+push", r"git\s+commit", r"/git/refs", r"/statuses/", r"/pulls"))


def _has_workflow_writes(text: str) -> bool:
    """workflow 文本里是否出现任何写操作（注释行不计）。"""
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return any(pattern.search(body) for pattern in _WRITE_PATTERNS)
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
# 精确命令合同（第五轮，**独立实现**）：精确路径身份 + 精确参数结构。
# 与 scripts/ci/workflow_command_contract.py 语义一致，但这里不 import 任何候选树代码。
_B_GENERAL_GUARD = "scripts/ci/verify_general_pr.py"
_B_GENERAL_CONFIGS = "scripts/verify_configs.py"
_B_GENERAL_RULESETS = "scripts/verify_rulesets.py"
_B_GENERAL_ICON_URLS = "scripts/icon_urls.py"
_B_GENERAL_README = "scripts/readme_stats.py"
_B_POLICY_GUARD = "scripts/ci/verify_workflow_policy.py"
_B_BASELINE_PATH = "$RUNNER_TEMP/verify_policy_baseline.py"
_B_BASELINE_SRC = "scripts/ci/verify_policy_baseline.py"
_B_BASELINE_BASE_REF = "${PR_BASE_SHA}"
_B_BASELINE_HEAD_REF = "${PR_HEAD_SHA}"

# ---------------------------------------------------------------------------
# 第六轮（独立实现）：命令遮蔽（shadowing）与真实可执行文件身份
# ---------------------------------------------------------------------------
# 与候选树合同**互不共享代码**：这里用自己的一套正则/解析，避免「候选改自己的守卫就自证安全」。
_B_VERIFIED_PY = "$PY_BIN"
_B_VERIFIED_GIT = "$GIT_BIN"
_B_GUARDED = frozenset({
    "python", "python3", "pip", "pip3", "bash", "sh", "dash", "zsh", "node", "ruby", "perl",
    "git", "grep", "cat", "test", "[", "printf", "readlink", "cp", "mv", "ln", "install",
    "chmod", "curl", "wget",
})
_B_SHADOW_FUNC = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)")
_B_SHADOW_FUNC_KW = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)")
_B_SHADOW_ALIAS = re.compile(r"^\s*alias\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")
_B_SHADOW_EXPORT_F = re.compile(r"\b(?:export|declare|typeset)\s+-[a-zA-Z]*f[a-zA-Z]*\s+"
                                r"([A-Za-z_][A-Za-z0-9_]*)")
_B_SHADOW_VARS = {
    "BASH_ENV": re.compile(r"(?:^|[\s\"'=])BASH_ENV="),
    "ENV": re.compile(r"(?:^|[\s\"'=])ENV="),
    "PATH": re.compile(r"(?:^|[\s\"'=])PATH="),
}
_B_REDIRECT = re.compile(r">>?\s*[\"']?([^\s\"';|&<>]+)")
_B_WRITE_CMDS = frozenset({"cp", "mv", "ln", "install", "touch", "chmod", "dd", "tar", "unzip"})
_B_DOWNLOAD_CMDS = frozenset({"curl", "wget"})
_B_READ_ONLY_TARGET = frozenset({"test", "[", "grep", "cat", "head", "tail", "wc", "readlink",
                                 "sha256sum", "shasum", "cmp", "diff", "stat", "file", "ls"})
# 运行时「可执行文件身份核验」步骤必须包含的标记（与候选树合同语义一致，但独立维护）
_B_RESOLVER_MARKERS = (
    "unset -f python3", "unset BASH_ENV", 'command -v -- "$name"', "/usr/*|/bin/*|/opt/*",
    'test -x "$path"', 'PY_BIN="$(_resolve python3)"', 'GIT_BIN="$(_resolve git)"',
    '"$PY_BIN" -c', '"$GIT_BIN" --version', 'echo "PY_BIN=$PY_BIN"',
    'echo "GIT_BIN=$GIT_BIN"', '>> "$GITHUB_ENV"',
)
_B_POLICY_REPORT_MARKERS = ('test -s "$RUNNER_TEMP/workflow-policy.json"', "json.load", '"ok"')
_B_BASELINE_CONTENT_MARKERS = ('test -s "$RUNNER_TEMP/verify_policy_baseline.py"', "hash-object",
                               "rev-parse", "grep -q", "def main(", "--base-sha")
_B_BASELINE_REPORT_MARKERS = ('test -s "$RUNNER_TEMP/trusted-baseline.json"', "json.load",
                              '"ok"', "base_sha", "head_sha")

REQUIRED_JOB_COMMANDS: dict[str, dict] = {
    "general-pr-verify": {
        "required_commands": [
            {"path": _B_GENERAL_GUARD, "interpreter": _B_VERIFIED_PY, "argv": {"flags": []}},
            {"path": _B_GENERAL_CONFIGS, "interpreter": _B_VERIFIED_PY, "argv": {"flags": []}},
            {"path": _B_GENERAL_RULESETS, "interpreter": _B_VERIFIED_PY, "argv": {"flags": []}},
            {"path": _B_GENERAL_ICON_URLS, "interpreter": _B_VERIFIED_PY, "argv": {"flags": []}},
            {"path": _B_GENERAL_README, "interpreter": _B_VERIFIED_PY, "argv": {"flags": [{"flag": "--check"}]}},
            {"exact_tokens": [_B_VERIFIED_PY, "-m", "unittest", "discover", "-s", "scripts/tests"]},
        ],
        "required_aux": [
            {"tokens": [_B_VERIFIED_PY, "-m", "pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": _B_GENERAL_GUARD},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": _B_GENERAL_GUARD},
        ],
    },
    "workflow-policy-guard": {
        "required_commands": [
            {"path": _B_POLICY_GUARD, "interpreter": _B_VERIFIED_PY,
             "argv": {"flags": [{"flag": "--json", "values": ("$RUNNER_TEMP/workflow-policy.json",)}]}},
        ],
        "required_aux": [
            {"tokens": [_B_VERIFIED_PY, "-m", "pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": _B_POLICY_GUARD},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": _B_POLICY_GUARD},
        ],
        "report_markers": _B_POLICY_REPORT_MARKERS,
    },
    "trusted-baseline-guard": {
        "required_commands": [
            {"path": _B_BASELINE_PATH, "interpreter": _B_VERIFIED_PY,
             "argv": {"flags": [{"flag": "--base-sha", "values": (_B_BASELINE_BASE_REF,)},
                                {"flag": "--head-sha", "values": (_B_BASELINE_HEAD_REF,)},
                                {"flag": "--repo-root", "values": (".",)},
                                {"flag": "--json", "values": ("$RUNNER_TEMP/trusted-baseline.json",)}]},
             "provenance": "baseline_guard_copy"},
        ],
        "required_aux": [
            {"tokens": [_B_VERIFIED_PY, "-m", "pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": _B_BASELINE_PATH},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": _B_BASELINE_PATH},
            {"tokens": [_B_VERIFIED_GIT, "cat-file", "-e", "${PR_BASE_SHA}^{commit}"],
             "desc": "base 提交可用性检查", "before": _B_BASELINE_PATH},
            {"tokens": [_B_VERIFIED_GIT, "cat-file", "-e", "${PR_HEAD_SHA}^{commit}"],
             "desc": "head 提交可用性检查", "before": _B_BASELINE_PATH},
        ],
        "report_markers": _B_BASELINE_REPORT_MARKERS,
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


def _normalize_path(token: str) -> str:
    """仅去掉 `./` 前缀；不做 basename 归一、不做 `..` 折叠（差异一律 fail-closed）。"""
    return token[2:] if token.startswith("./") else token


def _path_identity_ok(target: str | None, want: str) -> bool:
    """**精确路径身份**（Fix A，第五轮）：逐字一致才算同一个脚本。"""
    if not target or " " in target or "\t" in target:
        return False
    return _normalize_path(target) == _normalize_path(want)


_B_ARGV_SHORT_CIRCUIT = ("--help", "-h", "--usage", "--version", "-V")


def _argv_contract_ok(argv: list[str], spec: dict) -> tuple[bool, str]:
    """**解析后的参数结构**合同（Fix B，第五轮）：逐位走位匹配，拒绝一切多余/未知/重复/
    缺值参数与短路选项（`--help` 等）。"""
    flags = list(spec.get("flags") or [])
    i = fi = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            return False, f"出现未声明的位置参数 {tok!r}"
        if tok in _B_ARGV_SHORT_CIRCUIT:
            return False, f"出现可提前成功退出的短路选项 {tok!r}"
        if fi >= len(flags):
            return False, f"出现未声明的参数 {tok!r}"
        name = flags[fi]["flag"]
        if tok != name:
            return False, f"参数名称/顺序不符：期望 {name!r}，实际 {tok!r}"
        fi += 1
        i += 1
        values = flags[fi - 1].get("values")
        if values is None:
            continue
        if i >= len(argv):
            return False, f"{name} 缺少参数值"
        if argv[i] not in tuple(values):
            return False, f"{name} 的取值不在允许集合内：{argv[i]!r}（允许 {list(values)}）"
        i += 1
    if fi != len(flags):
        return False, "缺少必需参数：" + " ".join(f["flag"] for f in flags[fi:])
    return True, ""


def _argv_after_target(entry: dict, want: str) -> list[str] | None:
    """该命令中目标脚本**之后**的参数（解释器或直接执行两种形态）。"""
    seg = list(entry.get("seg_tokens") or [])
    idx = _command_word_index(seg)
    if idx is None:
        return None
    if _path_identity_ok(seg[idx], want):
        return seg[idx + 1:]
    for j in range(idx + 1, len(seg)):
        if _path_identity_ok(seg[j], want):
            return seg[j + 1:]
    return None


def _invokes(entry: dict, want: str) -> bool:
    """Fix A1/A2 + 第五轮 A：必须**顶层**单一简单命令，脚本是解释器的第一个位置参数，
    且路径与合同**逐字一致**（禁止 basename 伪装/同名换目录）。
    """
    if not entry["clean"] or not entry.get("top_level"):
        return False
    cw = entry["cw"]
    if _path_identity_ok(cw, want):
        return True
    if _basename(cw) not in INTERPRETERS and cw != _B_VERIFIED_PY:
        return False
    return _path_identity_ok(_interpreter_target(entry.get("seg_tokens") or []), want)


def _family_key(tokens: list[str]) -> tuple:
    """命令「家族」标识（命令词 + `-m` 模块名 或 第一个位置参数 basename）。"""
    if not tokens:
        return ()
    cw = _basename(tokens[0])
    if "-m" in tokens[:3]:
        i = tokens.index("-m")
        return (cw, "-m", tokens[i + 1] if i + 1 < len(tokens) else "")
    for tok in tokens[1:]:
        if not tok.startswith("-"):
            return (cw, _basename(tok))
    return (cw,)


def _if_branches(lines: list[dict], start: int) -> tuple[list[dict], list[dict]]:
    """从 `if` 块头开始，按深度返回 (then 分支, else 分支) 的行。"""
    base_depth = lines[start]["depth"]
    then_lines: list[dict] = []
    else_lines: list[dict] = []
    in_else = False
    for ann in lines[start + 1:]:
        cw = ann["tokens"][0] if ann["tokens"] else ""
        if cw == "fi" and ann["depth"] <= base_depth:
            break
        if cw == "else" and ann["depth"] == base_depth + 1:
            in_else = True
            continue
        if ann["depth"] >= base_depth + 1:
            (else_lines if in_else else then_lines).append(ann)
    return then_lines, else_lines


def _redirect_targets(ann: dict) -> list[str]:
    """该行重定向（`>`/`>>`）目标。"""
    segs = _segments(ann["text"])
    if len(segs) < 2:
        return []
    return [seg[-1] for seg in segs[1:] if seg]


def check_baseline_guard_provenance(doc: dict) -> list[str]:
    """Fix A1（独立实现）：可信基线 guard 的**来源与复制步骤**合同。

    允许且仅允许：`if git cat-file -e ${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py` 条件；
    then 分支从 `${PR_BASE_SHA}` 取用（可信来源）；else 分支的 `${PR_HEAD_SHA}` 回退是
    **bootstrap 一次性限制**（必须显式声明）；执行行必须精确路径 + 精确参数。
    任何其它写入/复制/下载到该路径的形态一律 fail-closed。
    """
    target = _B_BASELINE_PATH
    base_src = f"{_B_BASELINE_BASE_REF}:{_B_BASELINE_SRC}"
    head_src = f"{_B_BASELINE_HEAD_REF}:{_B_BASELINE_SRC}"
    problems: list[str] = []
    job = (doc.get("jobs") or {}).get("trusted-baseline-guard")
    if not isinstance(job, dict):
        return [f"{PR_VERIFY}: required job 'trusted-baseline-guard' missing"]

    per_step: list[tuple[int, list[dict]]] = []
    for si, step in enumerate((job.get("steps") or [])):
        run = step.get("run") if isinstance(step, dict) else None
        per_step.append((si, _annotate(_logical_lines(run)) if isinstance(run, str) else []))

    def is_allowed_copy(ann: dict) -> bool:
        toks = ann["tokens"]
        cw = toks[0] if toks else ""
        return (cw in ("git", _B_VERIFIED_GIT) and "show" in toks
                and (base_src in toks or head_src in toks)
                and target in _redirect_targets(ann))

    def is_allowed_verify(ann: dict) -> bool:
        """第六轮：对取得的文件做**只读**核验的允许形态（单段、无重定向，写不进去）。"""
        toks = ann["tokens"]
        if not toks:
            return False
        cw = _command_word(toks) or ""
        if cw in ("git", _B_VERIFIED_GIT):
            if "hash-object" in toks and target in toks:
                return True
            if "rev-parse" in toks and (base_src in toks or head_src in toks):
                return True
        segs = _segments(ann["text"])
        if len(segs) == 1 and _basename(cw) in _B_READ_ONLY_TARGET and target in ann["text"]:
            return True
        return False

    found_condition = base_ok = head_ok = False
    for _si, lines in per_step:
        for idx, ann in enumerate(lines):
            toks = ann["tokens"]
            if not (toks and toks[0] == "if" and base_src in toks
                    and ("git" in toks or _B_VERIFIED_GIT in toks) and "cat-file" in toks):
                continue
            found_condition = True
            then_lines, else_lines = _if_branches(lines, idx)
            if any(is_allowed_copy(a) and base_src in a["tokens"] for a in then_lines):
                base_ok = True
            if any(is_allowed_copy(a) and head_src in a["tokens"] for a in else_lines):
                head_ok = True

    for si, lines in per_step:
        for ann in lines:
            if target not in ann["text"]:
                continue
            toks = ann["tokens"]
            if is_allowed_copy(ann):
                continue
            if is_allowed_verify(ann):
                continue
            segs = _segments(ann["text"])
            if len(segs) == 1 and _path_identity_ok(_interpreter_target(segs[0]) or "", target):
                continue                                  # 执行行由必需命令合同校验
            problems.append(f"基线 guard 目标路径出现在未允许的上下文/写入方式："
                            f"step{si} {ann['text'].strip()[:140]!r}")
    if not found_condition:
        problems.append(f"缺少 `git cat-file -e {base_src}` 条件（无法证明复制来源是 PR base 提交）")
    if not base_ok:
        problems.append(f"缺少 then 分支的 `git show {base_src} > {target}`（可信来源必须是 PR base 提交）")
    if not head_ok:
        problems.append(f"缺少 else 分支的 `git show {head_src} > {target}`"
                        f"（bootstrap 一次性回退必须显式声明）")
    # 第六轮：取得的文件与 JSON 报告必须被**真实核验**（非空 / 与来源 blob 一致 / 关键结构 / 报告字段）
    step_text = " ".join(str(s.get("run") or "") for s in ((job.get("steps") or [])))
    for label, markers in (("基线 guard 文件内容核验", _B_BASELINE_CONTENT_MARKERS),
                           ("基线 JSON 报告核验", _B_BASELINE_REPORT_MARKERS)):
        missing = [m for m in markers if m not in step_text]
        if missing:
            problems.append(f"缺少{label}（存在/非空/与来源 blob 一致/关键结构/报告字段）："
                            f"缺失标记 {missing[:4]}")
    return problems


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
            hits: list[dict] = []
            argv_problems: list[str] = []
            nonconforming: list[str] = []
            for e in indexed:
                if "path" in want:
                    if not _invokes(e, want["path"]):
                        continue
                    if want.get("interpreter") and e["cw"] != want["interpreter"]:
                        why = (f"必须由核验过的可执行文件 {want['interpreter']} 调用"
                               f"（实际 {e['cw']!r}）—— 裸命令名可被 shell 函数/PATH 伪装劫持")
                        argv_problems.append(f"line {e['line']}: {why}")
                        nonconforming.append(f"line {e['line']}: {why}")
                        continue
                    ok_argv, why = _argv_contract_ok(_argv_after_target(e, want["path"]) or [],
                                                     want.get("argv") or {})
                    if ok_argv:
                        hits.append(e)
                    else:
                        argv_problems.append(f"line {e['line']}: {why}")
                        nonconforming.append(f"line {e['line']}: {why}")
                elif "exact_tokens" in want:
                    toks = [e["cw"]] + e["args"]
                    if e["clean"] and e.get("top_level") and toks == list(want["exact_tokens"]):
                        hits.append(e)
                    elif e.get("top_level") and _family_key(toks) == _family_key(want["exact_tokens"]):
                        why = f"token 向量与合同不一致（实际 {' '.join(toks)}）"
                        argv_problems.append(f"line {e['line']}: {why}")
                        nonconforming.append(f"line {e['line']}: {why}")
            label = want.get("path") or " ".join(want.get("exact_tokens") or [])
            toks = list(want.get("exact_tokens") or [])
            name = (_basename(str(want["path"])) if want.get("path")
                    else (toks[toks.index("-m") + 1] if "-m" in toks else _basename(toks[-1])))
            if nonconforming:
                problems.append(f"{PR_VERIFY}: job {job_id!r} —— {name} 存在**不合参数合同**的额外调用"
                                f"（短路/未知/重复/缺值参数一律拒绝）：" + "；".join(nonconforming[:3]))
            if not hits:
                if argv_problems:
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— {name} **参数合同不符**："
                                    + "；".join(argv_problems[:3]))
                elif name in text:
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— {name} 只出现脚本名"
                                    f"但**没有被真正执行或路径身份不符**（echo/注释/引号文本/here-doc/"
                                    f"条件分支/|| true 等空跑、执行语义绕过、同名换目录的伪装路径）")
                else:
                    problems.append(f"{PR_VERIFY}: job {job_id!r} —— 缺少必需命令 {label}")
            elif want.get("provenance") == "baseline_guard_copy":
                problems.extend(f"{PR_VERIFY}: job {job_id!r} —— {p}"
                                for p in check_baseline_guard_provenance(doc))
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
        # 第六轮：守卫必须真实产出可解析的 JSON 报告（不得只看 exit code）
        steps = [s for s in ((job.get("steps") or [])) if isinstance(s, dict)]
        step_text = " ".join(str(s.get("run") or "") for s in steps)
        for marker in tuple(spec.get("report_markers") or ()):
            if marker not in step_text:
                problems.append(f"{PR_VERIFY}: job {job_id!r} —— 缺少产物核验标记 {marker!r}"
                                f"（守卫必须真实生成 JSON 报告并被核验，不得只以 exit code 判定成功）")
    return problems


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


# ---------------------------------------------------- Fix B（独立实现）：auto-merge 合同
AUTOMERGE_JOB_ID = "request-auto-merge"
AUTOMERGE_ENV = "GITHUB_TOKEN"
_B_REPO = "${GITHUB_REPOSITORY}"
_B_PR = "${{ needs.publish.outputs.pr_number }}"
_B_HEAD = "${{ needs.publish.outputs.head_sha }}"
AUTOMERGE_REQUIRED_STEPS: list[dict] = [
    {"path": "scripts/ci/await_checks.py",
     "argv": {"flags": [{"flag": "--repo", "values": (_B_REPO,)},
                        {"flag": "--pr", "values": (_B_PR,)},
                        {"flag": "--expected-head-sha", "values": (_B_HEAD,)},
                        {"flag": "--base-branch", "values": ("$BASE_BRANCH",)},
                        {"flag": "--timeout", "values": ("900",)},
                        {"flag": "--interval", "values": ("15",)},
                        {"flag": "--max-interval", "values": ("60",)},
                        {"flag": "--json", "values": ("$RUNNER_TEMP/await-checks.json",)}]},
     "desc": "有界等待必需检查完成（只读轮询）"},
    {"path": "scripts/ci/verify_required_checks.py",
     "argv": {"flags": [{"flag": "--repo", "values": (_B_REPO,)},
                        {"flag": "--pr", "values": (_B_PR,)},
                        {"flag": "--head-sha", "values": (_B_HEAD,)},
                        {"flag": "--json", "values": ("$RUNNER_TEMP/required-checks.json",)}]},
     "desc": "严格必需检查验证（来源/身份/状态复核）"},
    {"path": "scripts/ci/request_automerge.py",
     "argv": {"flags": [{"flag": "--repo", "values": (_B_REPO,)},
                        {"flag": "--pr", "values": (_B_PR,)},
                        {"flag": "--head-sha", "values": (_B_HEAD,)},
                        {"flag": "--json", "values": ("$RUNNER_TEMP/automerge.json",)}]},
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


def _shadowing_problems(doc: dict, name: str) -> list[str]:
    """第六轮（独立实现）：命令遮蔽 / 启动环境注入 / PATH 伪装 / 同名伪造可执行文件。

    与候选树合同不共享任何代码：这里只对**逻辑命令行文本**做判定。
    """
    problems: list[str] = []
    for jid, job in (doc.get("jobs") or {}).items():
        for step in ((job or {}).get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for text in _logical_lines(run):
                where = f"{name}: job {jid!r} step={step.get('name')!r}"
                match = _B_SHADOW_FUNC.match(text) or _B_SHADOW_FUNC_KW.match(text)
                if match and match.group(1) in _B_GUARDED:
                    problems.append(f"{where} defines/overrides a shell function {match.group(1)!r} "
                                    f"(command shadowing: guard invocation would resolve to the fake)")
                    continue
                match = _B_SHADOW_ALIAS.match(text)
                if match and match.group(1) in _B_GUARDED:
                    problems.append(f"{where} aliases protected command {match.group(1)!r}")
                    continue
                match = _B_SHADOW_EXPORT_F.search(text)
                if match and match.group(1) in _B_GUARDED:
                    problems.append(f"{where} exports a function {match.group(1)!r} "
                                    f"(export -f propagates the fake to child shells)")
                    continue
                for var, pattern in _B_SHADOW_VARS.items():
                    if pattern.search(text):
                        problems.append(f"{where} assigns/rewrites {var} "
                                        f"(startup file or search path controlled by candidate content)")
                        break
                hit = None
                for match in _B_REDIRECT.finditer(text):
                    if match.group(1).rsplit("/", 1)[-1] in _B_GUARDED:
                        hit = match.group(1)
                        break
                toks = [t.strip("\"'") for t in text.split()]
                cw = toks[0].rsplit("/", 1)[-1] if toks else ""
                if hit is None and cw in _B_WRITE_CMDS:
                    for tok in toks[1:]:
                        if tok.startswith("-"):
                            continue
                        if tok.rsplit("/", 1)[-1] in _B_GUARDED:
                            hit = tok
                            break
                if hit is None and cw in _B_DOWNLOAD_CMDS:
                    for tok in toks[1:]:
                        if tok.startswith("-"):
                            continue
                        if "/" in tok and tok.rsplit("/", 1)[-1] in _B_GUARDED:
                            hit = tok
                            break
                if hit:
                    problems.append(f"{where} writes/creates a file named like a protected command "
                                    f"{hit!r} (PATH fake executable)")
    return problems


def _exec_identity_problems(doc: dict) -> list[str]:
    """第六轮（独立实现）：必需 job 必须先用**核验过的可执行文件身份**，
    并核验守卫真实产出的 JSON 报告。"""
    problems: list[str] = []
    jobs = doc.get("jobs") or {}
    for job_id, spec in REQUIRED_JOB_COMMANDS.items():
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            continue
        steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
        texts = [str(s.get("run") or "") for s in steps]
        found = [i for i, t in enumerate(texts) if all(m in t for m in _B_RESOLVER_MARKERS)]
        if not found:
            problems.append(f"{PR_VERIFY}: job {job_id!r} —— 缺少「可执行文件身份核验」步骤"
                            f"（unset -f / unset BASH_ENV / 系统根目录绝对路径 / test -x / "
                            f"真实运行核验 / $GITHUB_ENV 导出）")
        else:
            for i, text_ in enumerate(texts):
                if i in found:
                    continue
                for var in ("PY_BIN", "GIT_BIN"):
                    if re.search(rf"(?:^|[\s\"']){var}=", text_, re.MULTILINE):
                        problems.append(f"{PR_VERIFY}: job {job_id!r} —— {var} 只允许由核验步骤"
                                        f"（step {sorted(found)}）赋值，step {i} 也对其赋值")
        indexed = executed_commands_indexed({"jobs": {job_id: job}})
        for want in spec["required_commands"]:
            if "path" not in want:
                continue
            hits = [e for e in indexed if _invokes(e, want["path"])]
            if not hits:
                continue
            invocation_steps = {e["step"] for e in hits}
            if found and min(found) >= min(invocation_steps):
                problems.append(f"{PR_VERIFY}: job {job_id!r} —— 可执行文件身份核验步骤必须位于"
                                f"守卫调用之前（resolver step {sorted(found)} ≥ 调用 step "
                                f"{sorted(invocation_steps)}）")
        for marker in tuple(spec.get("report_markers") or ()):
            if not any(marker in t for t in texts):
                problems.append(f"{PR_VERIFY}: job {job_id!r} —— 缺少产物核验标记 {marker!r}")
    return problems


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
        label = want["path"]
        name = _basename(label)
        hits: list[dict] = []
        argv_problems: list[str] = []
        nonconforming: list[str] = []
        for e in indexed:
            if not _invokes(e, label):
                continue
            ok_argv, why = _argv_contract_ok(_argv_after_target(e, label) or [], want.get("argv") or {})
            if ok_argv:
                hits.append(e)
            else:
                argv_problems.append(f"line {e['line']}: {why}")
                nonconforming.append(f"line {e['line']}: {why}")
        if nonconforming:
            problems.append(f"{CANDIDATE_SYNC}: {want['desc']} —— {name} 存在**不合参数合同**的额外调用"
                            f"（短路/未知/重复/缺值参数一律拒绝）：" + "；".join(nonconforming[:3]))
        if not hits:
            present = any(name in str(s.get("run") or "") for s in steps)
            if argv_problems:
                problems.append(f"{CANDIDATE_SYNC}: {want['desc']} —— {name} **参数合同不符**："
                                + "；".join(argv_problems[:3]))
            else:
                problems.append(f"{CANDIDATE_SYNC}: {want['desc']} —— {name} "
                                + ("出现但**没有被实际执行或路径身份不符**（echo/注释/引号文本/"
                                   "非顶层上下文/同名换目录的伪装路径）"
                                   if present else f"缺少必需命令（{label}）"))
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
            # Route A：daily-sync 只允许朝「零写入委派」方向变化。
            #   朝写方向（重新出现 push/commit/refs/status/PR 写入）→ 视为重新武装，失败；
            #   去写方向（改成 workflow_call 委派）→ 允许，且由策略 guard 要求零写操作。
            if _has_workflow_writes(after):
                problems.append(f"{path}: legacy direct-write workflow must not gain write operations "
                                f"(re-arming forbidden)")
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
        # 第六轮：命令遮蔽 / 启动环境注入 / PATH 伪装 / 同名伪造可执行文件（独立实现）
        problems.extend(_shadowing_problems(wf_doc, wf))
        if wf == CANDIDATE_SYNC:
            problems.extend(check_automerge_contract(wf_doc))

    # guard 调用不得被移除（同时由 check_command_contract 做「真正执行」判定）
    if head_prv is not None:
        for script in ("scripts/ci/verify_general_pr.py", BASE_GUARD):
            if script not in head_prv:
                problems.append(f"{PR_VERIFY}: guard invocation {script} missing")
        # Fix B：候选 pr-verify.yml 的必要命令必须**真正执行**
        # （本 guard 自带独立严格解析，不 import 候选树代码，避免候选自证安全）
        head_prv_doc = yaml.safe_load(head_prv) or {}
        problems.extend(check_command_contract(head_prv_doc))
        # 第六轮：可执行文件身份核验步骤与产物核验（独立实现）
        problems.extend(_exec_identity_problems(head_prv_doc))
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
