"""Coding verification evidence ledger: records what the agent actually proved in
a code workspace. Deliberately passive — it never runs a suite, never blocks
completion, and never upgrades targeted checks into "repo green"."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from agent.terminal_writers import (
    command_words, conditional_write, executable_name, interpreter_kind, node_verifier_writes,
)
from hermes_constants import get_hermes_home


_DB_LOCK = threading.Lock()
_MAX_OUTPUT_SUMMARY_CHARS = 2000
_MAX_EVIDENCE_AGE_DAYS = 30
_MAX_EVENTS_PER_SESSION_ROOT = 100
_MAX_TOTAL_UNREFERENCED_EVENTS = 10_000
_AD_HOC_SCRIPT_NAME_PREFIXES = ("hermes-verify-", "hermes-ad-hoc-")
_VERIFY_SCHEMA_VERSION = 1

_INTERPRETERS = {"python", "python3", "node", "bash", "sh", "ruby", "perl"}
_TARGET_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java")
_TARGET_PREFIXES = ("test_", "tests", "spec", "__tests__")
# Ordered: first matching keyword group wins; "check" only counts when the
# command is not itself a test command; anything else is a test.
_KIND_KEYWORDS = (
    (("lint", "eslint", "ruff"), "lint"),
    (("typecheck", "tsc", "mypy", "pyright", "ty"), "typecheck"), (("build",), "build"),
    (("fmt", "format"), "format"),
)
_PYTEST_SPELLINGS = (
    ["python", "-m", "pytest"], ["python3", "-m", "pytest"],
    ["uv", "run", "pytest"], ["poetry", "run", "pytest"], ["pipenv", "run", "pytest"],
)
_PACKAGE_MANAGER_NAMES = frozenset({
    "npm", "pnpm", "yarn", "bun", "uv", "pip", "pip3", "poetry", "pipenv", "cargo", "go", "composer",
})
_PACKAGE_MANAGER_READS = frozenset({
    "audit", "config", "help", "info", "list", "ls", "outdated", "search", "show", "tree", "view", "why",
})
_SAFE_SCRIPT_RUNS = frozenset({"build", "check", "dev", "lint", "preview", "serve", "start", "test", "tests", "typecheck"})
# Script names that verify or build, as opposed to the servers (`dev`, `serve`, `start`, `preview`) above.
_VERIFICATION_SCRIPT_VERBS = frozenset({"build", "check", "lint", "test", "tests", "typecheck"})
# A script name that says it rewrites files makes `test:update` / `lint:fix` / `format:all` a writer whatever else it says.
_WRITING_SCRIPT_WORDS = frozenset({
    "fix", "write", "update", "snapshot", "snapshots", "format", "fmt", "approve", "accept", "generate", "gen",
    "bless", "rewrite", "autofix", "migrate",
})
_MUTATING_VERIFY_FLAGS = frozenset({"--accept", "--fix", "--fix-only", "--update", "--update-snapshots", "--write"})
_INLINE_MUTATION_RE = re.compile(
    r"(?:\bopen\s*\([^)]*,\s*['\"](?:w|a|x|\+)|\.write(?:_text|_bytes)?\s*\(|"
    r"\bwriteFile(?:Sync)?\s*\(|\b(?:unlink|remove|rename|replace|mkdir|rmdir|makedirs|rmtree|copy2?|move)\s*\(|"
    r"\b(?:appendFile|copyFile|createWriteStream|rmSync|cpSync|truncate|symlink|chmod|chown|WriteAll\w*|AppendAll\w*)\w*\s*\(|"
    # A program that can start another process can do anything: it is not provably read-only.
    r"\b(?:subprocess|Popen|child_process|execSync|spawnSync|execFileSync)\b|\bos\s*\.\s*(?:system|popen|exec\w*|spawn\w*)\s*\(|"
    r"\b(?:set-content|add-content|remove-item|move-item|copy-item|out-file|new-item|clear-content|rename-item)\b)",
    re.IGNORECASE,
)
# Flags that carry the program text inline, per interpreter kind (see terminal_writers.interpreter_kind).
_INLINE_PROGRAM_FLAGS = {
    "python": {"-c"}, "node": {"-e", "--eval", "-p", "--print"}, "ruby": {"-e"}, "perl": {"-e", "-E"}, "php": {"-r"},
    "shell": {"-c"}, "powershell": {"-c", "-command"}, "cmd": {"/c", "/k", "/r"},
}
_SHELL_COMMAND_FLAG_RE = re.compile(r"-[a-z]*c")  # bash -c, bash -lc, sh -ec, ...
_SCRIPT_FILE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".rb", ".pl", ".php", ".ps1"}
_HEREDOC_DELIMITER_RE = re.compile(r"""'([^'\n]+)'|"([^"\n]+)"|([^\s;&|()<>'"]+)""")
# Git subcommands that leave every file of the working tree alone (they read, or only touch refs and the object store).
# `worktree` depends on its operand (see _git_changes_workspace); `commit` is absent on purpose: a pre-commit hook may
# reformat files.
_READ_ONLY_GIT_COMMANDS = frozenset({
    "add", "branch", "config", "diff", "fetch", "grep", "log", "ls-files", "push", "remote", "rev-parse", "show",
    "status", "tag",
    "blame", "cat-file", "check-ignore", "cherry", "count-objects", "describe", "for-each-ref", "hash-object",
    "help", "ls-remote", "ls-tree", "merge-base", "name-rev", "rev-list", "shortlog", "show-ref", "version",
})
# Commands that write on every use, matched by base name. PowerShell cmdlets and cmd built-ins sit beside
# the POSIX tools because the terminal tool runs whichever shell the host has.
_SHELL_WRITER_COMMANDS = frozenset({
    "touch", "mkdir", "ln", "patch", "tee",
    "set-content", "add-content", "out-file", "new-item", "remove-item", "move-item", "copy-item",
    "clear-content", "rename-item",
    "del", "erase", "md", "rd", "ren", "move", "copy", "xcopy", "robocopy", "mklink",
})
_FORMATTERS_WRITING_BY_DEFAULT = frozenset({"black", "isort", "rustfmt"})
_FORMATTERS_WRITING_WITH_INPLACE_FLAG = frozenset({"autopep8", "yapf"})
_FORMAT_SUBCOMMAND = {"ruff": "format", "cargo": "fmt"}
_CHECK_ONLY_FLAGS = frozenset({"--check", "--check-only", "--diff", "--dry-run", "-n", "-c"})
_INPLACE_FLAGS = frozenset({"-i", "--in-place"})
_INPLACE_EDIT_INTERPRETERS = frozenset({"perl", "ruby"})
_INPLACE_EDIT_FLAG_RE = re.compile(r"-[A-Za-z]*i[A-Za-z0-9.]*")
_REDIRECT_WRITE_OPERATORS = frozenset({">", ">>", ">|", "&>", "&>>"})
_NON_FILE_SINKS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "nul", "$null", "con"})
_SCHEMA_DDL = (
    """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    """
        CREATE TABLE IF NOT EXISTS verification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            session_id TEXT NOT NULL,
            cwd TEXT NOT NULL,
            root TEXT NOT NULL,
            command TEXT NOT NULL,
            canonical_command TEXT NOT NULL,
            kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            output_summary TEXT NOT NULL
        )
        """,
    """
        CREATE TABLE IF NOT EXISTS verification_state (
            session_id TEXT NOT NULL,
            root TEXT NOT NULL,
            last_event_id INTEGER,
            last_edit_at TEXT,
            changed_paths_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (session_id, root)
        )
        """,
    """
        CREATE INDEX IF NOT EXISTS idx_verification_events_session_root
        ON verification_events(session_id, root, id DESC)
        """,
)


@dataclass(frozen=True)
class _ShellSegment:
    tokens: list[str]
    following_operator: str | None = None
    raw: str = ""
    # Text of the heredoc bodies that start on this segment's line. It is data for a program that reads
    # stdin, never commands, and is kept out of the tokenizer so its quotes cannot unbalance the command.
    heredoc: str = ""


@dataclass(frozen=True)
class _Stdin:
    """What can feed the standard input of the segment being judged."""

    piped: bool = False
    heredocs: str = ""


@dataclass(frozen=True)
class VerificationEvidence:
    """A classified command result worth recording."""

    command: str
    canonical_command: str
    kind: str
    scope: str
    status: str
    exit_code: int
    cwd: str
    root: str
    session_id: str
    output_summary: str = ""


_CLOCK_LOCK = threading.Lock()
_last_stamp: datetime | None = None


def _utc_now() -> str:
    """Strictly increasing within the process: the ledger decides staleness by comparing these stamps, and a
    coarse wall clock (15.6 ms on Windows before Python 3.13) would otherwise give an edit and the evidence
    right before it the same stamp."""
    global _last_stamp
    with _CLOCK_LOCK:
        now = datetime.now(timezone.utc)
        if _last_stamp is not None and now <= _last_stamp:
            now = _last_stamp + timedelta(microseconds=1)
        _last_stamp = now
        return now.isoformat()


def ledger_timestamp() -> str:
    """The ledger's clock, for callers that must date a run on the same scale as edits and evidence."""
    return _utc_now()


