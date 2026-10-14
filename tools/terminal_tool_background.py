"""Background-process launch path: ``terminal(background=true)`` decides the
completion / watch-pattern notification flags, spawns a tracked process carrying
them via the process registry (Popen locally, ``env.execute`` in a sandbox; the
registry adds the gateway chat's routing), registers the gateway completion watcher
and returns the JSON result. Lazy ``tools.terminal_tool`` imports keep the origin's
monkeypatch points authoritative.
"""

import json
import logging
from typing import Any, List, Optional

logger = logging.getLogger("tools.terminal_tool")

# A silent background process (no notify_on_complete / watch_patterns) is right
# only for servers/watchers; for bounded tasks the agent almost always wanted a
# notification and forgot the flag, so nudge it (cheap false positive).
_SILENT_BACKGROUND_HINT = (
    'background=true without notify_on_complete=true means this process runs SILENTLY — you '
    'will not be told when it exits. If this is a bounded task (test suite, build, CI poller, '
    'deploy, anything with a defined end), you almost certainly wanted notify_on_complete=true '
    'so the system pings you on exit. Re-launch with notify_on_complete=true, or call '
    "process(action='poll') / process(action='wait') yourself to learn the outcome. Only "
    'ignore this hint for genuine long-lived processes that never exit (servers, watchers, '
    'daemons).'
)

# Homebrewed CI pollers built on `gh pr view --json statusCheckRollup` or
# `gh pr checks | jq` fail silently in known ways (block-buffered stdout never
# reaches capture, jq null-key edge cases exit the loop, conclusion-vs-status
# confusion declares all-green early, TTY-only banners never appear piped).
# Detector is deliberately narrow: the canonical column-2 awk poller is fine.
_HOMEBREW_CI_POLLER_HINT = (
    'This looks like a homebrewed CI poller built from `gh pr view --json statusCheckRollup` '
    'and/or `gh pr checks | jq`. That shape has burned us repeatedly in hermes-agent dev work '
    '(PRs #31329, #31448, #31695, #31709, #31745, #32264, #33131) — stdout buffering kills '
    'output capture, jq null-key edge cases silently exit the loop, conclusion-vs-status field '
    'confusion exits early with bogus all-green verdicts, TTY-only summary banners never '
    'appear when piped. Use the canonical snippets in the green-ci-policy skill instead: the '
    'exit-code-driven `gh pr checks $PR >/dev/null` (rc 0 = green, 8 = pending, else fail) for '
    'exit-on-first-fail behavior, or the column-2 awk-on-tabs poller (`awk -F"\\t" '
    '"$2==\\"pending\\""`) for sharded matrices. Load '
    "skill_view(name='github/hermes-agent-dev', file_path='references/green-ci-policy.md') for "
    'the verbatim snippets. If you must roll a custom loop with rich structured output, write '
    "each tick to a known file (`tee -a /tmp/ci.log`) and rely on `process(action='log')` to "
    'read THAT file — do not rely on background-process stdout capture for line-buffered shell '
    'loops.'
)

_ASYNC_UNSUPPORTED_NOTE = (
    'notify_on_complete / watch_patterns are not available in this session — it cannot receive '
    'an async completion after the turn ends (a one-shot runner such as `stardust -z`, a cron '
    'job, a one-shot worker, or a stateless HTTP endpoint). The process is running in the '
    "background; retrieve its result with process(action='poll') or process(action='wait')."
)


def _looks_like_homebrew_ci_poller(command: str) -> bool:
    has_gh = "gh pr view" in command or "gh pr checks" in command
    has_jq = " jq " in command or "| jq" in command or "$(jq" in command
    # `gh pr checks` doesn't emit JSON, so piping it to jq is confused intent.
    return "statusCheckRollup" in command or (has_gh and has_jq)


def _spawn(process_registry, *, env, env_type, command, cwd, effective_task_id, task_id,
           session_key, effective_pty, notify_on_complete, watch_patterns, verification_session_id=""):
    common = dict(command=command, cwd=cwd, task_id=effective_task_id,
                  owner_task_id=task_id or effective_task_id, session_key=session_key,
                  notify_on_complete=notify_on_complete, watch_patterns=watch_patterns,
                  verification_session_id=verification_session_id)
    if env_type == "local":
        return process_registry.spawn_local(
            env_vars=env.env if hasattr(env, 'env') else None, use_pty=effective_pty, **common)
    return process_registry.spawn_via_env(env=env, **common)


