"""Stable intent/execution vocabulary for the Stardust personal-assistant control plane.

This module is deliberately policy-only: it does not run tools, mutate sessions, or
own task state. The live agent remains the decision maker; callers can use this
vocabulary to describe the selected execution rail consistently across gateway,
desktop, telemetry, and tests without creating a second agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    DELEGATION = "delegation"
    CRON = "cron"
    KANBAN = "kanban"


class TaskLifecycleState(str, Enum):
    """Common read-model state for assistant work owned by existing runtimes."""

    QUEUED = "queued"
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
        if self.intent is AssistantIntent.SCHEDULE and self.rail not in {ExecutionRail.CRON, ExecutionRail.KANBAN}:
            raise ValueError("scheduled work must select a durable execution rail")


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