def _db_path() -> Path:
    return get_hermes_home() / "verification_evidence.db"


def _ledger_enabled() -> bool:
    """The ledger exists only to feed verify-on-stop; when that guard is off nothing may
    record, read, or even create the database (an unconsumed ledger is pure disk churn)."""
    from agent.verification_stop import verify_on_stop_enabled

    return verify_on_stop_enabled()


def _connect() -> sqlite3.Connection:
    from hermes_cli.sqlite_util import open_db

    return open_db(_db_path(), db_label="verification_evidence.db", initialize=_ensure_schema)


def _transaction():
    from hermes_cli.sqlite_util import transaction

    return transaction(_connect())


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for ddl in _SCHEMA_DDL:
        conn.execute(ddl)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(_VERIFY_SCHEMA_VERSION),),
    )
    conn.commit()


def _heredoc_marker(command: str, index: int) -> tuple[str, bool, int] | None:
    """``<<[-]WORD`` at ``index`` as ``(delimiter, strip_tabs, end)``; ``<<<`` (a here-string) is not one."""
    if not command.startswith("<<", index) or command.startswith("<<<", index):
        return None
    cursor = index + 2
    strip_tabs = command.startswith("-", cursor)
    cursor += 1 if strip_tabs else 0
    while cursor < len(command) and command[cursor] in " \t":
        cursor += 1
    match = _HEREDOC_DELIMITER_RE.match(command, cursor)
    if match is None:
        return None
    delimiter = next(group for group in match.groups() if group)
    if match.group(3) is not None and delimiter.isdigit():
        return None  # `$((1<<3))` shifts bits; a later line reading `3` must not hide the commands after it
    return delimiter, strip_tabs, match.end()


def _read_heredoc_bodies(command: str, start: int, pending: list[tuple[str, bool]]) -> tuple[list[str], int] | None:
    """Bodies of the heredocs opened on the previous line, and the index just after the last terminator.

    ``None`` when a terminator is missing. A shell would then read to the end of its input, but a ``<<`` that was
    not really a heredoc (arithmetic, text) must not hide the commands that follow it, so the caller ignores it."""
    position, bodies = start, []
    for delimiter, strip_tabs in pending:
        lines: list[str] = []
        while True:
            end = command.find("\n", position)
            line = command[position:len(command) if end == -1 else end].rstrip("\r")
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                position = len(command) if end == -1 else end + 1
                break
            if end == -1:
                return None
            lines.append(line)
            position = end + 1
        bodies.append("\n".join(lines))
    return bodies, position


