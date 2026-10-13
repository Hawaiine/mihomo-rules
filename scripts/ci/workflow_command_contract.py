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

import re
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

# ---------------------------------------------------------------------------
# 精确命令合同（第五轮）
#
# 每个必需命令都必须同时满足两件事，缺一即 fail-closed：
#   A) **精确路径身份**：仓库相对路径逐字一致（`./x` 与 `x` 等价，其它任何差异——
#      同名换目录、绝对路径、`..` 拼接、临时副本——一律不算执行该脚本）；
#   B) **精确参数结构**：按解析后的 token 逐个走位匹配，顺序/取值必须与合同一致，
#      任何多余、未知、重复、缺值参数或可提前成功退出的短路选项（`--help`/`-h`/…）都拒绝。
# ---------------------------------------------------------------------------
GENERAL_GUARD = "scripts/ci/verify_general_pr.py"
GENERAL_CONFIGS = "scripts/verify_configs.py"
GENERAL_RULESETS = "scripts/verify_rulesets.py"
GENERAL_ICON_URLS = "scripts/icon_urls.py"
GENERAL_README = "scripts/readme_stats.py"
POLICY_GUARD = "scripts/ci/verify_workflow_policy.py"
BASELINE_GUARD_PATH = "$RUNNER_TEMP/verify_policy_baseline.py"
BASELINE_GUARD_SRC = "scripts/ci/verify_policy_baseline.py"
BASELINE_BASE_REF = "${PR_BASE_SHA}"
BASELINE_HEAD_REF = "${PR_HEAD_SHA}"
POLICY_JSON = "$RUNNER_TEMP/workflow-policy.json"
BASELINE_JSON = "$RUNNER_TEMP/trusted-baseline.json"

