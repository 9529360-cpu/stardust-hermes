"""Intent, execution, and read-only task projection contracts for Stardust.

The module is deliberately policy/read-model only. It does not run tools or own mutable
task state: current-session execution, background processes, delegation, and cron
remain authoritative in their existing runtimes.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal


# ``session_bound`` is the standing-goal durability: the goal text and its progress are saved with the
# conversation (state.db), but nothing advances it at process start. It continues only when that
# conversation is re-attached (``session.resume``), and only while the auto-continue checkpoint is
# fresh (``desktop.auto_continue.freshness_minutes``, default 15) and no wait barrier or turn budget
# stops it; otherwise the user's next message in that conversation continues it.
Durability = Literal["turn", "process_local", "restart_durable", "session_bound"]


class AssistantExecutionMode(str, Enum):
    RESPOND = "respond"
    EXECUTE_FOREGROUND = "execute_foreground"
    DELEGATE = "delegate"
    DELEGATE_BACKGROUND = "delegate_background"
    SCHEDULE_OR_WATCH = "schedule_or_watch"
    CLARIFY = "clarify"


class AssistantExecutionRail(str, Enum):
    """Existing runtime owner that carries executable work."""

    NONE = "none"
    CURRENT_SESSION = "current_session"
    PROCESS = "process"
    DELEGATION = "delegation"
    CRON = "cron"
    # Read-model only: a standing ``/goal`` advances inside its own conversation, so no execution mode maps to it.
    GOAL = "goal"


class AssistantTaskState(str, Enum):
    """Common user-facing lifecycle projected from authoritative runtime owners."""

    QUEUED = "queued"
    PAUSED = "paused"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_TASK_STATES = frozenset(
    {
        AssistantTaskState.COMPLETED,
        AssistantTaskState.FAILED,
        AssistantTaskState.CANCELLED,
        AssistantTaskState.INTERRUPTED,
    }
)

ATTENTION_TASK_STATES = frozenset(
    {
        AssistantTaskState.WAITING_FOR_USER,
        AssistantTaskState.BLOCKED,
        AssistantTaskState.FAILED,
        AssistantTaskState.INTERRUPTED,
    }
)

_DURABLE_RAILS = frozenset({AssistantExecutionRail.CRON})
_RAIL_DURABILITY: dict[AssistantExecutionRail, Durability] = {
    AssistantExecutionRail.CURRENT_SESSION: "turn",
    AssistantExecutionRail.PROCESS: "process_local",
    AssistantExecutionRail.DELEGATION: "process_local",
    AssistantExecutionRail.CRON: "restart_durable",
    AssistantExecutionRail.GOAL: "session_bound",
}
_RAILS_BY_MODE = {
    AssistantExecutionMode.RESPOND: frozenset({AssistantExecutionRail.NONE}),
    AssistantExecutionMode.EXECUTE_FOREGROUND: frozenset({AssistantExecutionRail.CURRENT_SESSION}),
    AssistantExecutionMode.DELEGATE: frozenset({AssistantExecutionRail.DELEGATION}),
    AssistantExecutionMode.DELEGATE_BACKGROUND: frozenset(
        {AssistantExecutionRail.PROCESS, AssistantExecutionRail.DELEGATION}
    ),
    AssistantExecutionMode.SCHEDULE_OR_WATCH: _DURABLE_RAILS,
    AssistantExecutionMode.CLARIFY: frozenset({AssistantExecutionRail.NONE}),
}

_DELEGATION_RUNNING_STATES = frozenset(
    {"dispatched", "pending", "queued", "running", "stalling", "finalizing"}
)
_DELEGATION_COMPLETED_STATES = frozenset(
    {"completed", "complete", "success", "succeeded", "ok", "done"}
)
_DELEGATION_FAILED_STATES = frozenset({"failed", "error", "rejected", "timeout", "stalled"})
_DELEGATION_CANCELLED_STATES = frozenset({"cancelled", "canceled"})
_DELEGATION_INTERRUPTED_STATES = frozenset({"interrupted", "unknown"})

_PROCESS_ACTIVE_STATES = frozenset({"running", "starting"})
_PROCESS_EXIT_STATES = frozenset({"exited", "already_exited", "completed"})

_CRON_QUEUED_STATES = frozenset({"scheduled", "queued"})
_CRON_FAILED_STATES = frozenset({"error", "failed"})
_CRON_RUN_ACTIVE_STATES = frozenset({"claimed", "running"})
# ``delivery_failed``: the run finished but its output never reached the user.
_CRON_LAST_RUN_FAILED = frozenset({"error", "failed", "delivery_failed"})
_CRON_JOB_PHASES = {
    AssistantTaskState.RUNNING: "running",
    AssistantTaskState.PAUSED: "paused",
    AssistantTaskState.COMPLETED: "completed",
    AssistantTaskState.FAILED: "error",
}
_GOAL_PHASES = {
    AssistantTaskState.BLOCKED: "blocked",
    AssistantTaskState.PAUSED: "paused",
    AssistantTaskState.COMPLETED: "done",
}

# ``phase`` is a short structured token the renderer localizes; the sets are deliberately open. These
# tables name only what this module produces today, and a consumer must keep a default branch.
_DELEGATION_PHASES = {
    **dict.fromkeys(("dispatched", "pending", "queued", "running"), "running"),
    "stalling": "stalling",
    "finalizing": "finalizing",
    **dict.fromkeys(_DELEGATION_COMPLETED_STATES, "completed"),
    **dict.fromkeys(("failed", "error", "rejected"), "failed"),
    "timeout": "timeout",
    "stalled": "stalled",
    **dict.fromkeys(_DELEGATION_CANCELLED_STATES, "cancelled"),
    "interrupted": "interrupted",
    "unknown": "unknown",
}

# Wire text bounds shared by the projections and the RPC row builder.
SUMMARY_LIMIT = 160
MAX_FILES = 8
_REDACT_INPUT_LIMIT = 8_000
_MAX_EPOCH_SECONDS = 4_102_444_800  # 2100-01-01: larger numbers are milliseconds or garbage
_WHITESPACE_RUN = re.compile(r"\s+")



@dataclass(frozen=True)
class AssistantExecutionContract:
    mode: AssistantExecutionMode
    durability: Durability
    background: bool
    description: str


EXECUTION_CONTRACTS = (
    AssistantExecutionContract(
        AssistantExecutionMode.RESPOND,
        "turn",
        False,
        "Answer, explain, brainstorm, review, or advise in the parent conversation without inventing an action task.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.EXECUTE_FOREGROUND,
        "turn",
        False,
        "Use the current authorized tool surface for bounded work that should complete while the user stays in the turn.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.DELEGATE,
        "process_local",
        False,
        "Use a scoped child worker for bounded coding, research, or multi-step work while the parent remains the orchestrator.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.DELEGATE_BACKGROUND,
        "process_local",
        True,
        "Run bounded work without holding the foreground, but never promise that process-local delegation survives restart.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.SCHEDULE_OR_WATCH,
        "restart_durable",
        True,
        "Use cron or another durable owner for future, recurring, monitored, or restart-surviving work.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.CLARIFY,
        "turn",
        False,
        "Ask only when a material user decision, authorization, recipient, or other safety-critical fact blocks progress; resolve missing secrets through secure local credential capture first when available.",
    ),
)


def contract_for(mode: AssistantExecutionMode) -> AssistantExecutionContract:
    return next(contract for contract in EXECUTION_CONTRACTS if contract.mode is mode)


def rails_for(mode: AssistantExecutionMode) -> frozenset[AssistantExecutionRail]:
    """Runtime owners that can truthfully implement one assistant execution mode."""

    return _RAILS_BY_MODE[mode]


def assistant_task_id(rail: AssistantExecutionRail, owner_id: Any) -> str:
    """Stable collision-free read-model id derived from an authoritative owner id."""

    if rail is AssistantExecutionRail.NONE:
        raise ValueError("assistant task ids require an execution rail")
    native = str(owner_id or "").strip()
    if not native:
        raise ValueError("assistant task ids require an owner id")
    return f"{rail.value}:{native}"


def assistant_cron_run_task_id(execution_id: Any) -> str:
    """Id of ONE cron execution attempt. ``cron:<job_id>`` names the job; the two never collide."""

    native = str(execution_id or "").strip()
    if not native:
        raise ValueError("assistant task ids require an owner id")
    return f"cron-run:{native}"


@dataclass(frozen=True)
class AssistantTaskProjection:
    """Read-only task metadata projected from an existing authoritative owner."""

    task_id: str
    title: str
    state: AssistantTaskState
    rail: AssistantExecutionRail
    durability: Durability
    background: bool
    owner_id: str
    parent_session_id: str | None = None
    child_ids: tuple[str, ...] = ()
    project_ref: str | None = None
    workspace: str | None = None
    requires_approval: bool = False
    approval_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    final_report: str | None = None
    detail: str = ""
    recoverable: bool = False
    recovery_action: str | None = None
    # Added for the task center. Times are Unix seconds; ``summary`` is already short and redacted;
    # ``files`` lists only files the work reported writing (never logs or summary paths).
    started_at: float | None = None
    updated_at: float | None = None
    next_run_at: float | None = None
    phase: str | None = None
    summary: str = ""
    parent_task_id: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("assistant task projections require a stable task id")
        if self.rail is AssistantExecutionRail.NONE:
            raise ValueError("assistant task projections require an authoritative execution rail")
        if not self.owner_id.strip():
            raise ValueError("assistant task projections require an owner id")
        expected = _RAIL_DURABILITY[self.rail]
        if self.durability != expected:
            raise ValueError(
                f"{self.rail.value} task projections require {expected} durability"
            )
        if self.rail is AssistantExecutionRail.CURRENT_SESSION and self.background:
            raise ValueError("current-session task projections cannot claim background execution")
        if self.rail in {
            AssistantExecutionRail.PROCESS,
            AssistantExecutionRail.CRON,
            AssistantExecutionRail.GOAL,
        } and not self.background:
            raise ValueError(f"{self.rail.value} task projections are background work")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_TASK_STATES

    @property
    def needs_attention(self) -> bool:
        return self.requires_approval or self.state in ATTENTION_TASK_STATES

    def to_wire(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "state": self.state.value,
            "rail": self.rail.value,
            "durability": self.durability,
            "background": self.background,
            "owner_id": self.owner_id,
            "parent_session_id": self.parent_session_id,
            "child_ids": list(self.child_ids),
            "project_ref": self.project_ref,
            "workspace": self.workspace,
            "requires_approval": self.requires_approval,
            "approval_refs": list(self.approval_refs),
            "artifact_refs": list(self.artifact_refs),
            "final_report": self.final_report,
            "detail": self.detail,
            "recoverable": self.recoverable,
            "recovery_action": self.recovery_action,
            "terminal": self.terminal,
            "needs_attention": self.needs_attention,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(records: tuple[Mapping[str, Any], ...], *keys: str) -> str:
    for record in records:
        for key in keys:
            value = _text(record.get(key))
            if value:
                return value
    return ""


def _flatten_refs(value: Any) -> tuple[str, ...]:
    values: list[str] = []

    def add(item: Any) -> None:
        if isinstance(item, str):
            text = item.strip()
            if text and text not in values:
                values.append(text)
        elif isinstance(item, Mapping):
            for nested in item.values():
                add(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                add(nested)

    add(value)
    return tuple(values)


def _refs_from(records: tuple[Mapping[str, Any], ...], *keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        for key in keys:
            for ref in _flatten_refs(record.get(key)):
                if ref not in values:
                    values.append(ref)
    return tuple(values)


def _child_refs(records: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    values = list(
        _refs_from(
            records,
            "child_session_id",
            "child_session_ids",
            "child_subagent_id",
            "child_subagent_ids",
        )
    )
    for record in records:
        rows = record.get("results")
        if not isinstance(rows, (list, tuple)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for ref in _refs_from(
                (row,),
                "child_session_id",
                "child_session_ids",
                "child_subagent_id",
                "child_subagent_ids",
            ):
                if ref not in values:
                    values.append(ref)
    return tuple(values)


def epoch_seconds(value: Any) -> float | None:
    """Unix seconds from a number or an ISO-8601 string (naive means local time).

    ``None`` for anything missing, non-finite or implausible, so a bad stored value drops one field
    instead of failing a whole projection."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            seconds = datetime.fromisoformat(value.strip()).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    else:
        return None
    return seconds if math.isfinite(seconds) and 0 < seconds < _MAX_EPOCH_SECONDS else None


