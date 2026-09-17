"""Stable intent/execution vocabulary for the Stardust personal-assistant control plane.

This module is deliberately policy/read-model only: it does not run tools, mutate
sessions, or own task state. The live agent remains the decision maker and existing
runtime owners remain authoritative; gateway/Desktop callers can project those owners
into one stable vocabulary without creating a second task database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class AssistantIntent(str, Enum):
    """What the user's turn principally asks Stardust to do."""

    RESPOND = "respond"
    EXECUTE = "execute"
    DELEGATE = "delegate"
    BACKGROUND = "background"
    SCHEDULE = "schedule"
    CLARIFY = "clarify"


class ExecutionDurability(str, Enum):
    """How long the selected work rail is expected to survive."""

    TURN = "turn"
    PROCESS = "process"
    RESTART_SAFE = "restart_safe"


class ExecutionRail(str, Enum):
    """Existing runtime owner that should carry executable work."""

    NONE = "none"
    CURRENT_SESSION = "current_session"
    PROCESS = "process"
    DELEGATION = "delegation"
    CRON = "cron"
    KANBAN = "kanban"


class TaskLifecycleState(str, Enum):
    """Common read-model state for assistant work owned by existing runtimes."""

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
        TaskLifecycleState.COMPLETED,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.INTERRUPTED,
    }
)

ATTENTION_TASK_STATES = frozenset(
    {
        TaskLifecycleState.WAITING_FOR_USER,
        TaskLifecycleState.BLOCKED,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.INTERRUPTED,
    }
)

_DURABLE_RAILS = frozenset({ExecutionRail.CRON, ExecutionRail.KANBAN})

_DELEGATION_RUNNING_STATES = frozenset({"dispatched", "pending", "queued", "running", "stalling", "finalizing"})
_DELEGATION_COMPLETED_STATES = frozenset({"completed", "complete", "success", "succeeded", "ok", "done"})
_DELEGATION_FAILED_STATES = frozenset({"failed", "error", "rejected", "timeout", "stalled"})
_DELEGATION_CANCELLED_STATES = frozenset({"cancelled", "canceled"})
_DELEGATION_INTERRUPTED_STATES = frozenset({"interrupted", "unknown"})

_KANBAN_QUEUED_STATES = frozenset({"triage", "todo", "scheduled", "ready"})
_KANBAN_RUNNING_STATES = frozenset({"running", "review"})
_KANBAN_COMPLETED_STATES = frozenset({"done", "archived"})

_PROCESS_ACTIVE_STATES = frozenset({"running", "starting"})
_PROCESS_EXIT_STATES = frozenset({"exited", "already_exited", "completed"})


@dataclass(frozen=True)
class AssistantExecutionDecision:
    """Serializable decision metadata; execution remains owned by existing rails."""

    intent: AssistantIntent
    durability: ExecutionDurability
    rail: ExecutionRail = ExecutionRail.NONE
    reason: str = ""
    requires_approval: bool = False
    task_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.intent is AssistantIntent.SCHEDULE and self.durability is not ExecutionDurability.RESTART_SAFE:
            raise ValueError("scheduled work must use a restart-safe execution rail")
        if self.intent is AssistantIntent.BACKGROUND and self.durability is ExecutionDurability.TURN:
            raise ValueError("background work cannot be turn-scoped")
        if self.intent in {AssistantIntent.RESPOND, AssistantIntent.CLARIFY}:
            if self.task_id is not None:
                raise ValueError("non-execution decisions cannot own a task id")
            if self.rail is not ExecutionRail.NONE:
                raise ValueError("non-execution decisions cannot select an execution rail")
        if self.intent is AssistantIntent.SCHEDULE and self.rail not in _DURABLE_RAILS:
            raise ValueError("scheduled work must select a durable execution rail")

    def to_wire(self) -> dict[str, Any]:
        """JSON-safe decision metadata for gateway/telemetry boundaries."""

        return {
            "intent": self.intent.value,
            "durability": self.durability.value,
            "rail": self.rail.value,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "task_id": self.task_id,
        }


