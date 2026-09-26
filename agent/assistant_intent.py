"""Intent, execution, and read-only task projection contracts for Stardust.

The module is deliberately policy/read-model only. It does not run tools or own mutable
task state: current-session execution, background processes, delegation, cron, and Kanban
remain authoritative in their existing runtimes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal


Durability = Literal["turn", "process_local", "restart_durable"]


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
    KANBAN = "kanban"


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

_DURABLE_RAILS = frozenset({AssistantExecutionRail.CRON, AssistantExecutionRail.KANBAN})
_RAIL_DURABILITY: dict[AssistantExecutionRail, Durability] = {
    AssistantExecutionRail.CURRENT_SESSION: "turn",
    AssistantExecutionRail.PROCESS: "process_local",
    AssistantExecutionRail.DELEGATION: "process_local",
    AssistantExecutionRail.CRON: "restart_durable",
    AssistantExecutionRail.KANBAN: "restart_durable",
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

_KANBAN_QUEUED_STATES = frozenset({"triage", "todo", "scheduled", "ready"})
_KANBAN_RUNNING_STATES = frozenset({"running", "review"})
_KANBAN_COMPLETED_STATES = frozenset({"done", "archived"})


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
        "Use cron, kanban, or another durable owner for future, recurring, monitored, or restart-surviving work.",
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
            AssistantExecutionRail.KANBAN,
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


def kanban_lifecycle_state(owner_state: Any) -> AssistantTaskState:
    state = _owner_state(owner_state, "kanban")
    if state in _KANBAN_QUEUED_STATES:
        return AssistantTaskState.QUEUED
    if state in _KANBAN_RUNNING_STATES:
        return AssistantTaskState.RUNNING
    if state == "blocked":
        return AssistantTaskState.BLOCKED
    if state in _KANBAN_COMPLETED_STATES:
        return AssistantTaskState.COMPLETED
    raise ValueError(f"unknown kanban state: {state}")


def project_delegation(record: Mapping[str, Any]) -> AssistantTaskProjection:
    """Project live, completion-event, or durable-ledger delegation data."""

    task = _mapping(record.get("task"))
    event = _mapping(record.get("event"))
    result = _mapping(record.get("result"))
    records = (record, event, result, task)

    owner_id = _first_text(records, "delegation_id")
    owner_state = _first_text((event, record), "status", "state")
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
        final_report=_first_text((job,), "final_report", "last_report") or None,
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
    )


def project_kanban_task(
    task: Mapping[str, Any], *, run: Mapping[str, Any] | None = None
) -> AssistantTaskProjection:
    """Project a durable Kanban task plus its optional current/latest run handoff."""

    run_record = _mapping(run)
    metadata = _mapping(run_record.get("metadata"))
    records = (task, run_record, metadata)
    owner_id = _text(task.get("task_id") or task.get("id"))
    state = kanban_lifecycle_state(task.get("status"))
    final_report = (
        _first_text((run_record, task), "final_report", "summary", "result") or None
    )

    return AssistantTaskProjection(
        task_id=assistant_task_id(AssistantExecutionRail.KANBAN, owner_id),
        title=_text(task.get("title")) or "Task",
        state=state,
        rail=AssistantExecutionRail.KANBAN,
        durability="restart_durable",
        background=True,
        owner_id=owner_id,
        parent_session_id=_text(task.get("parent_session_id") or task.get("session_id")) or None,
        child_ids=_refs_from(records, "child_ids", "children", "created_cards"),
        project_ref=_text(task.get("project_id") or task.get("project_ref")) or None,
        workspace=_text(task.get("workspace_path") or task.get("workdir")) or None,
        requires_approval=bool(
            task.get("requires_approval")
            or run_record.get("requires_approval")
            or metadata.get("requires_approval")
        ),
        approval_refs=_refs_from(records, "approval_id", "approval_ids", "approval_refs"),
        artifact_refs=_refs_from(records, "artifacts", "artifact_refs"),
        final_report=final_report,
        detail=_first_text(
            (run_record, task),
            "blocked_reason",
            "error",
            "last_failure_error",
        ),
        recoverable=False,
        recovery_action="unblock" if state is AssistantTaskState.BLOCKED else None,
    )


ASSISTANT_EXECUTION_GUIDANCE = (
    "Choose the smallest execution mode that satisfies the user's intent: `respond` for conversation, "
    "explanation, brainstorming, review, and advice; `execute_foreground` for bounded authorized work on the "
    "current tool surface; `delegate` for scoped coding, research, or multi-step child work while the parent "
    "remains the orchestrator; `delegate_background` for process-local work that can run without holding the "
    "foreground, never as a promise of restart durability; `schedule_or_watch` for future, recurring, monitored, "
    "or restart-surviving work through cron, kanban, or another durable owner; and `clarify` only when a material "
    "user decision, authorization, recipient, or safety-critical fact is genuinely missing. Missing passwords, "
    "payment details, OTP seeds, or API secrets should use secure local credential capture or Vault resolution "
    "before chat clarification whenever that path exists. Background work must remain observable and must not steal focus."
)