def _split_shell_segments(command: str, *, posix: bool = True) -> list[_ShellSegment]:
    """Tokenize top-level shell commands while preserving their control operators.

    Blank lines, ``#`` comments and a line break after an operator separate nothing and are dropped. Heredoc
    bodies are not commands: they are attached to the segment on whose line they start. Returns ``[]`` for
    anything unparseable (unbalanced quotes, an operator with nothing on one side, a trailing operator other
    than ``;`` or a background ``&``) so callers never match a partial parse.
    """
    raw_segments: list[tuple[str, str | None, str]] = []
    pieces: list[str] = []  # text of the current segment that precedes a comment cut out of it
    chunk = 0  # where the uncut tail of the current segment begins
    quote: str | None = None
    escaped = False
    pending: list[tuple[str, bool]] = []  # heredocs opened on the current line
    index = 0

    def segment_text(end: int) -> str:
        return ("".join(pieces) + command[chunk:end]).strip()

    while index < len(command):
        char = command[index]
        if escaped or (char == "\\" and quote != "'"):
            escaped = not escaped  # consume the escaped char / start an escape
            index += 1
            continue
        if quote or char in "'\"":
            quote = None if char == quote else (quote or char)  # close / stay / open
            index += 1
            continue
        if char == "#" and (index == 0 or command[index - 1] in " \t\r\n;&|()"):
            # A comment runs to the end of the line and is no part of the command (its apostrophes are not quotes).
            end = command.find("\n", index)
            end = len(command) if end == -1 else end
            pieces.append(command[chunk:index])
            chunk = index = end
            continue
        marker = _heredoc_marker(command, index)
        if marker is not None:
            pending.append(marker[:2])
            index = marker[2]
            continue

        operator = None
        if command.startswith(("&&", "||", "|&"), index):
            operator = command[index:index + 2]
        elif char == "\n":
            operator = ";"
        elif char in ";|" or (
            char == "&"
            and (index == 0 or command[index - 1] not in "<>")
            and not command.startswith(("&>", "&>>"), index)
        ):
            operator = char

        if operator is None:
            index += 1
            continue

        raw = segment_text(index)
        resume = index + (1 if char == "\n" else len(operator))
        heredoc = ""
        if char == "\n":
            bodies = _read_heredoc_bodies(command, resume, pending) if pending and raw else None
            if bodies is not None:
                heredoc, resume = "\n".join(bodies[0]), bodies[1]
            pending = []
        if not raw:
            if char != "\n":
                return []
            # a blank line, a comment-only line or a break after an operator: nothing to separate
        else:
            raw_segments.append((raw, operator, heredoc))
        pieces, chunk, index = [], resume, resume

    if quote or escaped:
        return []
    trailing = segment_text(len(command))
    if trailing:
        raw_segments.append((trailing, None, ""))
    elif raw_segments and raw_segments[-1][1] not in {";", "&"}:  # `cmd &` (a background job) is a complete command
        return []

    try:
        segments = [
            _ShellSegment(shlex.split(raw, posix=posix), operator, raw, heredoc)
            for raw, operator, heredoc in raw_segments
        ]
    except ValueError:
        return []
    return segments if all(s.tokens for s in segments) else []


def _exit_status_is_attributable(segments: list[_ShellSegment], match_index: int, exit_code: int) -> bool:
    """Whether the shell's status proves the matched segment's own status.

    Only the last ``;``-separated sequence reports its status; backgrounding,
    pipes and ``||`` hide it; an ``&&`` chain proves each member only on success.
    """
    if not segments or not 0 <= match_index < len(segments) or any(s.following_operator == "&" for s in segments):
        return False

    sequence_start = max(
        (i + 1 for i, s in enumerate(segments[:-1]) if s.following_operator == ";"), default=0
    )
    if match_index < sequence_start:
        return False

    operators = {segment.following_operator for segment in segments[sequence_start:-1]}
    if operators & {"|", "|&", "||"}:
        return False
    return not operators or (int(exit_code) == 0 and operators == {"&&"})


def _canonical_tokens(canonical: str) -> list[str]:
    """Tokenize a canonical command, stripping leading ``./`` from each token."""
    try:
        return [re.sub(r"^(?:\./)+", "", t.strip()) for t in shlex.split(canonical) if t]
    except ValueError:
        return []


def _strip_command_prefix(tokens: list[str]) -> list[str]:
    """Remove harmless command prefixes (env, VAR=x, command/time/noglob/nohup)."""
    i = 1 if tokens and tokens[0] == "env" else 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
        i += 1
    while i < len(tokens) and tokens[i] in {"command", "time", "noglob", "nohup"}:
        i += 1
    return list(tokens[i:])


def _equivalent_needles(needle: list[str]) -> list[list[str]]:
    """Return command spellings equivalent to the detected canonical command."""
    candidates = [needle]
    if len(needle) >= 3 and needle[1] == "run" and needle[0] in {"npm", "pnpm", "yarn", "bun"}:
        candidates.append([needle[0], needle[2]])
    if len(needle) == 1 and "/" in needle[0]:
        candidates += [["bash", needle[0]], ["sh", needle[0]]]
    if needle == ["pytest"]:
        candidates += _PYTEST_SPELLINGS
    return candidates


def _command_key(word: str) -> str:
    """What a command word runs, whatever launcher spelling reached it: ``C:/x/bash.exe`` is ``bash``, ``pnpm.cmd``
    is ``pnpm``, ``python3.11`` / ``py`` / ``python.exe`` are ``python``."""
    name = executable_name(word)
    return "python" if interpreter_kind(name) == "python" else name


def _segment_command_word(segment: _ShellSegment) -> str:
    """The segment's command word as typed (a Windows path keeps its backslashes), after harmless prefixes."""
    words = _strip_command_prefix(command_words(segment.raw)) if segment.raw else []
    return words[0] if words else (_strip_command_prefix(segment.tokens) or [""])[0]


def _runs_command(tokens: list[str], command_word: str, needle: list[str]) -> bool:
    """Whether the prefix-stripped ``tokens`` start with ``needle``. A bare command name is judged by what it runs,
    not by how it is launched; a script path (``scripts/run_tests.sh``) must match exactly."""
    if len(tokens) < len(needle):
        return False
    head = needle[0]
    same_head = tokens[0] == head if ("/" in head or "\\" in head) else _command_key(command_word) == _command_key(head)
    return same_head and tokens[1:len(needle)] == needle[1:]


def _find_canonical_match(command: str, canonical_commands: list[str], exit_code: int) -> Optional[tuple[str, list[str]]]:
    """Return ``(canonical, trailing_args)`` for the first detected command."""
    segments = _split_shell_segments(command)
    for canonical in canonical_commands:
        needle = _canonical_tokens(canonical)
        if not needle:
            continue
        for index, segment in enumerate(segments):
            candidate_tokens = _strip_command_prefix(segment.tokens)
            command_word = _segment_command_word(segment)
            for candidate in _equivalent_needles(needle):
                if _runs_command(candidate_tokens, command_word, candidate) and _exit_status_is_attributable(
                    segments, index, exit_code
                ):
                    return canonical, candidate_tokens[len(candidate):]
    return None