@dataclass(frozen=True)
class AssistantTaskProjection:
    """Read-only task metadata projected from an existing authoritative owner.

    ``owner_id`` is the native delegation/job/card/process identifier. This object
    deliberately stores no mutable execution state: reconciliation always returns
    to the real owner.
    """

    task_id: str
    title: str
    state: TaskLifecycleState
    rail: ExecutionRail
    durability: ExecutionDurability
    parent_session_id: Optional[str] = None
    owner_id: Optional[str] = None
    detail: str = ""
    requires_approval: bool = False
    recoverable: bool = False
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("assistant task projections require a stable task id")
        if self.rail is ExecutionRail.NONE:
            raise ValueError("assistant task projections require an authoritative execution rail")
        if self.durability is ExecutionDurability.RESTART_SAFE and self.rail not in _DURABLE_RAILS:
            raise ValueError("restart-safe task projections require cron or kanban ownership")
        if self.rail in _DURABLE_RAILS and self.durability is not ExecutionDurability.RESTART_SAFE:
            raise ValueError("cron/kanban task projections must be restart-safe")

    @property
    def terminal(self) -> bool:
        return task_state_is_terminal(self.state)

    @property
    def needs_attention(self) -> bool:
        return task_state_needs_attention(self.state) or self.requires_approval

    def to_wire(self) -> dict[str, Any]:
        """Stable JSON-safe read model for gateway/Desktop projection."""

        return {
            "task_id": self.task_id,
            "title": self.title,
            "state": self.state.value,
            "rail": self.rail.value,
            "durability": self.durability.value,
            "parent_session_id": self.parent_session_id,
            "owner_id": self.owner_id,
            "detail": self.detail,
            "requires_approval": self.requires_approval,
            "recoverable": self.recoverable,
            "artifact_refs": list(self.artifact_refs),
            "terminal": self.terminal,
            "needs_attention": self.needs_attention,
        }


def _owner_state(value: Any, owner: str) -> str:
    state = str(value or "").strip().lower()
    if not state:
        raise ValueError(f"{owner} state is required")
    return state


def assistant_task_id(rail: ExecutionRail, owner_id: Any) -> str:
    """Stable collision-free read-model id derived from an authoritative owner id."""

    if rail is ExecutionRail.NONE:
        raise ValueError("assistant task ids require an execution rail")
    native = str(owner_id or "").strip()
    if not native:
        raise ValueError("assistant task ids require an owner id")
    return f"{rail.value}:{native}"


def delegation_lifecycle_state(owner_state: Any) -> TaskLifecycleState:
    """Normalize ``tools.async_delegation``/delegate result states."""

    state = _owner_state(owner_state, "delegation")
    if state in _DELEGATION_RUNNING_STATES:
        return TaskLifecycleState.RUNNING
    if state in _DELEGATION_COMPLETED_STATES:
        return TaskLifecycleState.COMPLETED
    if state in _DELEGATION_FAILED_STATES:
        return TaskLifecycleState.FAILED
    if state in _DELEGATION_CANCELLED_STATES:
        return TaskLifecycleState.CANCELLED
    if state in _DELEGATION_INTERRUPTED_STATES:
        return TaskLifecycleState.INTERRUPTED
    raise ValueError(f"unknown delegation state: {state}")


def process_lifecycle_state(
    owner_state: Any, *, exit_code: Any = None, completion_reason: Any = None,
) -> TaskLifecycleState:
    """Normalize ``tools.process_registry`` rows without hiding lost/killed outcomes."""

    state = _owner_state(owner_state, "process")
    reason = str(completion_reason or "").strip().lower()
    if state in _PROCESS_ACTIVE_STATES:
        return TaskLifecycleState.RUNNING
    if reason == "killed":
        return TaskLifecycleState.CANCELLED
    if reason == "lost":
        return TaskLifecycleState.INTERRUPTED
    if reason == "failed_start":
        return TaskLifecycleState.FAILED
    if state in _PROCESS_EXIT_STATES:
        if exit_code is None:
            return TaskLifecycleState.INTERRUPTED
        try:
            code = int(exit_code)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid process exit code: {exit_code!r}") from exc
        return TaskLifecycleState.COMPLETED if code == 0 else TaskLifecycleState.FAILED
    raise ValueError(f"unknown process state: {state}")


def kanban_lifecycle_state(owner_state: Any) -> TaskLifecycleState:
    """Normalize canonical ``hermes_cli.kanban_db.VALID_STATUSES`` values."""

    state = _owner_state(owner_state, "kanban")
    if state in _KANBAN_QUEUED_STATES:
        return TaskLifecycleState.QUEUED
    if state in _KANBAN_RUNNING_STATES:
        return TaskLifecycleState.RUNNING
    if state == "blocked":
        return TaskLifecycleState.BLOCKED
    if state in _KANBAN_COMPLETED_STATES:
        return TaskLifecycleState.COMPLETED
    raise ValueError(f"unknown kanban state: {state}")


def cron_job_lifecycle_state(*, enabled: bool, running: bool = False) -> TaskLifecycleState:
    """Project the durable cron job entity, not one ephemeral run session."""

    if running:
        return TaskLifecycleState.RUNNING
    return TaskLifecycleState.QUEUED if enabled else TaskLifecycleState.PAUSED


