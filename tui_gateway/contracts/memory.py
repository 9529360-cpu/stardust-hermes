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
    targets: dict[Literal["memory", "user"], Literal["enabled", "disabled"]]


class MemoryRememberParams(Params):
    target: Literal["memory", "user"]
    content: str


class MemoryForgetParams(Params):
    target: Literal["memory", "user"]
    index: StrictInt | None = None
    text: str | None = None
    expected_text: str | None = None


class MemoryMutationResult(Result):
    success: bool


method("memory.list", params=MemoryListParams, result=MemoryListResult,
       doc="List enabled curated targets with zero-based indices and explicit target status. Unreadable enabled targets return an error; disabled targets expose no entries.")
method("memory.remember", params=MemoryRememberParams, result=MemoryMutationResult,
       doc="Persist a curated entry using the agent memory content and size guards.")
method("memory.forget", params=MemoryForgetParams, result=MemoryMutationResult,
       doc="Remove by exact unique text, or index plus expected_text from memory.list. Stale selections do not write.")
