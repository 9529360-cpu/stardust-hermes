"""Single source of truth for the agent working directory.

`TERMINAL_CWD` is the runtime carrier for the configured working directory (`terminal.cwd`
is bridged to it once at gateway/cron startup; the local CLI leaves it unset and relies on
the launch dir). Reading it in one place keeps the system prompt, tool surfaces, and
context-file discovery agreeing on where the agent lives. Multi-session gateways can pin a
logical cwd via `_SESSION_CWD`.
"""

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UNSET: Any = object()

_SESSION_CWD: ContextVar = ContextVar("STARDUST_SESSION_CWD", default=_UNSET)

# The package/source root (<root>/agent/runtime_cwd.py). A backend launched from or
# self-spawned into this tree (desktop default) must never let an os.getcwd() fallback
# inject this repo's contributor AGENTS.md as project context.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _is_install_tree(p: Path) -> bool:
    """True only when ``p`` IS the package root or sits inside it — ancestors
    (a home dir containing the checkout) are legitimate workspaces."""
    try:
        p = p.resolve()
    except Exception:
        return False
    return p == _PACKAGE_ROOT or _PACKAGE_ROOT in p.parents


def set_session_cwd(cwd: str | None) -> Token:
    """Pin the logical cwd for the current context."""
    return _SESSION_CWD.set((cwd or "").strip())


def clear_session_cwd() -> None:
    _SESSION_CWD.set("")


def scope_terminal_cwd() -> str:
    """Scope-aware TERMINAL_CWD value (may be empty) — every cwd consumer reads through this.

    Under gateway multiplexing the per-turn terminal scope carries the active profile's cwd;
    the process-global env var may hold another profile's. Only an ImportError falls back: an
    active refusal scope must raise, not silently resolve the launch profile's cwd.
    """
    try:
        from tools.terminal_scope import terminal_env
    except ImportError:
        return os.environ.get("TERMINAL_CWD", "")
    override = _SESSION_CWD.get()
    if override is not _UNSET and override:
        # Still enforce a refusing terminal scope before returning a session path.
        terminal_env("TERMINAL_CWD", "")
        return str(override)
    return terminal_env("TERMINAL_CWD", "")


def initial_session_cwd() -> str:
    """Capture launch cwd once when session_first is enabled.

    A bound gateway/cron/ACP scope already represents an explicit session choice.
    Remote backends retain their backend namespace rather than acquiring a host cwd.
    """
    from tools.terminal_scope import get_terminal_scope, terminal_env
    override = _SESSION_CWD.get()
    if override is not _UNSET and override:
        return str(override)
    if get_terminal_scope() is not None:
        return terminal_env("TERMINAL_CWD", "")
    from hermes_cli.config_effective import load_user_config_effective
    terminal = load_user_config_effective().get("terminal") or {}
    configured = str(terminal.get("cwd") or "").strip()
    if configured and configured not in {".", "./", "auto", "cwd"}:
        return os.path.expanduser(configured)
    if terminal_env("TERMINAL_ENV", "local") != "local":
        return terminal_env("TERMINAL_CWD", "")
    if terminal.get("cwd_env_policy", "env_first") != "session_first":
        return terminal_env("TERMINAL_CWD", "") or os.getcwd()
    return os.getcwd()


@contextmanager
def conversation_cwd(agent, task_id):
    """Bind prompt and tools to the same session cwd, restoring nested callers."""
    from tools.terminal_tool import get_session_cwd, record_session_cwd, clear_session_cwd
    session_id = getattr(agent, "session_id", None)
    if task_id is None:
        task_id = session_id or "default"
    cwd = (get_session_cwd(task_id) if task_id else None) or get_session_cwd(session_id)
    if not cwd:
        cwd = getattr(agent, "session_cwd", None) or getattr(agent, "_initial_session_cwd", None)
    if cwd is None:
        cwd = initial_session_cwd()
        agent._initial_session_cwd = cwd
    record_session_cwd(session_id, cwd)
    record_session_cwd(task_id, cwd)
    token = set_session_cwd(cwd)
    try:
        yield
    finally:
        record_session_cwd(session_id, get_session_cwd(task_id))
        if session_id and task_id != session_id:
            clear_session_cwd(task_id)
        _SESSION_CWD.reset(token)


def _existing_dir(raw: str, label: str) -> Path | None:
    p = Path(raw).expanduser()
    if p.is_dir():
        return p
    logger.warning("%s does not exist: %s", label, raw)
    return None


def _resolve_configured_cwd(*, override_is_final: bool) -> Path | None:
    """Session override, then TERMINAL_CWD; each validated as a real directory.

    ``override_is_final``: a set-but-missing session override yields None
    instead of falling through to TERMINAL_CWD.
    """
    override = _SESSION_CWD.get()
    override = "" if override is _UNSET else str(override).strip()
    if override:
        p = _existing_dir(override, "configured working directory")
        if p is not None or override_is_final:
            return p
    raw = scope_terminal_cwd().strip()
    return _existing_dir(raw, "TERMINAL_CWD") if raw else None


def resolve_agent_cwd() -> Path:
    """Configured cwd, else the launch dir (os.getcwd()'s OSError on a deleted cwd deliberately propagates)."""
    return _resolve_configured_cwd(override_is_final=False) or Path(os.getcwd())


def resolve_context_cwd() -> Path | None:
    """Configured cwd for context-file discovery, or None (build_context_files_prompt then falls back to the
    launch dir). An existing configured path is honored verbatim — including the Hermes source tree, a
    legitimate workspace when developing Hermes; fallback-directory policy lives in the caller."""
    return _resolve_configured_cwd(override_is_final=True)