def summarize_line(value: Any, limit: int) -> str:
    """One redacted line of at most ``limit`` characters (``…`` marks a cut).

    Redaction runs before the cut so a secret straddling the limit cannot leave a readable fragment."""

    if value is None:
        return ""
    from agent.redact import redact_sensitive_text
    from tools.ansi_strip import sanitize_display_text

    text = redact_sensitive_text(
        str(value)[:_REDACT_INPUT_LIMIT], force=True, redact_url_credentials=True
    )
    text = _WHITESPACE_RUN.sub(" ", sanitize_display_text(text)).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _first_epoch(records: tuple[Mapping[str, Any], ...], *keys: str) -> float | None:
    for record in records:
        for key in keys:
            seconds = epoch_seconds(record.get(key))
            if seconds is not None:
                return seconds
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _written_files(records: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    """Files the work reported writing: ``files_written`` / ``artifacts`` of each record and of every
    per-task row under ``results``. Log and summary paths are not files the user asked for."""

    rows: list[Mapping[str, Any]] = list(records)
    for record in records:
        nested = record.get("results")
        if isinstance(nested, (list, tuple)):
            rows.extend(row for row in nested if isinstance(row, Mapping))
    return _refs_from(tuple(rows), "files_written", "artifacts")[:MAX_FILES]


def _owner_state(value: Any, owner: str) -> str:
    state = _text(value).lower()
    if not state:
        raise ValueError(f"{owner} state is required")
    return state


def delegation_lifecycle_state(owner_state: Any) -> AssistantTaskState:
    state = _owner_state(owner_state, "delegation")
    if state in _DELEGATION_RUNNING_STATES:
        return AssistantTaskState.RUNNING
    if state in _DELEGATION_COMPLETED_STATES:
        return AssistantTaskState.COMPLETED
    if state in _DELEGATION_FAILED_STATES:
        return AssistantTaskState.FAILED
    if state in _DELEGATION_CANCELLED_STATES:
        return AssistantTaskState.CANCELLED
    if state in _DELEGATION_INTERRUPTED_STATES:
        return AssistantTaskState.INTERRUPTED
    raise ValueError(f"unknown delegation state: {state}")


def process_lifecycle_state(
    owner_state: Any, *, exit_code: Any = None, completion_reason: Any = None
) -> AssistantTaskState:
    state = _owner_state(owner_state, "process")
    reason = _text(completion_reason).lower()
    if state in _PROCESS_ACTIVE_STATES:
        return AssistantTaskState.RUNNING
    if reason == "killed":
        return AssistantTaskState.CANCELLED
    if reason == "lost":
        return AssistantTaskState.INTERRUPTED
    if reason == "failed_start":
        return AssistantTaskState.FAILED
    if state in _PROCESS_EXIT_STATES:
        if exit_code is None:
            return AssistantTaskState.INTERRUPTED
        try:
            code = int(exit_code)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid process exit code: {exit_code!r}") from exc
        return AssistantTaskState.COMPLETED if code == 0 else AssistantTaskState.FAILED
    raise ValueError(f"unknown process state: {state}")


def cron_job_lifecycle_state(
    owner_state: Any = None, *, enabled: bool = True, running: bool = False
) -> AssistantTaskState:
    if running:
        return AssistantTaskState.RUNNING
    state = _text(owner_state).lower() or ("scheduled" if enabled else "paused")
    if state in _CRON_QUEUED_STATES:
        return AssistantTaskState.QUEUED
    if state == "paused":
        return AssistantTaskState.PAUSED
    if state in {"running", "active"}:
        return AssistantTaskState.RUNNING
    if state == "completed":
        return AssistantTaskState.COMPLETED
    if state in _CRON_FAILED_STATES:
        return AssistantTaskState.FAILED
    raise ValueError(f"unknown cron state: {state}")


def cron_run_lifecycle_state(owner_state: Any, *, owner_lost: bool = False) -> AssistantTaskState:
    """One execution attempt from the cron ledger. ``owner_lost`` is the reader's proof that the
    process holding a claimed/running attempt is gone (the ledger row itself is left untouched)."""

    state = _owner_state(owner_state, "cron run")
    if state in _CRON_RUN_ACTIVE_STATES:
        return AssistantTaskState.INTERRUPTED if owner_lost else AssistantTaskState.RUNNING
    if state == "completed":
        return AssistantTaskState.COMPLETED
    if state == "failed":
        return AssistantTaskState.FAILED
    if state == "unknown":
        return AssistantTaskState.INTERRUPTED
    raise ValueError(f"unknown cron run state: {state}")


def goal_lifecycle_state(status: Any, *, last_verdict: Any = None) -> AssistantTaskState:
    """A standing goal. The judge's ``blocked`` verdict pauses the goal, so blocked is read from the
    verdict, not from ``status``. ``cleared`` goals are not tasks and are rejected."""

    state = _owner_state(status, "goal")
    blocked = _text(last_verdict).lower() == "blocked"
    if state == "done":
        return AssistantTaskState.COMPLETED
    if state in {"active", "paused"}:
        if blocked:
            return AssistantTaskState.BLOCKED
        return AssistantTaskState.RUNNING if state == "active" else AssistantTaskState.PAUSED
    raise ValueError(f"unknown goal state: {state}")


def project_delegation(record: Mapping[str, Any]) -> AssistantTaskProjection:
    """Project live, completion-event, or durable-ledger delegation data."""

    task = _mapping(record.get("task"))
    event = _mapping(record.get("event"))
    result = _mapping(record.get("result"))
    records = (record, event, result, task)

    owner_id = _first_text(records, "delegation_id")
    owner_state = _first_text((event, record), "status", "state")
    known_states = (
        _DELEGATION_RUNNING_STATES | _DELEGATION_COMPLETED_STATES | _DELEGATION_FAILED_STATES
        | _DELEGATION_CANCELLED_STATES | _DELEGATION_INTERRUPTED_STATES
    )
    if owner_state.lower() == "unknown" or owner_state.lower() not in known_states:
        owner_state = "interrupted"  # forward/owner-loss states stay compatible at the read boundary
    state = delegation_lifecycle_state(owner_state)
    goal = _first_text(records, "goal")
    if not goal:
        goals = record.get("goals") or event.get("goals") or task.get("goals")
        if isinstance(goals, (list, tuple)):
            labels = [_text(item) for item in goals if _text(item)]
            if len(labels) == 1:
                goal = labels[0]
            elif labels:
                goal = f"{len(labels)} delegated tasks"

    final_report = _first_text((event, result, record), "final_report", "summary") or None
    detail = _first_text((event, result, record), "error", "exit_reason", "detail")
    artifacts = _refs_from(
        (result, event, record),
        "artifacts",
        "artifact_refs",
        "files_written",
        "live_transcripts",
        "summary_path",
        "full_summary_path",
    )
    interrupted = state is AssistantTaskState.INTERRUPTED
    started_at = _first_epoch((record, event, task), "dispatched_at")

    return AssistantTaskProjection(
        task_id=assistant_task_id(AssistantExecutionRail.DELEGATION, owner_id),
        title=goal or "Delegated work",
        state=state,
        rail=AssistantExecutionRail.DELEGATION,
        durability="process_local",
        background=True,
        owner_id=owner_id,
        parent_session_id=_first_text(records, "parent_session_id") or None,
        child_ids=_child_refs(records),
        project_ref=_first_text(records, "project_id", "project_ref") or None,
        workspace=_first_text(records, "workspace_path", "workdir", "cwd") or None,
        requires_approval=bool(
            record.get("requires_approval")
            or event.get("requires_approval")
            or result.get("requires_approval")
        ),
        approval_refs=_refs_from(records, "approval_id", "approval_ids", "approval_refs"),
        artifact_refs=artifacts,
        final_report=final_report,
        detail=detail,
        recoverable=interrupted,
        recovery_action="retry" if interrupted else None,
        started_at=started_at,
        updated_at=_first_epoch((record, event, result), "updated_at", "completed_at") or started_at,
        phase=_DELEGATION_PHASES.get(owner_state.lower()),
        summary=summarize_line(final_report, SUMMARY_LIMIT),
        files=_written_files((result, event, record)),
    )


def project_process(record: Mapping[str, Any]) -> AssistantTaskProjection:
    owner_id = _text(record.get("session_id"))
    state = process_lifecycle_state(
        record.get("status"),
        exit_code=record.get("exit_code"),
        completion_reason=record.get("completion_reason"),
    )
    interrupted = state is AssistantTaskState.INTERRUPTED

    return AssistantTaskProjection(
        task_id=assistant_task_id(AssistantExecutionRail.PROCESS, owner_id),
        title=_text(record.get("command")) or "Background process",
        state=state,
        rail=AssistantExecutionRail.PROCESS,
        durability="process_local",
        background=True,
        owner_id=owner_id,
        parent_session_id=_text(record.get("parent_session_id")) or None,
        project_ref=_text(record.get("project_id") or record.get("project_ref")) or None,
        workspace=_text(record.get("cwd")) or None,
        requires_approval=bool(record.get("requires_approval")),
        approval_refs=_refs_from((record,), "approval_id", "approval_ids", "approval_refs"),
        artifact_refs=_refs_from((record,), "artifacts", "artifact_refs", "files_written"),
        final_report=_text(record.get("final_report")) or None,
        detail=_first_text(
            (record,),
            "handoff_note",
            "completion_reason",
            "termination_source",
        ),
        recoverable=interrupted,
        recovery_action="retry" if interrupted else None,
    )


def project_cron_job(
    job: Mapping[str, Any], *, running: bool = False
) -> AssistantTaskProjection:
    owner_id = _text(job.get("job_id") or job.get("id"))
    state = cron_job_lifecycle_state(
        job.get("state"),
        enabled=bool(job.get("enabled", True)),
        running=running,
    )
    failed = state is AssistantTaskState.FAILED
    last_failed = _text(job.get("last_status")).lower() in _CRON_LAST_RUN_FAILED
    final_report = _first_text((job,), "final_report", "last_report") or None
    started_at = epoch_seconds(job.get("created_at"))

    return AssistantTaskProjection(
        task_id=assistant_task_id(AssistantExecutionRail.CRON, owner_id),
        title=_text(job.get("name")) or "Scheduled work",
        state=state,
        rail=AssistantExecutionRail.CRON,
        durability="restart_durable",
        background=True,
        owner_id=owner_id,
        parent_session_id=_text(job.get("parent_session_id") or job.get("origin_session_id")) or None,
        project_ref=_text(job.get("project_id") or job.get("project_ref")) or None,
        workspace=_text(job.get("workdir")) or None,
        requires_approval=bool(job.get("requires_approval")),
        approval_refs=_refs_from((job,), "approval_id", "approval_ids", "approval_refs"),
        artifact_refs=_refs_from((job,), "artifacts", "artifact_refs"),
        final_report=final_report,
        detail=_first_text(
            (job,),
            "last_error",
            "last_fire_error",
            "last_delivery_error",
            "paused_reason",
            "next_run_at",
        ),
        recoverable=failed,
        recovery_action="review_or_retry" if failed else None,
        started_at=started_at,
        updated_at=epoch_seconds(job.get("last_run_at")) or started_at,
        next_run_at=epoch_seconds(job.get("next_run_at")),
        phase=_CRON_JOB_PHASES.get(state) or ("last_failed" if last_failed else "scheduled"),
        summary=summarize_line(final_report, SUMMARY_LIMIT),
    )


def project_cron_run(
    execution: Mapping[str, Any], *, job_name: str = "", owner_lost: bool = False
) -> AssistantTaskProjection:
    """One execution attempt from the cron ledger, as its own row under its job.

    ``owner_lost`` is the reader's in-memory proof that a claimed/running attempt's process is gone;
    the ledger row is never rewritten by a read."""

    execution_id = _text(execution.get("id"))
    job_id = _text(execution.get("job_id"))
    status = _text(execution.get("status")).lower()
    state = cron_run_lifecycle_state(status, owner_lost=owner_lost)
    claimed_at = epoch_seconds(execution.get("claimed_at"))
    started_at = epoch_seconds(execution.get("started_at")) or claimed_at
    in_flight = status in _CRON_RUN_ACTIVE_STATES

    return AssistantTaskProjection(
        task_id=assistant_cron_run_task_id(execution_id),
        title=_text(job_name) or f"Cron job {job_id or 'run'}",
        state=state,
        rail=AssistantExecutionRail.CRON,
        durability="restart_durable",
        background=True,
        owner_id=execution_id,
        detail=_text(execution.get("error")),
        started_at=started_at,
        updated_at=epoch_seconds(execution.get("finished_at")) or started_at,
        phase="unknown" if in_flight and owner_lost else status,
        parent_task_id=assistant_task_id(AssistantExecutionRail.CRON, job_id) if job_id else None,
    )


def project_goal(
    session_id: str, goal: Mapping[str, Any], *, waiting: bool = False
) -> AssistantTaskProjection:
    """A standing ``/goal``. ``goal`` is the stored ``GoalState`` as a mapping; ``waiting`` is the
    reader's verdict that a wait barrier is still holding the goal (the stored barrier is lazily cleared
    by the goal loop, so the reader decides)."""

    owner_id = _text(session_id)
    state = goal_lifecycle_state(goal.get("status"), last_verdict=goal.get("last_verdict"))
    if waiting and state is AssistantTaskState.RUNNING:
        state = AssistantTaskState.QUEUED
    phase = _GOAL_PHASES.get(state) or ("waiting" if waiting else "active")
    created_at = epoch_seconds(goal.get("created_at"))
    recoverable = state in {AssistantTaskState.PAUSED, AssistantTaskState.BLOCKED}

    return AssistantTaskProjection(
        task_id=assistant_task_id(AssistantExecutionRail.GOAL, owner_id),
        title=next((line.strip() for line in _text(goal.get("goal")).splitlines() if line.strip()), "")
        or "Standing goal",
        state=state,
        rail=AssistantExecutionRail.GOAL,
        durability="session_bound",
        background=True,
        owner_id=owner_id,
        parent_session_id=owner_id or None,
        detail=_first_text((goal,), "paused_reason", "waiting_reason"),
        recoverable=recoverable,
        recovery_action="resume" if recoverable else None,
        started_at=created_at,
        updated_at=epoch_seconds(goal.get("last_turn_at")) or created_at,
        phase=phase,
        summary=summarize_line(goal.get("last_reason"), SUMMARY_LIMIT),
        progress_current=_count(goal.get("turns_used")),
        progress_total=_count(goal.get("max_turns")),
    )


ASSISTANT_EXECUTION_GUIDANCE = (
    "Choose the smallest execution mode that satisfies the user's intent: `respond` for conversation, "
    "explanation, brainstorming, review, and advice; `execute_foreground` for authorized work on the current tool "
    "surface, including coding and multi-step work, so the user can follow it; `delegate` only when the user asks "
    "for helpers or independent pieces genuinely run in parallel, relaying each helper's progress and result in "
    "the conversation; `delegate_background` for process-local work that can run without holding the "
    "foreground, never as a promise of restart durability; `schedule_or_watch` for future, recurring, monitored, "
    "or restart-surviving work through cron or another durable owner; and `clarify` only when a material "
    "user decision, authorization, recipient, or safety-critical fact is genuinely missing. Missing passwords, "
    "payment details, OTP seeds, or API secrets should use secure local credential capture or Vault resolution "
    "before chat clarification whenever that path exists. "
    + "Background work must remain observable and must not steal focus."
)