def _kind_for_command(canonical: str) -> str:
    lowered = canonical.lower()
    kind = next((k for words, k in _KIND_KEYWORDS if any(w in lowered for w in words)), None)
    return kind or ("check" if "check" in lowered and "test" not in lowered else "test")


def _canonical_match_for_segment(
    segment: _ShellSegment, canonical_commands: list[str]
) -> Optional[tuple[str, list[str]]]:
    """Return the canonical verifier and trailing args for one shell segment."""
    candidate_tokens = _strip_command_prefix(segment.tokens)
    command_word = _segment_command_word(segment)
    for canonical in canonical_commands:
        needle = _canonical_tokens(canonical)
        if not needle:
            continue
        for candidate in _equivalent_needles(needle):
            if _runs_command(candidate_tokens, command_word, candidate):
                return canonical, candidate_tokens[len(candidate):]
    return None


def _verification_segment_indexes(
    segments: list[_ShellSegment], *, canonical_commands: list[str], root: str | Path | None, exit_code: int
) -> list[int]:
    """Indexes of shell segments whose result can contribute fresh verification evidence."""
    indexes: list[int] = []
    for index, segment in enumerate(segments):
        if canonical_commands and _canonical_match_for_segment(segment, canonical_commands) is not None:
            if _exit_status_is_attributable(segments, index, exit_code):
                indexes.append(index)
        elif not canonical_commands and _ad_hoc_script_args(segment.tokens, root) is not None:
            if _exit_status_is_attributable(segments, index, exit_code):
                indexes.append(index)
    return indexes


# Flags that take a separate value, so the value is not mistaken for the script operand.
_INTERPRETER_VALUE_FLAGS = {
    "python": {"-w", "-x"},
    "node": {"-r", "--require", "--import", "--loader", "--experimental-loader"},
    "powershell": {"-executionpolicy", "-ep", "-windowstyle", "-inputformat", "-outputformat", "-configurationname",
                   "-workingdirectory", "-wd", "-version"},
    "shell": {"-o", "--rcfile", "--init-file"},
}
_REDIRECT_OPERATOR_TOKENS = frozenset({"<", ">", ">>", ">|", "<<", "&>", "&>>", "1>", "2>", "1>>", "2>>"})


def _interpreter_may_modify(
    kind: str, args: list[str], *, canonical_commands: list[str], root: str | Path | None, stdin: _Stdin,
) -> bool:
    """Whether an interpreter run may write files. Its program is inline text, a module, a script file, or is read
    from stdin; custom scripts are effect-capable while obvious test scripts are exempt."""
    flags, value_flags = _INLINE_PROGRAM_FLAGS[kind], _INTERPRETER_VALUE_FLAGS.get(kind, ())
    operand, redirected, position = "", False, 0
    while position < len(args):
        arg = args[position]
        lowered = arg.lower()
        position += 1
        if lowered == "-encodedcommand":
            return True  # an opaque base64 program
        if lowered in flags or (kind == "shell" and _SHELL_COMMAND_FLAG_RE.fullmatch(lowered)):
            rest = args[position:]
            if not rest:
                return False
            text = " ".join(rest) if kind in {"powershell", "cmd"} else rest[0]
            return _inline_program_may_modify(kind, text, canonical_commands=canonical_commands, root=root)
        if kind == "python" and arg == "-m":
            module = args[position].lower() if position < len(args) else ""
            return bool(module) and module not in {"pytest", "unittest", "nose", "nose2"}
        if kind == "shell" and arg == "-s":
            operand = "-"  # the shell reads its commands from stdin
            break
        if arg in _REDIRECT_OPERATOR_TOKENS:
            redirected = redirected or arg == "<"
            position += 1  # the redirect target is not the script
            continue
        if arg.startswith("<") and not arg.startswith("<<"):
            redirected = True
            continue
        if arg.startswith(("<", ">")) or re.match(r"\d+[<>]", arg):
            continue
        if lowered in value_flags:
            position += 1
            continue
        if arg.startswith("-") or (kind == "cmd" and arg.startswith("/")) or "=" in arg:
            continue
        operand = arg
        break
    if operand and operand != "-":
        if kind == "node":
            known_verifier_writes = node_verifier_writes(operand, args[position:])
            if known_verifier_writes is not None:
                return known_verifier_writes
        return _script_path_may_modify(operand, root=root)
    # No script operand: the program comes from stdin (`python -`, `python <<EOF`, `curl ... | sh`).
    if stdin.heredocs:
        return _inline_program_may_modify(kind, stdin.heredocs, canonical_commands=canonical_commands, root=root)
    return stdin.piped or redirected  # an unknown producer or a file: opaque; otherwise nothing is fed


def _inline_program_may_modify(kind: str, text: str, *, canonical_commands: list[str], root: str | Path | None) -> bool:
    """Program text we can read: writes by pattern, and for a shell the text is itself a command line."""
    if _INLINE_MUTATION_RE.search(text):
        return True
    if kind in {"shell", "powershell", "cmd"}:
        return _command_line_may_modify(text, canonical_commands=canonical_commands, root=root)
    return False


def _command_line_may_modify(command: str, *, canonical_commands: list[str], root: str | Path | None) -> bool:
    """Whether a command line (a shell's inline script, or heredoc text fed to one) may write files."""
    segments = _split_shell_segments(command)
    if not segments:
        return True  # unparseable: nothing proves it read-only
    return bool(_mutating_segment_indexes(segments, canonical_commands=canonical_commands, root=root))


def _mutating_segment_indexes(
    segments: list[_ShellSegment], *, canonical_commands: list[str], root: str | Path | None,
) -> list[int]:
    """Indexes of the segments that may change workspace files (verifier output capture excepted)."""
    heredocs = "\n".join(segment.heredoc for segment in segments if segment.heredoc)
    return [
        index for index, segment in enumerate(segments)
        if _segment_may_modify_workspace(
            segment, canonical_commands=canonical_commands, root=root,
            stdin=_Stdin(piped=index > 0 and segments[index - 1].following_operator in {"|", "|&"}, heredocs=heredocs),
        )
        and not _is_verifier_output_capture(segments, index, canonical_commands=canonical_commands, root=root)
    ]


