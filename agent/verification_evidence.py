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
_MUTATING_VERIFY_FLAGS = frozenset({"--accept", "--fix", "--fix-only", "--update", "--update-snapshots", "--write"})
_INLINE_MUTATION_RE = re.compile(
    r"(?:\bopen\s*\([^)]*,\s*['\"](?:w|a|x|\+)|\.write(?:_text|_bytes)?\s*\(|"
    r"\bwriteFile(?:Sync)?\s*\(|\b(?:unlink|remove|rename|replace|mkdir|rmdir|makedirs|rmtree|copy2?|move)\s*\(|"
    r"\b(?:set-content|add-content|remove-item|move-item|copy-item|out-file|new-item|clear-content|rename-item)\b)",
    re.IGNORECASE,
)
_READ_ONLY_GIT_COMMANDS = frozenset({
    "add", "branch", "config", "diff", "fetch", "grep", "log", "ls-files", "push", "remote", "rev-parse", "show",
    "status", "tag",
})
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _split_shell_segments(command: str, *, posix: bool = True) -> list[_ShellSegment]:
    """Tokenize top-level shell commands while preserving their control operators.

    Returns ``[]`` for anything unparseable (unbalanced quotes, empty segment,
    trailing operator other than ``;``) so callers never match a partial parse.
    """
    raw_segments: list[tuple[str, str | None]] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0

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

        raw = command[start:index].strip()
        if not raw:
            return []
        raw_segments.append((raw, operator))
        index += 1 if char == "\n" else len(operator)
        start = index

    if quote or escaped:
        return []
    trailing = command[start:].strip()
    if trailing:
        raw_segments.append((trailing, None))
    elif raw_segments and raw_segments[-1][1] != ";":
        return []

    try:
        segments = [_ShellSegment(shlex.split(raw, posix=posix), operator, raw) for raw, operator in raw_segments]
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
    """Remove harmless command prefixes (env, VAR=x, command/time/noglob)."""
    i = 1 if tokens and tokens[0] == "env" else 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
        i += 1
    while i < len(tokens) and tokens[i] in {"command", "time", "noglob"}:
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


def _find_canonical_match(command: str, canonical_commands: list[str], exit_code: int) -> Optional[tuple[str, list[str]]]:
    """Return ``(canonical, trailing_args)`` for the first detected command."""
    segments = _split_shell_segments(command)
    for canonical in canonical_commands:
        needle = _canonical_tokens(canonical)
        if not needle:
            continue
        for index, segment in enumerate(segments):
            candidate_tokens = _strip_command_prefix(segment.tokens)
            for candidate in _equivalent_needles(needle):
                if candidate_tokens[:len(candidate)] == candidate and _exit_status_is_attributable(segments, index, exit_code):
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
    for canonical in canonical_commands:
        needle = _canonical_tokens(canonical)
        if not needle:
            continue
        for candidate in _equivalent_needles(needle):
            if candidate_tokens[:len(candidate)] == candidate:
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


def _script_invocation_may_modify(tokens: list[str], *, root: str | Path | None) -> bool:
    """Treat custom scripts as effect-capable while exempting obvious test scripts."""
    if not tokens:
        return False
    base = Path(tokens[0]).name.lower()
    if base not in _INTERPRETERS | {"pwsh", "powershell", "cmd", "cmd.exe"}:
        return False
    args = tokens[1:]
    if not args:
        return False
    inline = next((index for index, token in enumerate(args[:3]) if token in {"-c", "-e", "-command", "/c", "/k"}), None)
    if inline is not None:
        return bool(_INLINE_MUTATION_RE.search(" ".join(args[inline + 1:])))
    if "-m" in args:
        module_index = args.index("-m") + 1
        if module_index < len(args):
            module = args[module_index].lower()
            return module not in {"pytest", "unittest", "nose", "nose2"}
    script = next((token for token in args if not token.startswith("-") and "=" not in token), "")
    return _script_path_may_modify(script, root=root)


