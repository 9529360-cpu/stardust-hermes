"""Session-scoped workflow selection during one model-schema build.

The registry's ordinary availability cache is profile-wide, while a gateway can build schemas for
several platforms in the same profile concurrently. Carry explicit selections through ContextVars,
never process env. The personal-assistant coordinator and the low-level durable-task kernel are
separate capabilities: selecting one must not silently expose the other.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable, Iterator, Optional

_kanban_requested: ContextVar[Optional[bool]] = ContextVar("kanban_toolset_requested", default=None)
_assistant_requested: ContextVar[Optional[bool]] = ContextVar("assistant_orchestration_requested", default=None)


def kanban_toolset_requested() -> Optional[bool]:
    """None outside schema assembly; otherwise whether low-level Kanban was named explicitly."""
    return _kanban_requested.get()


def assistant_orchestration_requested() -> Optional[bool]:
    """None outside schema assembly; otherwise whether the personal-assistant facade was named explicitly."""
    return _assistant_requested.get()


@contextmanager
def scoped_kanban_toolset_selection(toolsets: Optional[Iterable[str]]) -> Iterator[None]:
    """Bind explicit workflow capabilities for one schema/availability pass.

    ``kanban`` is the worker/operator kernel surface. ``assistant_orchestration`` is the Desktop
    coordinator surface. They may both be selected by an explicitly technical surface, but neither
    selection implies the other. ``None`` remains an unscoped discovery context rather than an opt-in.
    """
    if toolsets is None:
        kanban_selected: Optional[bool] = None
        assistant_selected: Optional[bool] = None
    else:
        selected = {str(name).strip() for name in toolsets if str(name).strip()}
        kanban_selected = "kanban" in selected
        assistant_selected = "assistant_orchestration" in selected

    kanban_token = _kanban_requested.set(kanban_selected)
    assistant_token = _assistant_requested.set(assistant_selected)
    try:
        yield
    finally:
        _assistant_requested.reset(assistant_token)
        _kanban_requested.reset(kanban_token)