def _script_path_may_modify(script: str, *, root: str | Path | None) -> bool:
    if not script or _is_temp_script_path(script, root):
        return False
    lowered = script.replace("\\", "/").lower()
    name = Path(script).name.lower()
    if any(part in lowered.split("/") for part in _TARGET_PREFIXES):
        return False
    if re.search(r"(?:^|[-_])(test|tests|spec|verify|check|lint|smoke|probe)(?:[-_.]|$)", name):
        return False
    return Path(script).suffix.lower() in _SCRIPT_FILE_SUFFIXES


def _segment_may_modify_workspace(
    segment: _ShellSegment, *, canonical_commands: list[str], root: str | Path | None, stdin: _Stdin = _Stdin(),
) -> bool:
    """Use command intent to recognize writes without treating ordinary tests/builds as edits."""
    match = _canonical_match_for_segment(segment, canonical_commands) if canonical_commands else None
    tokens = _strip_command_prefix(segment.tokens)
    lowered_tokens = {token.lower() for token in tokens}
    if match is not None:
        canonical, _trailing_args = match
        kind = _kind_for_command(canonical)
        if kind == "format" and not ({"--check", "--dry-run", "-n"} & lowered_tokens):
            return True
        return bool(_MUTATING_VERIFY_FLAGS & lowered_tokens)

    # An explicitly named temporary verification script is an established ad-hoc
    # proof surface. Other scripts are effect-capable because shell output alone
    # cannot prove that their writes stayed outside the workspace.
    if _ad_hoc_script_args(segment.tokens, root) is not None:
        return False
    if not tokens:
        return False

    from agent.tool_dispatch_helpers import _DESTRUCTIVE_PATTERNS

    raw = segment.raw or shlex.join(segment.tokens)
    if any(not _redirect_leaves_workspace(target, root) for target in _redirect_write_targets(raw)):
        return True
    format_verdict = _formatter_verdict(tokens)
    if format_verdict is not None:
        return format_verdict
    if _DESTRUCTIVE_PATTERNS.search(raw):
        return True
    # The command word as typed: POSIX splitting eats the backslashes of `.venv\Scripts\python.exe`.
    words = _strip_command_prefix(command_words(raw)) or tokens
    base = executable_name(words[0])
    if base in _SHELL_WRITER_COMMANDS:
        return _writer_has_target(base, tokens)
    conditional = conditional_write(base, tokens[1:])
    if conditional is not None:
        return conditional
    if _inplace_edit(tokens):
        return True
    if base in _PACKAGE_MANAGER_NAMES:
        if len(tokens) < 2:
            return False
        subcommand = tokens[1].lower()
        if subcommand in _PACKAGE_MANAGER_READS or subcommand in {"--version", "-v", "version"}:
            return False
        if subcommand in _SAFE_SCRIPT_RUNS:
            return False
        if subcommand == "run" and len(tokens) > 2:
            return not _is_verification_script(tokens[2])
        return subcommand not in {"help"}
    if base == "git":
        return _git_changes_workspace(tokens)
    kind = interpreter_kind(base)
    if kind is not None and _interpreter_may_modify(
        kind, tokens[1:], canonical_commands=canonical_commands, root=root, stdin=stdin,
    ):
        return True
    if Path(words[0]).suffix.lower() in _SCRIPT_FILE_SUFFIXES:
        return _script_path_may_modify(words[0], root=root)
    return False


def _command_name(token: str) -> str:
    return Path(token).name.lower().removesuffix(".exe")


def _redirect_write_targets(raw: str) -> list[str]:
    """Files a segment's ``>`` / ``>>`` style redirects write to; quoted text is never an operator."""
    lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    return [
        target for operator, target in zip(tokens, tokens[1:])
        if operator in _REDIRECT_WRITE_OPERATORS and target.lower() not in _NON_FILE_SINKS
    ]


def _formatter_verdict(tokens: list[str]) -> Optional[bool]:
    """Whether a formatter invocation rewrites files; ``None`` when ``tokens`` is not a formatter."""
    name = _command_name(tokens[0])
    flags = {token.lower() for token in tokens[1:]}
    if name in _FORMATTERS_WRITING_BY_DEFAULT:
        return not flags & _CHECK_ONLY_FLAGS
    if name in _FORMATTERS_WRITING_WITH_INPLACE_FLAG:
        return bool(flags & _INPLACE_FLAGS) and not flags & _CHECK_ONLY_FLAGS
    if name in _FORMAT_SUBCOMMAND and len(tokens) > 1 and tokens[1].lower() == _FORMAT_SUBCOMMAND[name]:
        return not flags & _CHECK_ONLY_FLAGS
    return None


def _writer_has_target(name: str, tokens: list[str]) -> bool:
    """``tee`` with no file operand only forwards its input; every other writer always writes."""
    if name != "tee":
        return True
    return any(
        not token.startswith("-") and token.lower() not in _NON_FILE_SINKS for token in tokens[1:]
    )


def _inplace_edit(tokens: list[str]) -> bool:
    """``perl -pi`` / ``ruby -i`` / ``awk -i inplace`` rewrite their input files."""
    name = _command_name(tokens[0])
    if name in _INPLACE_EDIT_INTERPRETERS:
        return any(_INPLACE_EDIT_FLAG_RE.fullmatch(token) for token in tokens[1:] if not token.startswith("--"))
    if name in {"awk", "gawk"}:
        return any(flag == "-i" and value == "inplace" for flag, value in zip(tokens[1:], tokens[2:]))
    return False


def _is_verifier_output_capture(
    segments: list[_ShellSegment], index: int, *, canonical_commands: list[str], root: str | Path | None
) -> bool:
    """``<verifier> | tee log``: the file holds the verifier's own output, not an edit to the project."""
    if index == 0 or segments[index - 1].following_operator not in {"|", "|&"}:
        return False
    tokens = _strip_command_prefix(segments[index].tokens)
    if not tokens or _command_name(tokens[0]) != "tee":
        return False
    producer = segments[index - 1]
    return (
        _canonical_match_for_segment(producer, canonical_commands) is not None
        or _ad_hoc_script_args(producer.tokens, root) is not None
    )


def _git_subcommand_index(tokens: list[str]) -> int:
    """Index of git's subcommand after global options that take values, -1 when there is none."""
    value_options = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in value_options:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return -1


