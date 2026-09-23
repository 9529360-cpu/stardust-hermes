"""Authoritative ownership map for durable Stardust state.

This module is intentionally small and dependency-free so backend surfaces,
diagnostics, tests, and future migration code can share one answer to
"which store owns this fact?". It is descriptive policy, not a shadow store.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StateDomain(str, Enum):
    USER_PROFILE = "user_profile"
    GLOBAL_MEMORY = "global_memory"
    PROJECT = "project"
    SESSION = "session"
    TASK = "task"
    INFERENCE = "inference"


@dataclass(frozen=True)
class StateAuthority:
    domain: StateDomain
    store: str
    scope: str
    durable: bool
    notes: str


AUTHORITIES = {
    StateDomain.USER_PROFILE: StateAuthority(
        StateDomain.USER_PROFILE,
        "memories/USER.md",
        "profile",
        True,
        "Explicit durable facts and preferences about the user.",
    ),
    StateDomain.GLOBAL_MEMORY: StateAuthority(
        StateDomain.GLOBAL_MEMORY,
        "memories/MEMORY.md",
        "profile",
        True,
        "Cross-project assistant knowledge that is not owned by a project or task.",
    ),
    StateDomain.PROJECT: StateAuthority(
        StateDomain.PROJECT,
        "projects.db",
        "project",
        True,
        "Project identity, folders, metadata, and project-scoped facts with provenance.",
    ),
    StateDomain.SESSION: StateAuthority(
        StateDomain.SESSION,
        "state.db",
        "session",
        True,
        "Conversation transcript, lineage, compression state, and session metadata.",
    ),
    StateDomain.TASK: StateAuthority(
        StateDomain.TASK,
        "kanban.db",
        "task",
        True,
        "Durable task lifecycle. Session summaries may describe tasks but never own them.",
    ),
    StateDomain.INFERENCE: StateAuthority(
        StateDomain.INFERENCE,
        "owning domain with provenance",
        "derived",
        False,
        "Model inference is never authoritative until explicitly verified or promoted.",
    ),
}


def authority_for(domain: StateDomain | str) -> StateAuthority:
    """Return the one declared owner for a state domain."""
    return AUTHORITIES[StateDomain(domain)]
