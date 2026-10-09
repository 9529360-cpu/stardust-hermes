"""Unified work ledger wire contract."""
from typing import Literal
from pydantic import Field
from .base import JsonValue, Params, Result
from .registry import method


class WorkListParams(Params):
    session_id: str = ""


class WorkItem(Result):
    id: str
    kind: Literal["delegation", "process", "subagent"]
    title: str
    status: Literal["running", "completed", "failed", "cancelled", "interrupted"]
    started_at: float | None
    updated_at: float | None
    detail: dict[str, JsonValue]


class WorkListResult(Result):
    work: list[WorkItem]


class WorkCancelParams(Params):
    id: str = Field(min_length=1)
    session_id: str = ""


class WorkCancelResult(Result):
    id: str
    status: Literal["not_found", "already_finished", "cancelled", "interrupt_requested", "unavailable", "error"]
    message: str


method("work.list", params=WorkListParams, result=WorkListResult)
method("work.cancel", params=WorkCancelParams, result=WorkCancelResult)