def _git_subcommand(tokens: list[str]) -> str:
    """Find git's subcommand after global options that take values."""
    index = _git_subcommand_index(tokens)
    return tokens[index].lower() if index >= 0 else ""


def _git_changes_workspace(tokens: list[str]) -> bool:
    """Whether a git command may change a file of the working tree.

    No subcommand at all (`git --version`, `git --help`) only prints; `git worktree` depends on its operand
    (`list` reads, `add` / `remove` / `prune` write)."""
    index = _git_subcommand_index(tokens)
    if index < 0:
        return False
    subcommand = tokens[index].lower()
    if subcommand in _READ_ONLY_GIT_COMMANDS:
        return False
    if subcommand == "worktree":
        return next((t.lower() for t in tokens[index + 1:] if not t.startswith("-")), "") != "list"
    return True


def _is_verification_script(name: str) -> bool:
    """``test``, ``check:lint``, ``test:ui``, ``ui:test``: a package script that verifies (or builds, serves), unless a
    word of its name says it rewrites files (``test:update``, ``lint:fix``, ``format:all``). A verification verb may lead
    the name (``test:ui``); a server verb (`dev:seed-db`) only counts as the name's last word, as before."""
    parts = [part for part in re.split(r"[:\-_.]", name.lower()) if part]
    if not parts or any(part in _WRITING_SCRIPT_WORDS for part in parts):
        return False
    return parts[-1] in _SAFE_SCRIPT_RUNS or parts[0] in _VERIFICATION_SCRIPT_VERBS


def _redirect_leaves_workspace(target: str, root: str | Path | None) -> bool:
    """Whether a redirect target is provably outside the project, so the file it writes cannot be one the proof covers.

    Only an absolute path can be shown to be: a relative one, an unexpanded variable or a glob may land on a project file."""
    if not root or not target or any(char in target for char in "$%`*?[~"):
        return False
    return Path(target).is_absolute() and not _is_under(target, root)


def _looks_like_target(arg: str) -> bool:
    return bool(arg) and not arg.startswith("-") and "=" not in arg and (
        any(m in arg for m in ("/", "\\", "::")) or arg.endswith(_TARGET_EXTENSIONS) or arg.startswith(_TARGET_PREFIXES)
    )


def _is_under(token: str, base: str | Path | None) -> bool:
    """Whether absolute path ``token`` is ``base`` or lies beneath it (resolved)."""
    if not base:
        return False
    try:
        path = Path(token).expanduser()
        if not path.is_absolute():
            return False
        resolved, base_path = path.resolve(), Path(base).expanduser().resolve()
        return resolved == base_path or base_path in resolved.parents
    except Exception:
        return False


def _is_temp_script_path(token: str, root: str | Path | None) -> bool:
    """An ad-hoc verify script: prefixed name, under the temp dir, outside the repo."""
    try:
        name = Path(token).expanduser().name
    except Exception:
        return False
    return name.startswith(_AD_HOC_SCRIPT_NAME_PREFIXES) and _is_under(token, tempfile.gettempdir()) and not _is_under(token, root)


def _ad_hoc_script_args(tokens: list[str], root: str | Path | None) -> Optional[list[str]]:
    candidate_tokens = _strip_command_prefix(tokens)
    if not candidate_tokens:
        return None
    command = candidate_tokens[0]
    if _is_temp_script_path(command, root):
        return candidate_tokens[1:]
    if command in _INTERPRETERS:
        # Skip interpreter flags; the first positional must be the script.
        for idx, token in enumerate(candidate_tokens[1:], start=1):
            if _is_temp_script_path(token, root):
                return candidate_tokens[idx + 1:]
            if token != "--" and not token.startswith("-"):
                return None
    return None


def _find_ad_hoc_match(command: str, root: str | Path | None, exit_code: int = 0) -> Optional[list[str]]:
    # posix=False is retried so Windows backslash script paths survive splitting.
    for posix in (True, False):
        segments = _split_shell_segments(command, posix=posix)
        for index, segment in enumerate(segments):
            trailing_args = _ad_hoc_script_args(segment.tokens, root)
            if trailing_args is not None and _exit_status_is_attributable(segments, index, exit_code):
                return trailing_args
    return None


def _summarize_output(output: str) -> str:
    text = (output or "").strip()
    if len(text) <= _MAX_OUTPUT_SUMMARY_CHARS:
        return text
    head = _MAX_OUTPUT_SUMMARY_CHARS // 3
    return (
        f"{text[:head]}\n... [{len(text) - _MAX_OUTPUT_SUMMARY_CHARS} chars omitted] ...\n"
        f"{text[head - _MAX_OUTPUT_SUMMARY_CHARS:]}"
    )