def _async_deliverable_flags(notify_on_complete, watch_patterns):
    """Finite sessions (stateless HTTP, one-shot Kanban workers) can't route a
    completion back after the turn ends: drop the flags so the agent is told to
    poll. Returns (notify, watch_patterns, dropped)."""
    if not (notify_on_complete or watch_patterns):
        return notify_on_complete, watch_patterns, False
    from gateway.session_context import async_delivery_supported

    if async_delivery_supported():
        return notify_on_complete, watch_patterns, False
    return False, None, True


def _register_completion_watcher(process_registry, proc_session, session_key) -> None:
    """Gateway mode: register a fast watcher so completion triggers a new
    agent turn (CLI mode uses the completion_queue directly). The session was
    created with the chat route and watcher interval (``_new_session``)."""
    from tools.process_registry import _WATCHER_ROUTE_KEYS

    with process_registry._lock:
        process_registry.pending_watchers.append({
            "session_id": proc_session.id, "owner_home": proc_session.owner_home,
            "check_interval": proc_session.watcher_interval, "session_key": session_key,
            **{key: getattr(proc_session, f"watcher_{key}") for key in _WATCHER_ROUTE_KEYS},
            "notify_on_complete": True, "parent_session_id": proc_session.parent_session_id,
        })


def spawn_background_process(
    *, command: str, env: Any, env_type: str, effective_task_id: str, task_id: Optional[str],
    session_key: str, session_id: Optional[str] = None, workdir: Optional[str], cwd: str, effective_pty: bool,
    notify_on_complete: bool, watch_patterns: Optional[List[str]], approval_note: Optional[str],
    pty_disabled_reason: Optional[str],
) -> str:
    """Spawn *command* as a tracked background process and return the JSON result.

    Never inline-polls ``is_interrupted()``: the spawn detaches and returns
    exit_code 0 immediately, so the stale-interrupt kill cannot occur here.
    """
    from tools.process_registry import process_registry
    from tools.terminal_tool import (
        _redact_terminal_error_text, _resolve_command_cwd, _resolve_notification_flag_conflict,
    )

    effective_cwd = _resolve_command_cwd(
        workdir=workdir, default_cwd=cwd, session_key=session_key, env_type=env_type,
    )
    try:
        # Decided before the spawn (none of it depends on the process): the registry's reader
        # may see the child exit or print a match before _spawn even returns.
        notify, watch, async_dropped = _async_deliverable_flags(notify_on_complete, watch_patterns)
        watch, conflict_note = _resolve_notification_flag_conflict(
            notify_on_complete=bool(notify), watch_patterns=watch, background=True,
        )
        proc_session = _spawn(
            process_registry, env=env, env_type=env_type, command=command, cwd=effective_cwd,
            effective_task_id=effective_task_id, task_id=task_id, session_key=session_key,
            effective_pty=effective_pty, notify_on_complete=bool(notify), watch_patterns=watch,
            verification_session_id=session_id or task_id or effective_task_id,
        )
        result_data = {"output": "Background process started", "session_id": proc_session.id,
                       "pid": proc_session.pid, "exit_code": 0, "error": None}
        mutation = None
        with process_registry._lock:
            if proc_session.exited:
                process_registry._record_workspace_mutation_on_exit(proc_session)
            elif proc_session.completion_reason != "failed_start":
                try:
                    from agent.verification_evidence import record_terminal_workspace_mutation
                    mutation = record_terminal_workspace_mutation(
                        command=command, cwd=effective_cwd,
                        session_id=session_id or task_id or effective_task_id, exit_code=0,
                        verification_complete=False,
                    )
                except Exception:
                    logger.debug("background workspace mutation recording failed", exc_info=True)
        if mutation is None and proc_session.workspace_mutation_root:
            mutation = {"root": proc_session.workspace_mutation_root}
        if mutation:
            proc_session.workspace_mutation_root = mutation["root"]
            result_data["workspace_mutation"] = mutation
        if approval_note:
            result_data["approval"] = approval_note
        if pty_disabled_reason:
            result_data["pty_note"] = pty_disabled_reason
        if not notify_on_complete and not watch_patterns:
            result_data["hint"] = _SILENT_BACKGROUND_HINT
        if command and _looks_like_homebrew_ci_poller(command):
            existing = result_data.get("hint", "")
            result_data["hint"] = (existing + "\n\n" + _HOMEBREW_CI_POLLER_HINT if existing
                                   else _HOMEBREW_CI_POLLER_HINT)

        if async_dropped:
            result_data["notify_on_complete"] = False
            result_data["notify_unsupported"] = _ASYNC_UNSUPPORTED_NOTE
            logger.info("background proc %s: async delivery unsupported on this "
                        "session; notify_on_complete/watch_patterns disabled", proc_session.id)
        if conflict_note:
            logger.warning("background proc %s: %s", proc_session.id, conflict_note)
            result_data["watch_patterns_ignored"] = conflict_note
        if notify:
            result_data["notify_on_complete"] = True
            if proc_session.watcher_platform:
                _register_completion_watcher(process_registry, proc_session, session_key)
            from agent.delegation_context import is_delegated_child_context
            if is_delegated_child_context():
                result_data["notify_on_complete"] = False
                result_data["subagent_note"] = _SUBAGENT_NOTIFY_NOTE
        else:
            from gateway.session_context import get_session_env
            if get_session_env("STARDUST_SINGLE_QUERY_SESSION") == "1":
                result_data["finite_session_note"] = _FINITE_SESSION_PROCESS_NOTE
        if watch:
            result_data["watch_patterns"] = list(watch)
        return json.dumps(result_data, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "output": "", "exit_code": -1,
            "error": _redact_terminal_error_text(f"Failed to start background process: {e}"),
        }, ensure_ascii=False)


