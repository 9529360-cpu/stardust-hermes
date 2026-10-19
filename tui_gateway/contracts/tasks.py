"""Profile-scoped Task Center read contract."""

from typing import Literal

from pydantic import Field

from .base import Params, Result
from .registry import method


TaskState = Literal[
    "queued", "paused", "running", "waiting_for_user", "blocked", "completed",
    "failed", "cancelled", "interrupted",
]
TaskRail = Literal["delegation", "process", "cron", "goal"]
TaskDurability = Literal["process_local", "restart_durable", "session_bound"]
TaskRecoveryAction = Literal["retry", "review_or_retry", "resume"]
TaskDegradedRail = Literal["delegation", "cron", "goal"]


class TaskRow(Result):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(max_length=500)
    state: TaskState
    rail: TaskRail
    durability: TaskDurability
    background: bool
    phase: str | None = Field(default=None, max_length=80)
    started_at: float | None = None
    updated_at: float | None = None
    next_run_at: float | None = None
    progress_current: int | None = Field(default=None, ge=0)
    progress_total: int | None = Field(default=None, ge=0)
    parent_session_id: str | None = None
    parent_task_id: str | None = None
    requires_approval: bool
    recoverable: bool
    recovery_action: TaskRecoveryAction | None = None
    summary: str = Field(max_length=160)
    detail: str = Field(max_length=500)
    files: list[str] = Field(max_length=8)
    needs_attention: bool
    terminal: bool
    profile: str | None = None


class TasksListParams(Params):
    profile: str = ""
    limit: int = Field(default=20, ge=1, le=50)


class TasksListResult(Result):
    tasks: list[TaskRow]
    degraded: list[TaskDegradedRail]
    scoped: str = ""


method(
    "tasks.list",
    params=TasksListParams,
    result=TasksListResult,
    doc=(
        "Read allowlisted task rows from one profile home. The profile resolves as in cron.manage list "
        "(unknown or deleted: 4064); limit per task rail is clamped to 1..50; the response includes "
        "`degraded` rails and a `scoped` profile name so clients can reject a mismatched slice. "
        "This method is read-only and grants no task control authority."
    ),
)