def _script_path_may_modify(script: str, *, root: str | Path | None) -> bool:
    if not script or _is_temp_script_path(script, root):
        return False
    lowered = script.replace("\\", "/").lower()
    name = Path(script).name.lower()
    if any(part in lowered.split("/") for part in _TARGET_PREFIXES):
        return False
    if re.search(r"(?:^|[-_])(test|tests|spec|verify|check|lint|smoke|probe)(?:[-_.]|$)", name):
        return False
    return Path(script).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".rb", ".pl", ".php", ".ps1"}


def _segment_may_modify_workspace(
    segment: _ShellSegment, *, canonical_commands: list[str], root: str | Path | None
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

    from agent.tool_dispatch_helpers import _is_destructive_command

    if _is_destructive_command(segment.raw or shlex.join(segment.tokens)):
        return True
    if not tokens:
        return False
    base = Path(tokens[0]).name.lower()
    if base in _PACKAGE_MANAGER_NAMES:
        if len(tokens) < 2:
            return False
        subcommand = tokens[1].lower()
        if subcommand in _PACKAGE_MANAGER_READS or subcommand in {"--version", "-v", "version"}:
            return False
        if subcommand in _SAFE_SCRIPT_RUNS:
            return False
        if subcommand == "run" and len(tokens) > 2:
            script_name = tokens[2].lower().split(":")[-1]
            return script_name not in _SAFE_SCRIPT_RUNS
        return subcommand not in {"help"}
    if base == "git":
        subcommand = _git_subcommand(tokens)
        return subcommand not in _READ_ONLY_GIT_COMMANDS
    if _script_invocation_may_modify(tokens, root=root):
        return True
    if Path(tokens[0]).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".rb", ".pl", ".php", ".ps1"}:
        return _script_path_may_modify(tokens[0], root=root)
    return False


def _git_subcommand(tokens: list[str]) -> str:
    """Find git's subcommand after global options that take values."""
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
        return token.lower()
    return ""


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
    *, command: str, cwd: str | Path | None, session_id: str | None, exit_code: int, output: str = ""
) -> Optional[dict[str, Any]]:
    """Record a foreground terminal result when it is verification evidence."""
    if not _ledger_enabled():
        return None
    evidence = classify_verification_command(command, cwd=cwd, session_id=session_id, exit_code=exit_code, output=output)
    return None if evidence is None else _insert_evidence(evidence)


def terminal_workspace_mutation_root(
    *, command: str, cwd: str | Path | None, exit_code: int, verification_complete: bool = True
) -> str | None:
    """Return the project root if a command may have changed it after its last verifier.

    The shell result is not a filesystem diff, so this is deliberately intent-based:
    recognized tests, lint, typechecks, and builds are read-only; package changes,
    formatting, file operations, and custom scripts can invalidate earlier evidence.
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
        return None

    canonical_commands = list(facts.get("verifyCommands") or [])
    verification_indexes = _verification_segment_indexes(
        segments, canonical_commands=canonical_commands, root=facts.get("root"), exit_code=int(exit_code),
    )
    mutating_indexes = [
        index for index, segment in enumerate(segments)
        if _segment_may_modify_workspace(segment, canonical_commands=canonical_commands, root=facts.get("root"))
    ]
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


def _insert_evidence(evidence: VerificationEvidence) -> dict[str, Any]:
    """Insert a classified evidence row and repoint the workspace state."""
    created_at = _utc_now()
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
        conn.execute(
            "INSERT INTO verification_state("
            " session_id, root, last_event_id, last_edit_at, changed_paths_json"
            ") VALUES (?, ?, ?, NULL, '[]')"
            " ON CONFLICT(session_id, root) DO UPDATE SET"
            " last_event_id = excluded.last_event_id,"
            " last_edit_at = NULL,"
            " changed_paths_json = '[]'",
            (e.session_id, e.root, event_id),
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