def project_process(record: Mapping[str, Any]) -> AssistantTaskProjection:
    """Project a session-scoped background process from ``process.list``."""

    owner_id = str(record.get("session_id") or "").strip()
    state = process_lifecycle_state(
        record.get("status"),
        exit_code=record.get("exit_code"),
        completion_reason=record.get("completion_reason"),
    )
    detail = str(record.get("handoff_note") or record.get("completion_reason") or "").strip()
    return AssistantTaskProjection(
        task_id=assistant_task_id(ExecutionRail.PROCESS, owner_id),
        title=str(record.get("command") or "Background process").strip() or "Background process",
        state=state,
        rail=ExecutionRail.PROCESS,
        durability=ExecutionDurability.PROCESS,
        parent_session_id=str(record.get("parent_session_id") or "").strip() or None,
        owner_id=owner_id,
        detail=detail,
    )


def project_delegation(record: Mapping[str, Any]) -> AssistantTaskProjection:
    """Project a ``list_async_delegations()`` row without taking ownership."""

    owner_id = str(record.get("delegation_id") or "").strip()
    state = delegation_lifecycle_state(record.get("status"))
    goal = str(record.get("goal") or "").strip()
    if not goal and isinstance(record.get("goals"), (list, tuple)):
        goals = [str(item).strip() for item in record["goals"] if str(item).strip()]
        goal = goals[0] if len(goals) == 1 else f"{len(goals)} delegated tasks" if goals else ""
    detail = str(record.get("error") or record.get("summary") or "").strip()
    return AssistantTaskProjection(
        task_id=assistant_task_id(ExecutionRail.DELEGATION, owner_id),
        title=goal or "Delegated work",
        state=state,
        rail=ExecutionRail.DELEGATION,
        durability=ExecutionDurability.PROCESS,
        parent_session_id=str(record.get("parent_session_id") or "").strip() or None,
        owner_id=owner_id,
        detail=detail,
        recoverable=state is TaskLifecycleState.INTERRUPTED,
    )


def project_cron_job(job: Mapping[str, Any], *, running: bool = False) -> AssistantTaskProjection:
    """Project a durable cron job; run sessions remain children of this owner."""

    owner_id = str(job.get("id") or job.get("job_id") or "").strip()
    enabled = bool(job.get("enabled", True))
    detail = str(job.get("paused_reason") or job.get("next_run_at") or "").strip()
    return AssistantTaskProjection(
        task_id=assistant_task_id(ExecutionRail.CRON, owner_id),
        title=str(job.get("name") or "Scheduled work").strip() or "Scheduled work",
        state=cron_job_lifecycle_state(enabled=enabled, running=running),
        rail=ExecutionRail.CRON,
        durability=ExecutionDurability.RESTART_SAFE,
        owner_id=owner_id,
        detail=detail,
    )


def project_kanban_task(task: Mapping[str, Any]) -> AssistantTaskProjection:
    """Project a durable Kanban card from the shared board database."""

    owner_id = str(task.get("id") or task.get("task_id") or "").strip()
    state = kanban_lifecycle_state(task.get("status"))
    detail = str(task.get("blocked_reason") or task.get("summary") or "").strip()
    return AssistantTaskProjection(
        task_id=assistant_task_id(ExecutionRail.KANBAN, owner_id),
        title=str(task.get("title") or "Task").strip() or "Task",
        state=state,
        rail=ExecutionRail.KANBAN,
        durability=ExecutionDurability.RESTART_SAFE,
        owner_id=owner_id,
        detail=detail,
    )


def default_durability(intent: AssistantIntent) -> ExecutionDurability:
    """Return the minimum truthful durability for an intent."""

    if intent is AssistantIntent.BACKGROUND:
        return ExecutionDurability.PROCESS
    if intent is AssistantIntent.SCHEDULE:
        return ExecutionDurability.RESTART_SAFE
    return ExecutionDurability.TURN


def default_rail(intent: AssistantIntent) -> ExecutionRail:
    """Return the existing owner that normally carries this intent.

    Durable work intentionally chooses cron as the simple default; multi-step durable
    graphs can explicitly select KANBAN. No new task runtime is introduced here.
    """

    if intent in {AssistantIntent.RESPOND, AssistantIntent.CLARIFY}:
        return ExecutionRail.NONE
    if intent is AssistantIntent.EXECUTE:
        return ExecutionRail.CURRENT_SESSION
    if intent in {AssistantIntent.DELEGATE, AssistantIntent.BACKGROUND}:
        return ExecutionRail.DELEGATION
    if intent is AssistantIntent.SCHEDULE:
        return ExecutionRail.CRON
    raise ValueError(f"unsupported assistant intent: {intent!r}")


def task_state_is_terminal(state: TaskLifecycleState) -> bool:
    """Whether the authoritative owner has reached a terminal outcome."""

    return state in TERMINAL_TASK_STATES


def task_state_needs_attention(state: TaskLifecycleState) -> bool:
    """Whether a user-facing activity projection should surface the work prominently."""

    return state in ATTENTION_TASK_STATES
