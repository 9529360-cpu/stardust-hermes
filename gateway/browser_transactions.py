"""Browser transaction primitives.

This module is the state layer for browser actions that span multiple controller
frames. It deliberately does not own browser automation; the browser controller
and artifact store remain authoritative. A transaction only records progress,
recovery metadata, and artifact references.

The goal is to avoid growing browser tools into unrelated one-shot calls:
future upload/download, multi-step form, and recovery flows can share this
state contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Optional


class BrowserTransactionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BrowserTransaction:
    """Serializable browser workflow state.

    This intentionally stores references, not browser secrets, cookies, or raw
    file bytes. ArtifactStore remains the owner of transferred data.
    """

    transaction_id: str
    session_id: str
    operation: str
    status: BrowserTransactionStatus = BrowserTransactionStatus.CREATED
    current_step: str = ""
    active_tab_id: Optional[str] = None
    artifact_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)
    last_error: Optional[str] = None

    def advance(self, step: str) -> None:
        self.current_step = step
        self.status = BrowserTransactionStatus.RUNNING
        self.updated_at = time()

    def attach_artifact(self, artifact_id: str) -> None:
        if artifact_id not in self.artifact_ids:
            self.artifact_ids.append(artifact_id)
        self.updated_at = time()

    def fail(self, error: str) -> None:
        self.status = BrowserTransactionStatus.FAILED
        self.last_error = error
        self.updated_at = time()

    def complete(self) -> None:
        self.status = BrowserTransactionStatus.COMPLETED
        self.updated_at = time()