CONTRACT: dict[str, dict[str, Any]] = {
    "general-pr-verify": {
        "required_commands": [
            {"path": GENERAL_GUARD, "argv": {"flags": []}, "env": ["MIHOMO_ICON_REPO"],
             "desc": "通用 PR 只读 guard"},
            {"path": GENERAL_CONFIGS, "argv": {"flags": []}, "env": ["MIHOMO_ICON_REPO"],
             "desc": "config 树校验"},
            {"path": GENERAL_RULESETS, "argv": {"flags": []}, "desc": "ruleset 校验"},
            {"path": GENERAL_ICON_URLS, "argv": {"flags": []}, "desc": "生产图标 URL 可达性"},
            {"path": GENERAL_README, "argv": {"flags": [{"flag": "--check"}]},
             "desc": "README 统计口径"},
            {"exact_tokens": ["python3", "-m", "unittest", "discover", "-s", "scripts/tests"],
             "env": ["MIHOMO_ICON_REPO"], "desc": "全量单元测试"},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": GENERAL_GUARD},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": GENERAL_GUARD},
        ],
        "checkouts": [{"repository": ICONS_REPO, "ref": PINNED_ICONS_SHA, "desc": "固定版本图标仓库"},
                      {"pr_head": True, "desc": "检出 PR head"}],
    },
    "workflow-policy-guard": {
        "required_commands": [
            {"path": POLICY_GUARD, "argv": {"flags": [{"flag": "--json", "values": (POLICY_JSON,)}]},
             "desc": "workflow 策略 guard"},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": POLICY_GUARD},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": POLICY_GUARD},
        ],
        "checkouts": [{"pr_head": True, "desc": "检出 PR head"}],
    },
    "trusted-baseline-guard": {
        "required_commands": [
            {"path": BASELINE_GUARD_PATH,
             "argv": {"flags": [{"flag": "--base-sha", "values": (BASELINE_BASE_REF,)},
                                {"flag": "--head-sha", "values": (BASELINE_HEAD_REF,)},
                                {"flag": "--repo-root", "values": (".",)},
                                {"flag": "--json", "values": (BASELINE_JSON,)}]},
             "provenance": "baseline_guard_copy",
             "desc": "可信基线 guard（候选树校验）"},
        ],
        "required_aux": [
            {"tokens": ["pip", "install", "pyyaml"], "desc": "安装依赖 pyyaml",
             "before": BASELINE_GUARD_PATH},
            {"tokens": ["grep", "-Eq", "^[0-9a-f]{40}$"], "desc": "解析并校验 PR base/head SHA 格式",
             "before": BASELINE_GUARD_PATH},
            {"tokens": ["git", "cat-file", "-e", "${PR_BASE_SHA}^{commit}"],
             "before": BASELINE_GUARD_PATH,
             "desc": "确认 base 提交可用（必须在基线 guard 之前）"},
            {"tokens": ["git", "cat-file", "-e", "${PR_HEAD_SHA}^{commit}"],
             "before": BASELINE_GUARD_PATH,
             "desc": "确认 head 提交可用（必须在基线 guard 之前）"},
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
    depth: int = 0            # shell 块嵌套深度（0 = 顶层）
    top_level: bool = True    # 是否位于顶层（只有顶层命令才算「确实执行」）
    block_header: bool = False  # 该行本身是块头（if/for/while/case/函数定义/`{`/`(`）

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


BLOCK_OPENERS = {"if", "for", "while", "until", "case", "select"}
BLOCK_CLOSERS = {"fi", "done", "esac"}


def _opens_block(tokens: list[str]) -> bool:
    """该行是否是 shell 块的开头（if/for/while/case/函数定义/`{`/`(`）。"""
    if not tokens:
        return False
    first = tokens[0]
    if first in BLOCK_OPENERS or first in ("{", "(", "function"):
        return True
    return "()" in first or (len(tokens) > 1 and tokens[1] == "()")


def _closes_block(tokens: list[str]) -> bool:
    if not tokens:
        return False
    return tokens[0] in BLOCK_CLOSERS or tokens[0] in ("}", ")")


def annotate_structure(lines: list[CommandLine]) -> None:
    """标注每条逻辑命令行的 shell 结构层级（Fix A2）。

    保守口径：命令只要出现在条件分支、循环、case、子 shell、函数体或单行块内，
    就**不算**「在顶层执行」。无法证明执行语义时 fail-closed ——
    这覆盖 `if false; then … fi`、`run_guard() { … }`（未调用）、`case`、`for/while`、
    子 shell `( … )`、单行块 `{ …; }` 等全部「可能不执行」的上下文。
    """
    depth = 0
    for cmd in lines:
        toks = cmd.tokens
        header = _opens_block(toks)
        if _closes_block(toks) and not header:
            depth = max(0, depth - 1)
        cmd.depth = depth
        cmd.block_header = header
        cmd.top_level = (depth == 0 and not header)
        if header:
            depth += 1
            if toks and toks[-1] in BLOCK_CLOSERS | {"}", ")"}:
                depth = max(0, depth - 1)     # 单行块：if x; then y; fi


def _command_word_index(seg: list[str]) -> int | None:
    """命令词在段内的下标（跳过 env/sudo/time 等包装）。"""
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


def interpreter_target(seg: list[str]) -> str | None:
    """解释器**真正执行的目标**（第一个位置参数）；`-m`/`-c` 形态返回 None。

    这样 `python3 -m py_compile x.py`、`python3 -c "print(1)" x.py` 中的
    x.py 不会被误判为「正在执行」——它只是普通参数。
    """
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


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _is_path_token(token: str) -> bool:
    """像脚本路径的 token：含 `/`（或本身就是脚本名）、无空白。"""
    return bool(token) and " " not in token and "\t" not in token


def normalize_path(token: str) -> str:
    """仅去掉 `./` 前缀；**不做** basename 归一、不做 `..` 折叠（差异一律 fail-closed）。"""
    return token[2:] if token.startswith("./") else token


def path_identity_ok(target: str | None, want: str) -> bool:
    """**精确路径身份**（Fix A，第五轮）：逐字一致才算同一个脚本。

    禁止 basename 伪装：`attacker-controlled/verify_general_pr.py`、
    `/tmp/verify_general_pr.py`、`scripts/ci/../ci/x.py`、`$RUNNER_TEMP/…` 之外的副本
    都不是被声明的那个脚本。
    """
    if not target or not _is_path_token(target):
        return False
    return normalize_path(target) == normalize_path(want)


# 可让安全检查「成功但什么都不做」的短路选项：一律拒绝
ARGV_SHORT_CIRCUIT = ("--help", "-h", "--usage", "--version", "-V")


def argv_contract_ok(argv: list[str], spec: dict) -> tuple[bool, str]:
    """**解析后的参数结构**合同（Fix B，第五轮）。

    逐位走位匹配：参数顺序、名称、取值必须与合同一致；任何未声明参数（含 `--help`/`-h`
    等短路选项）、重复参数、缺值、多余位置参数都失败。只检查选项名出现与否是不够的。
    """
    flags = list(spec.get("flags") or [])
    i = fi = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            return False, f"出现未声明的位置参数 {tok!r}"
        if tok in ARGV_SHORT_CIRCUIT:
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


def argv_after_target(cmd: CommandLine, want: str) -> list[str] | None:
    """该行中目标脚本**之后**的参数（解释器或直接执行两种形态）。"""
    seg = cmd.segments[0]
    idx = _command_word_index(seg)
    if idx is None:
        return None
    if path_identity_ok(seg[idx], want):
        return seg[idx + 1:]
    for j in range(idx + 1, len(seg)):
        if path_identity_ok(seg[j], want):
            return seg[j + 1:]
    return None


def invokes_path(cmd: CommandLine, want: str) -> bool:
    """该行是否**真正执行**了精确路径 `want`。

    必须同时满足：
      1. 单一简单命令（无 `|`/`||`/`&&`/`;` 等操作符）且不是 here-doc 正文；
      2. 位于**顶层**（不在 if/for/while/case/子 shell/函数体/单行块内）；
      3. 命令词是解释器或脚本自身，且脚本路径必须是解释器的**第一个位置参数**；
      4. 该路径与合同声明的路径**逐字一致**（Fix A：禁止 basename 伪装）。
    """
    if not cmd.clean or cmd.from_heredoc or not cmd.top_level:
        return False
    seg = cmd.segments[0]
    cw = command_word(seg)
    if cw is None or _basename(cw) in NOOP_COMMANDS:
        return False
    if path_identity_ok(cw, want):
        return True
    if _basename(cw) not in INTERPRETERS:
        return False
    return path_identity_ok(interpreter_target(seg), want)


def command_family_key(tokens: list[str]) -> tuple:
    """命令「家族」标识（命令词 + `-m` 模块名 或 第一个位置参数 basename）。

    用于判断某行是否属于**同一条命令家族**（例如 `python3 -m unittest …`），
    从而只对同家族的行做「token 向量必须逐字一致」的严格判定，避免误伤同 job 内其它命令。
    """
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


def exact_tokens_ok(cmd: CommandLine, tokens: list[str]) -> bool:
    """整条命令的 token 向量必须**逐字相等**（顺序与取值都不许变）。"""
    if not cmd.clean or cmd.from_heredoc or not cmd.top_level:
        return False
    return list(cmd.tokens) == list(tokens)


def matches_tokens(cmd: CommandLine, tokens: list[str], want_cw: str | None = None,
                   require_top_level: bool = False) -> bool:
    """该行（任一简单命令段）是否**真实执行**了指定命令。

    判定：该段的命令词不是空操作命令，且段内**精确包含**全部指定 token
    （精确匹配，不做子串匹配——`echo "pip install pyyaml"` 不算执行）。
    若给出 `want_cw`，还要求该段命令词的 basename 等于它。
    """
    if require_top_level and not cmd.top_level:
        return False
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
            step_lines = split_command_lines(run)
            annotate_structure(step_lines)     # 结构按 step 独立标注，避免跨 step 串扰
            for cmd in step_lines:
                cmd.step = step.get("name")  # type: ignore[attr-defined]
                cmd.step_index = si            # type: ignore[attr-defined]
                cmd.index = len(lines) + 1
                lines.append(cmd)
    details: dict[str, Any] = {"steps": len(steps), "logical_command_lines": len(lines),
                               "required_commands": [], "required_aux": []}

    # 必需命令：必须被**真正执行**，且路径/参数与合同逐字一致
    for req in spec["required_commands"]:
        desc = req.get("desc", "")
        executed: list[CommandLine] = []
        argv_problems: list[str] = []
        nonconforming: list[tuple[CommandLine, str]] = []
        for cmd in lines:
            if "path" in req:
                if not invokes_path(cmd, req["path"]):
                    continue
                ok, why = argv_contract_ok(argv_after_target(cmd, req["path"]) or [],
                                           req.get("argv") or {})
                if ok:
                    executed.append(cmd)
                else:
                    argv_problems.append(f"line {cmd.line_no}: {why}")
                    nonconforming.append((cmd, why))
            elif "exact_tokens" in req:
                if exact_tokens_ok(cmd, req["exact_tokens"]):
                    executed.append(cmd)
                elif cmd.top_level and command_family_key(cmd.tokens) == \
                        command_family_key(req["exact_tokens"]):
                    why = f"token 向量与合同不一致（实际 {' '.join(cmd.tokens)}）"
                    argv_problems.append(f"line {cmd.line_no}: {why}")
                    nonconforming.append((cmd, why))
        entry = {"desc": desc, "path": req.get("path"), "exact_tokens": req.get("exact_tokens"),
                 "argv": req.get("argv"), "executed_lines": [c.line_no for c in executed],
                 "nonconforming_lines": [c.line_no for c, _w in nonconforming],
                 "ok": bool(executed) and not nonconforming}
        if nonconforming:
            # 即使存在一条合合同的调用，任何**额外**的不合合同调用（如 `--help` 短路、附加参数、
            # 重复调用）都必须拒绝：安全命令只允许完全符合声明合同的调用形态。
            problems.append(f"{job_id}: {desc} —— 存在**不合参数合同**的额外调用"
                            f"（line {[c.line_no for c, _w in nonconforming]}）："
                            + "；".join(f"line {c.line_no}: {w}" for c, w in nonconforming[:3]))
        if not executed:
            needle = req.get("path") or " ".join(req.get("exact_tokens") or [])
            _toks = list(req.get("exact_tokens") or [])
            name = (_basename(str(req["path"])) if req.get("path")
                    else (_toks[_toks.index("-m") + 1] if "-m" in _toks else _basename(_toks[-1])))
            present = any(name in c.text for c in lines) or any(
                name in str(s.get("run") or "") for s in steps)
            if argv_problems:
                problems.append(f"{job_id}: {desc} —— **参数合同不符**（脚本路径正确，"
                                f"但参数结构不是声明的合同；`--help` 等短路选项、未知/重复/"
                                f"缺值参数一律拒绝）：" + "；".join(argv_problems[:3]))
            elif present:
                problems.append(f"{job_id}: {desc} —— 脚本/命令名出现但**没有被实际执行或路径身份不符**"
                                f"（echo/注释/引号文本/占位空跑/编译目标或普通参数/条件分支或函数体等"
                                f"非顶层上下文/同名换目录的伪装路径）：{needle}")
            else:
                problems.append(f"{job_id}: 缺少必需命令 —— {desc}（{needle}）")
            details["required_commands"].append(entry)
            continue
        # 基线 guard 的**文件来源与复制步骤**合同（bootstrap 一次性限制如实保留）
        if req.get("provenance") == "baseline_guard_copy":
            prov_problems = check_baseline_guard_provenance(job)
            entry["provenance_ok"] = not prov_problems
            problems.extend(f"{job_id}: {p}" for p in prov_problems)
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
            target = next((c for c in lines if invokes_path(c, aux["before"])), None)
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


def _if_branches(lines: list[CommandLine], start: int) -> tuple[list[CommandLine], list[CommandLine]]:
    """从 `if` 块头开始，返回 (then 分支行, else 分支行)（按深度归属）。"""
    header = lines[start]
    base_depth = header.depth
    then_lines: list[CommandLine] = []
    else_lines: list[CommandLine] = []
    in_else = False
    for cmd in lines[start + 1:]:
        cw = command_word(cmd.tokens)
        if cw == "fi" and cmd.depth <= base_depth:
            break
        if cw == "else" and cmd.depth == base_depth + 1:
            in_else = True
            continue
        if cmd.depth >= base_depth + 1:
            (else_lines if in_else else then_lines).append(cmd)
    return then_lines, else_lines


def _redirect_targets(cmd: CommandLine) -> list[str]:
    """该行重定向（`>`/`>>`）的目标（`_split_segments` 已把操作符去掉，段数 >1 即含重定向）。"""
    if cmd.clean or len(cmd.segments) < 2:
        return []
    return [seg[-1] for seg in cmd.segments[1:] if seg]


def check_baseline_guard_provenance(job: dict) -> list[str]:
    """Fix A1：可信基线 guard 的**来源与复制步骤**合同。

    允许且仅允许：
      1. `if git cat-file -e ${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py …; then`
         作为条件（确认 base 版本存在）；
      2. then 分支：`git show ${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py > $RUNNER_TEMP/…`
         （可信来源 = PR base 提交的文件）；
      3. else 分支：`git show ${PR_HEAD_SHA}:scripts/ci/verify_policy_baseline.py > $RUNNER_TEMP/…`
         （**bootstrap 一次性限制**：base 尚无该 guard 时临时用 head 版本，需在 PR 中声明）；
      4. 执行行：精确路径 + 精确参数（由必需命令合同负责）。
    任何其它写入/复制/下载到该路径的形态一律 fail-closed。
    """
    target = BASELINE_GUARD_PATH
    base_src = f"{BASELINE_BASE_REF}:{BASELINE_GUARD_SRC}"
    head_src = f"{BASELINE_HEAD_REF}:{BASELINE_GUARD_SRC}"
    problems: list[str] = []

    per_step: list[tuple[int, list[CommandLine]]] = []
    for si, step in enumerate(_steps_of(job)):
        run = step.get("run")
        lines = split_command_lines(run) if isinstance(run, str) else []
        annotate_structure(lines)
        per_step.append((si, lines))

    def is_allowed_copy(cmd: CommandLine) -> bool:
        """`git show <base|head>:<src> > <target>` 形态（来源必须是声明的那两个 ref）。"""
        return (command_word(cmd.tokens) == "git" and "show" in cmd.tokens
                and (base_src in cmd.tokens or head_src in cmd.tokens)
                and target in _redirect_targets(cmd))

    found_condition = base_ok = head_ok = False
    for _si, lines in per_step:
        for idx, cmd in enumerate(lines):
            if not (command_word(cmd.tokens) == "if" and base_src in cmd.tokens
                    and "git" in cmd.tokens and "cat-file" in cmd.tokens):
                continue
            found_condition = True
            then_lines, else_lines = _if_branches(lines, idx)
            if any(is_allowed_copy(c) and base_src in c.tokens for c in then_lines):
                base_ok = True
            if any(is_allowed_copy(c) and head_src in c.tokens for c in else_lines):
                head_ok = True

    for si, lines in per_step:
        for cmd in lines:
            if target not in cmd.text:
                continue
            if is_allowed_copy(cmd) or invokes_path(cmd, target):
                continue
            problems.append(f"基线 guard 目标路径出现在未允许的上下文/写入方式："
                            f"step{si} {cmd.text.strip()[:140]!r}")

    if not found_condition:
        problems.append(f"缺少 `git cat-file -e {base_src}` 条件（无法证明复制来源是 PR base 提交）")
    if not base_ok:
        problems.append(f"缺少 then 分支的 `git show {base_src} > {target}`"
                        f"（可信来源必须是 PR base 提交的文件）")
    if not head_ok:
        problems.append(f"缺少 else 分支的 `git show {head_src} > {target}`"
                        f"（bootstrap 一次性回退必须显式声明）")
    return problems


def _is_pr_head_checkout(step: dict) -> bool:
    ref = str((step.get("with") or {}).get("ref") or "")
    return "pull_request.head.sha" in ref or "steps.pr.outputs.head" in ref


def needle_in_run(step: dict, req: dict) -> bool:
    """该 step 的 run 文本是否涉及这条必需命令（仅用于定位 env 归属，不用于「已执行」判定）。"""
    run = str(step.get("run") or "")
    if "path" in req:
        return _basename(str(req["path"])) in run
    if "exact_tokens" in req:
        return " ".join(req["exact_tokens"]) in run
    return all(t in run for t in req.get("tokens") or [])


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


# ============================================================ Fix B：auto-merge 命令合同
AUTOMERGE_JOB_ID = "request-auto-merge"
# 自动合并请求的安全顺序（必须是**真正执行**的独立步骤，路径与参数逐字精确）
AUTOMERGE_REPO = "${GITHUB_REPOSITORY}"
AUTOMERGE_PR = "${{ needs.publish.outputs.pr_number }}"
AUTOMERGE_HEAD = "${{ needs.publish.outputs.head_sha }}"
AUTOMERGE_REQUIRED_STEPS: list[dict[str, Any]] = [
    {"path": "scripts/ci/await_checks.py",
     "argv": {"flags": [{"flag": "--repo", "values": (AUTOMERGE_REPO,)},
                        {"flag": "--pr", "values": (AUTOMERGE_PR,)},
                        {"flag": "--expected-head-sha", "values": (AUTOMERGE_HEAD,)},
                        {"flag": "--base-branch", "values": ("$BASE_BRANCH",)},
                        {"flag": "--timeout", "values": ("900",)},
                        {"flag": "--interval", "values": ("15",)},
                        {"flag": "--max-interval", "values": ("60",)},
                        {"flag": "--json", "values": ("$RUNNER_TEMP/await-checks.json",)}]},
     "desc": "有界等待必需检查完成（只读轮询）"},
    {"path": "scripts/ci/verify_required_checks.py",
     "argv": {"flags": [{"flag": "--repo", "values": (AUTOMERGE_REPO,)},
                        {"flag": "--pr", "values": (AUTOMERGE_PR,)},
                        {"flag": "--head-sha", "values": (AUTOMERGE_HEAD,)},
                        {"flag": "--json", "values": ("$RUNNER_TEMP/required-checks.json",)}]},
     "desc": "严格必需检查验证（来源/身份/状态复核）"},
    {"path": "scripts/ci/request_automerge.py",
     "argv": {"flags": [{"flag": "--repo", "values": (AUTOMERGE_REPO,)},
                        {"flag": "--pr", "values": (AUTOMERGE_PR,)},
                        {"flag": "--head-sha", "values": (AUTOMERGE_HEAD,)},
                        {"flag": "--json", "values": ("$RUNNER_TEMP/automerge.json",)}]},
     "desc": "请求 auto-merge（唯一允许的 mutation 路径）"},
]
AUTOMERGE_ENV = "GITHUB_TOKEN"

# 直接触发 auto-merge / 合并的形态：workflow 内**一律**禁止（只能由 helper 执行 mutation）。
# 注意：这里只认「写」形态（GraphQL mutation 名 / REST merge 端点 / gh pr merge），
# 不把 PR 对象的只读字段（如 REST 的 auto_merge、auto_merge_gate）当作违规。
DIRECT_MUTATION_MARKERS = ("enablePullRequestAutoMerge", "disablePullRequestAutoMerge")
DIRECT_MUTATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgh\s+pr\s+merge\b"), "gh pr merge（绕过 helper 的合并路径）"),
    (re.compile(r"\bgh\s+api\b[^\n]*\bgraphql\b"), "gh api graphql（workflow 内直接 GraphQL）"),
    (re.compile(r"\bgh\s+api\b[^\n]*(-X|--method)\s*(PUT|POST|PATCH|DELETE)[^\n]*\bmerge\b"),
     "gh api 写请求命中 merge"),
    (re.compile(r"/(?:repos/[^\s\"\']+/)?pulls/[^\s\"\']*/merge\b"), "REST merge 端点"),
    (re.compile(r"\b(?:curl|wget|http|httpie)\b[^\n]*\bgraphql\b"), "HTTP 客户端直接发 GraphQL"),
)


def _steps_of(job: Any) -> list[dict]:
    return [s for s in ((job or {}).get("steps") or []) if isinstance(s, dict)]


def direct_mutation_problems(doc: dict, name: str = "") -> list[str]:
    """禁止任何 workflow 自己直接发送 auto-merge / 合并 mutation（Fix B3）。"""
    problems: list[str] = []
    for jid, job in (doc.get("jobs") or {}).items():
        for step in _steps_of(job):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            for cmd in split_command_lines(run):
                text = cmd.text
                marks = [m for m in DIRECT_MUTATION_MARKERS if m in text]
                if marks:
                    problems.append(
                        f"{name}: job '{jid}' step={step.get('name')!r} 出现直接 auto-merge mutation 标记 "
                        f"{marks} —— mutation 只能由 scripts/ci/request_automerge.py 执行")
                    continue
                for pattern, why in DIRECT_MUTATION_PATTERNS:
                    if pattern.search(text):
                        problems.append(f"{name}: job '{jid}' step={step.get('name')!r} 命中直接合并路径"
                                        f"（{why}）：{text.strip()[:120]!r}")
    return problems


def check_automerge_job(doc: dict, job_id: str = AUTOMERGE_JOB_ID) -> tuple[list[str], dict]:
    """Fix B：auto-merge 前置命令必须**真正执行**、顺序正确、且不得绕过 helper。"""
    problems: list[str] = []
    details: dict[str, Any] = {"job_id": job_id}
    job = (doc.get("jobs") or {}).get(job_id)
    if not isinstance(job, dict):
        return [f"{job_id}: 缺少 auto-merge job（无法验证前置命令合同）"], details
    steps = _steps_of(job)
    lines: list[CommandLine] = []
    for si, step in enumerate(steps):
        run = step.get("run")
        if isinstance(run, str):
            step_lines = split_command_lines(run)
            annotate_structure(step_lines)
            for cmd in step_lines:
                cmd.step = step.get("name")          # type: ignore[attr-defined]
                cmd.step_index = si                  # type: ignore[attr-defined]
                cmd.index = len(lines) + 1
                lines.append(cmd)
    found: list[dict] = []
    for want in AUTOMERGE_REQUIRED_STEPS:
        label = want["path"]
        name = _basename(label)
        hits: list[CommandLine] = []
        argv_problems: list[str] = []
        nonconforming: list[tuple[CommandLine, str]] = []
        for c in lines:
            if not invokes_path(c, label):
                continue
            ok, why = argv_contract_ok(argv_after_target(c, label) or [], want.get("argv") or {})
            if ok:
                hits.append(c)
            else:
                argv_problems.append(f"line {c.line_no}: {why}")
                nonconforming.append((c, why))
        if nonconforming:
            problems.append(f"{job_id}: {want['desc']} —— 存在**不合参数合同**的额外调用"
                            f"（line {[c.line_no for c, _w in nonconforming]}）："
                            + "；".join(f"line {c.line_no}: {w}" for c, w in nonconforming[:3]))
        if not hits:
            present = any(name in c.text for c in lines) or any(
                name in str(s.get("run") or "") for s in steps)
            if argv_problems:
                problems.append(f"{job_id}: {want['desc']} —— **参数合同不符**（路径正确但参数结构"
                                f"不是声明的合同；短路/未知/重复/缺值参数一律拒绝）："
                                + "；".join(argv_problems[:3]))
            elif present:
                problems.append(f"{job_id}: {want['desc']} —— {name} 出现但**没有被实际执行或路径"
                                f"身份不符**（echo/注释/引号文本/非顶层上下文/同名换目录的伪装路径）")
            else:
                problems.append(f"{job_id}: 缺少必需命令 —— {want['desc']}（{label}）")
            continue
        hit = hits[0]
        env = steps[hit.step_index].get("env") or {}
        if AUTOMERGE_ENV not in env:
            problems.append(f"{job_id}: {want['desc']} 所在步骤必须声明 env {AUTOMERGE_ENV}")
        found.append({"path": label, "step_index": hit.step_index, "line_no": hit.line_no,
                      "step_name": steps[hit.step_index].get("name"), "ok": True})
    if len(found) == len(AUTOMERGE_REQUIRED_STEPS):
        order = [f["step_index"] for f in found]
        if len(set(order)) != len(order) or order != sorted(order):
            problems.append(f"{job_id}: 三个前置命令必须按 await_checks → verify_required_checks → "
                            f"request_automerge 的顺序位于**独立步骤**中（实际 step={order}）")
    details["required_steps"] = found
    return problems, details