def _prune_old_events(conn: sqlite3.Connection, *, session_id: str, root: str) -> None:
    """Bound ledger growth without deleting the current state pointer.

    Order matters: per-(session, root) cap, expire stale state rows, then expire
    old events and cap the total — never dropping an event still referenced
    by a ``verification_state.last_event_id``.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_MAX_EVIDENCE_AGE_DAYS)).isoformat()
    conn.execute(
        "DELETE FROM verification_events WHERE session_id = ? AND root = ? AND id NOT IN ("
        " SELECT id FROM verification_events WHERE session_id = ? AND root = ?"
        " ORDER BY id DESC LIMIT ?)",
        (session_id, root, session_id, root, _MAX_EVENTS_PER_SESSION_ROOT),
    )
    conn.execute(
        "DELETE FROM verification_state"
        " WHERE (last_edit_at IS NOT NULL AND last_edit_at < ?)"
        " OR (last_edit_at IS NULL AND last_event_id IN ("
        " SELECT id FROM verification_events WHERE created_at < ?))",
        (cutoff, cutoff),
    )
    conn.execute(
        "DELETE FROM verification_events WHERE created_at < ? AND id NOT IN ("
        " SELECT last_event_id FROM verification_state WHERE last_event_id IS NOT NULL)",
        (cutoff,),
    )
    conn.execute(
        "DELETE FROM verification_events WHERE id NOT IN ("
        " SELECT id FROM verification_events ORDER BY id DESC LIMIT ?)"
        " AND id NOT IN ("
        " SELECT last_event_id FROM verification_state WHERE last_event_id IS NOT NULL)",
        (_MAX_TOTAL_UNREFERENCED_EVENTS,),
    )


def _project_facts(cwd: str | Path | None) -> Optional[dict[str, Any]]:
    """Workspace facts for ``cwd``; ``None`` when detection fails or finds nothing."""
    try:
        from agent.coding_context import project_facts_for

        return project_facts_for(cwd)
    except Exception:
        return None


def _root_for(facts: dict[str, Any] | None, cwd: str | Path | None) -> str:
    return str((facts or {}).get("root") or Path(cwd or ".").resolve())


def _load_changed_paths(raw: Any) -> list[Any]:
    try:
        return json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []


def classify_verification_command(
    command: str, *, cwd: str | Path | None = None, session_id: str | None = None, exit_code: int = 0, output: str = ""
) -> Optional[VerificationEvidence]:
    """Classify a terminal command as verification evidence, if applicable.

    Ad-hoc temp scripts only count when the project has no canonical verify
    commands at all, so they never shadow a real suite.
    """
    if not command or not isinstance(command, str):
        return None
    facts = _project_facts(cwd)
    if not facts:
        return None

    verify_commands = list(facts.get("verifyCommands") or [])
    match = _find_canonical_match(command, verify_commands, int(exit_code))
    if match is not None:
        canonical, trailing_args = match
        kind = _kind_for_command(canonical)
        scope = "targeted" if any(map(_looks_like_target, trailing_args)) else "full"
    else:
        if verify_commands or _find_ad_hoc_match(command, facts.get("root"), int(exit_code)) is None:
            return None
        canonical, kind, scope = "ad-hoc verification script", "ad_hoc", "targeted"
    return VerificationEvidence(
        command=command, canonical_command=canonical, kind=kind, scope=scope,
        status="passed" if int(exit_code) == 0 else "failed", exit_code=int(exit_code),
        cwd=str(Path(cwd or ".").resolve()), root=_root_for(facts, cwd),
        session_id=str(session_id or "default"), output_summary=_summarize_output(output),
    )


def record_terminal_result(
    *, command: str, cwd: str | Path | None, session_id: str | None, exit_code: int, output: str = "",
    observed_at: str | None = None,
) -> Optional[dict[str, Any]]:
    """Record a terminal result when it is verification evidence.

    ``observed_at`` (a :func:`ledger_timestamp`) dates evidence from a run that finished later than it
    started, such as a background command: edits recorded after that moment were not covered by the run
    and stay pending instead of being cleared by it.
    """
    if not _ledger_enabled():
        return None
    evidence = classify_verification_command(command, cwd=cwd, session_id=session_id, exit_code=exit_code, output=output)
    return None if evidence is None else _insert_evidence(evidence, observed_at=observed_at)


def terminal_workspace_mutation_root(
    *, command: str, cwd: str | Path | None, exit_code: int, verification_complete: bool = True
) -> str | None:
    """Return the project root if a command may have changed it after its last verifier.

    The shell result is not a filesystem diff, so this is deliberately intent-based:
    recognized tests, lint, typechecks, and builds are read-only; package changes,
    formatting, file operations, and custom scripts can invalidate earlier evidence.
    Read-only is proven, never assumed: a command that cannot be parsed, or a program
    fed through stdin that cannot be read, counts as a possible mutation.
    In a compound command, a later verifier keeps a completed command fresh. Set
    ``verification_complete=False`` while a background command is still running.
    """
    if not command or not isinstance(command, str):
        return None
    facts = _project_facts(cwd)
    if not facts:
        return None
    segments = _split_shell_segments(command)
    if not segments:
        # Unparseable (unbalanced quotes, a dangling operator): nothing proves the workspace is unchanged, and a
        # false "unchanged" lets stale proof pass for fresh while a false "changed" costs one more verification.
        return _root_for(facts, cwd)

    canonical_commands = list(facts.get("verifyCommands") or [])
    verification_indexes = _verification_segment_indexes(
        segments, canonical_commands=canonical_commands, root=facts.get("root"), exit_code=int(exit_code),
    )
    mutating_indexes = _mutating_segment_indexes(
        segments, canonical_commands=canonical_commands, root=facts.get("root"),
    )
    if not mutating_indexes:
        return None

    if not verification_complete:
        return _root_for(facts, cwd)

    last_mutation = max(mutating_indexes)
    last_verification = max(verification_indexes, default=None)
    if last_verification is not None and last_mutation < last_verification:
        return None
    return _root_for(facts, cwd)


def record_terminal_workspace_mutation(
    *, command: str, cwd: str | Path | None, session_id: str | None, exit_code: int,
    verification_complete: bool = True,
) -> Optional[dict[str, Any]]:
    """Invalidate evidence after a terminal command may have changed workspace files."""
    if not _ledger_enabled():
        return None
    root = terminal_workspace_mutation_root(
        command=command, cwd=cwd, exit_code=exit_code, verification_complete=verification_complete,
    )
    if not root:
        return None
    state = mark_workspace_edited(session_id=session_id, cwd=cwd, paths=[root])
    return {"root": state["root"]} if state else None


def record_verify_run(
    *, root: str | Path, session_id: str | None = None, ok: bool, command: str = "stardust verify",
    scope: str = "full", output: str = "",
) -> Optional[dict[str, Any]]:
    """Record a completed ``stardust verify`` run as verification evidence.

    A pass marks the workspace ``passed`` for the verify-on-stop guard like a
    canonical test command would. ``root`` is re-resolved through project facts
    so it matches what :func:`verification_status` derives later.
    """
    if not _ledger_enabled():
        return None
    resolved = str(Path(root).resolve())
    return _insert_evidence(VerificationEvidence(
        command=command, canonical_command="stardust verify", kind="verify",
        scope=scope if scope in {"full", "targeted"} else "full",
        status="passed" if ok else "failed", exit_code=0 if ok else 1, cwd=resolved,
        root=str((_project_facts(root) or {}).get("root") or resolved),
        session_id=str(session_id or "default"), output_summary=_summarize_output(output),
    ))


def _insert_evidence(evidence: VerificationEvidence, *, observed_at: str | None = None) -> dict[str, Any]:
    """Insert a classified evidence row and repoint the workspace state."""
    created_at = observed_at or _utc_now()
    e = evidence
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            "INSERT INTO verification_events("
            " created_at, session_id, cwd, root, command, canonical_command,"
            " kind, scope, status, exit_code, output_summary"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (created_at, e.session_id, e.cwd, e.root, e.command, e.canonical_command, e.kind, e.scope,
             e.status, e.exit_code, e.output_summary),
        )
        if cur.lastrowid is None:
            raise RuntimeError("verification event insert did not return an id")
        event_id = int(cur.lastrowid)
        # A run dated before an edit did not cover that edit: leave it pending rather than clearing it.
        pending = conn.execute(
            "SELECT last_edit_at FROM verification_state WHERE session_id = ? AND root = ?",
            (e.session_id, e.root),
        ).fetchone()
        keep_edits = int(bool(observed_at and pending is not None and (pending["last_edit_at"] or "") > created_at))
        conn.execute(
            "INSERT INTO verification_state("
            " session_id, root, last_event_id, last_edit_at, changed_paths_json"
            ") VALUES (?, ?, ?, NULL, '[]')"
            " ON CONFLICT(session_id, root) DO UPDATE SET"
            " last_event_id = excluded.last_event_id,"
            " last_edit_at = CASE WHEN ? THEN last_edit_at ELSE NULL END,"
            " changed_paths_json = CASE WHEN ? THEN changed_paths_json ELSE '[]' END",
            (e.session_id, e.root, event_id, keep_edits, keep_edits),
        )
        _prune_old_events(conn, session_id=e.session_id, root=e.root)
        conn.commit()

    return {"id": event_id, **e.__dict__, "created_at": created_at}


def mark_workspace_edited(
    *, session_id: str | None, cwd: str | Path | None, paths: list[str] | tuple[str, ...] | None = None
) -> Optional[dict[str, Any]]:
    """Mark verification evidence stale after a successful file edit."""
    if not _ledger_enabled():
        return None
    facts = _project_facts(cwd)
    if not facts:
        return None

    sid = str(session_id or "default")
    root = _root_for(facts, cwd)
    changed_paths = sorted({str(p) for p in (paths or []) if p})
    edited_at = _utc_now()

    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            "SELECT changed_paths_json FROM verification_state WHERE session_id = ? AND root = ?",
            (sid, root),
        ).fetchone()
        # Merge with what was already recorded, bounded to the last 200 paths.
        existing = _load_changed_paths(row["changed_paths_json"]) if row is not None else []
        merged = sorted(set(existing) | set(changed_paths))[-200:]
        conn.execute(
            "INSERT INTO verification_state("
            " session_id, root, last_event_id, last_edit_at, changed_paths_json"
            ") VALUES (?, ?, NULL, ?, ?)"
            " ON CONFLICT(session_id, root) DO UPDATE SET"
            " last_edit_at = excluded.last_edit_at,"
            " changed_paths_json = excluded.changed_paths_json",
            (sid, root, edited_at, json.dumps(merged)),
        )
        conn.commit()

    return {"session_id": sid, "root": root, "last_edit_at": edited_at, "changed_paths": changed_paths}


def carry_verification_state(*, from_session_id: str | None, to_session_id: str | None) -> int:
    """Copy the parent's per-workspace ledger rows onto a continuation session.

    Context compression (and resume onto a rotated tip) re-points ``agent.session_id`` at a fresh child id, while
    every ledger lookup is a flat ``(session_id, root)`` read. Without this the child reads ``unverified`` for a
    workspace the parent already verified, and the verify-on-stop gate asks the model to re-run checks it just ran.
    Rows the child already holds win (they are newer). Returns the number of rows copied."""
    if not _ledger_enabled():
        return 0
    src, dst = str(from_session_id or ""), str(to_session_id or "")
    if not src or not dst or src == dst:
        return 0
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO verification_state("
            " session_id, root, last_event_id, last_edit_at, changed_paths_json"
            ") SELECT ?, root, last_event_id, last_edit_at, changed_paths_json"
            " FROM verification_state WHERE session_id = ?",
            (dst, src),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def verification_status(*, session_id: str | None, cwd: str | Path | None) -> dict[str, Any]:
    """Return the best known verification state for a session/workspace.

    Evidence recorded before the latest edit is reported as ``stale``.
    """
    if not _ledger_enabled():
        return {"status": "disabled", "evidence": None}
    facts = _project_facts(cwd)
    if not facts:
        return {"status": "not_applicable", "evidence": None}

    sid = str(session_id or "default")
    root = _root_for(facts, cwd)
    with _DB_LOCK, _transaction() as conn:
        state = conn.execute(
            "SELECT last_event_id, last_edit_at, changed_paths_json"
            " FROM verification_state WHERE session_id = ? AND root = ?",
            (sid, root),
        ).fetchone()
        if state is None:
            return {"status": "unverified", "evidence": None, "root": root, "session_id": sid, "changed_paths": []}
        event = None
        if state["last_event_id"] is not None:
            event = conn.execute("SELECT * FROM verification_events WHERE id = ?", (state["last_event_id"],)).fetchone()

    result = {
        "evidence": None, "root": root, "session_id": sid, "changed_paths": _load_changed_paths(state["changed_paths_json"])
    }
    if event is None:
        return {"status": "unverified", **result}

    evidence = dict(event)
    stale = bool(state["last_edit_at"]) and state["last_edit_at"] > evidence["created_at"]
    return {"status": "stale" if stale else evidence["status"], **result, "evidence": evidence}


def verification_state_marker(*, session_id: str | None, cwd: str | Path | None) -> Optional[tuple[str, str, Optional[int]]]:
    """``(root, last_edit_at, last_event_id)`` of a workspace's ledger row.

    What a caller compares to tell one unverified state from the next: a new edit moves ``last_edit_at``, a new
    verification run moves ``last_event_id``. ``None`` when the ledger is off, ``cwd`` is no project, or nothing
    was recorded for it."""
    if not _ledger_enabled():
        return None
    facts = _project_facts(cwd)
    if not facts:
        return None
    root = _root_for(facts, cwd)
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            "SELECT last_event_id, last_edit_at FROM verification_state WHERE session_id = ? AND root = ?",
            (str(session_id or "default"), root),
        ).fetchone()
    return None if row is None else (root, row["last_edit_at"] or "", row["last_event_id"])