_SUBAGENT_NOTIFY_NOTE = (
    "You are a subagent: this process's completion notice will NOT reach your parent, and the process is killed when "
    "you finish. Before you finish, either wait for it (process_manage wait), kill it, or hand it to your parent with "
    "process_manage(action='handoff', session_id=..., data='<purpose>') so the parent receives its completion. For CI "
    "watchers prefer returning the fact (PR number, SHA) and letting the parent watch."
)

_FINITE_SESSION_PROCESS_NOTE = (
    "This is a one-shot run (e.g. a one-shot worker): it ends after your final answer and stops the background "
    "processes it started. Use this one for checks within the run; never tell the user a server or watcher was "
    "left running — tell them how to start it themselves if they need it afterwards."
)

_YIELDED_NOTE = (
    "The user sent a message while this command was running, so it was moved to the "
    "background WITHOUT being killed and is still running. You will be notified when it "
    "exits (notify_on_complete). Read the user's message and respond to it now; use "
    "process(action='poll'|'wait'|'log', session_id=...) to check on this command."
)


def yield_to_background_handler(
    *, command: str, env_type: str, cwd: Optional[str], effective_task_id: str,
    task_id: Optional[str], session_key: str, session_id: Optional[str] = None,
):
    """Build the ``yield_handler`` a foreground ``env.execute`` calls when the tool thread is
    asked to yield (a user message arrived mid-command). Local backend only: the live Popen
    is adopted by the process registry as a notify-on-complete background session and the
    partial output is returned to the model right away. Other backends return None (no
    adoptable host process) and the foreground wait continues."""
    if env_type != "local":
        return None

    def _handler(proc, output_so_far: str) -> dict:
        from gateway.session_context import async_delivery_supported
        from tools.process_registry import process_registry

        # Decided before adoption, like a notify_on_complete spawn: the registry's reader
        # may see the child exit before adopt_local returns.
        session = process_registry.adopt_local(
            proc, command=command, cwd=cwd, task_id=effective_task_id,
            owner_task_id=task_id or effective_task_id, session_key=session_key,
            output_so_far=output_so_far, notify_on_complete=async_delivery_supported(),
            verification_session_id=session_id or task_id or effective_task_id)
        if session.notify_on_complete and session.watcher_platform:
            _register_completion_watcher(process_registry, session, session_key)
        logger.info("foreground command yielded to background as %s (pid %s)", session.id, session.pid)
        return {
            "output": output_so_far, "returncode": None, "yielded_session_id": session.id, "pid": session.pid,
        }
    return _handler
