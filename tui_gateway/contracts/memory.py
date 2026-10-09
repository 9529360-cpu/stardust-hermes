"""User-facing curated memory management contracts."""

from typing import Literal

from pydantic import StrictInt

from .base import Params, Result
from .registry import method


class MemoryListParams(Params):
    target: Literal["memory", "user", "both"] = "both"


class MemoryEntry(Result):
    target: Literal["memory", "user"]
    index: int
    text: str


class MemoryListResult(Result):
    entries: list[MemoryEntry]


class MemoryRememberParams(Params):
    target: Literal["memory", "user"]
    content: str


class MemoryForgetParams(Params):
    target: Literal["memory", "user"]
    index: StrictInt | None = None
    text: str | None = None


class MemoryMutationResult(Result):
    success: bool


method("memory.list", params=MemoryListParams, result=MemoryListResult,
       doc="List curated entries with zero-based per-target indices; indices are stable until the list changes.")
method("memory.remember", params=MemoryRememberParams, result=MemoryMutationResult,
       doc="Persist a curated entry using the agent memory content and size guards.")
method("memory.forget", params=MemoryForgetParams, result=MemoryMutationResult,
       doc="Remove by zero-based index or uniquely matching text (exactly one selector required).")
