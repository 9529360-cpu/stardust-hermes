"""A child handed to ``_run_single_child`` has an owner from its first statement.

Everything ``_run_single_child`` does before it starts the conversation (leasing a credential,
registering the child, arming the heartbeat) can fail. The caller has already detached the child
from the parent's interrupt list or never will, so the run must give the child back itself.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import model_tools
from tools import delegate_tool


class _Pool:
    """Credential pool double that records every lease it hands out and takes back."""

    def __init__(self, acquire_error: Exception | None = None) -> None:
        self.acquire_error = acquire_error
        self.leased: list[str] = []
        self.released: list[str] = []

    def acquire_lease(self):
        if self.acquire_error is not None:
            raise self.acquire_error
        self.leased.append("cred-1")
        return "cred-1"

    def current(self):
        return None

    def release_lease(self, credential_id):
        self.released.append(credential_id)


class _Child:
    def __init__(self, pool: _Pool, subagent_id: str | None = None) -> None:
        self.tool_progress_callback = None
        self._credential_pool = pool
        # Keep the process-global tool-name restore in cleanup() a no-op for other tests.
        self._delegate_saved_tool_names = list(model_tools._last_resolved_tool_names)
        self._delegate_role = "leaf"
        self._delegate_depth = 1
        self._subagent_id = subagent_id
        self.model = "test-model"
        self.session_id = "child-start-ownership"
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_estimated_cost_usd = 0.0
        self.session_cost_status = "unknown"
        self.conversations = 0
        self.close_calls = 0

    def run_conversation(self, **_kwargs):
        self.conversations += 1
        return {"final_response": "done", "completed": True, "api_calls": 1, "messages": []}

    def get_activity_summary(self):
        return {"api_call_count": 0}

    def close(self):
        self.close_calls += 1


def _parent(child: _Child) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="parent-start-ownership", _current_task_id=None,
        _active_children=[child], _active_children_lock=threading.Lock(),
    )


@pytest.fixture(autouse=True)
def _no_worktree(monkeypatch):
    monkeypatch.setattr(delegate_tool, "_get_worktree_isolation", lambda: False)


def test_credential_lease_failure_gives_the_child_back():
    """acquire_lease() persists the pool under its lock; a locked auth store raises before the run starts."""
    child = _Child(_Pool(acquire_error=OSError("auth store is locked")))
    parent = _parent(child)

    result = delegate_tool._run_single_child(task_index=0, goal="never starts", child=child, parent_agent=parent)

    assert result["status"] == "error"
    assert "auth store is locked" in result["error"]
    assert child.conversations == 0
    assert child.close_calls == 1, "a child that never started was left open"
    assert parent._active_children == [], "a child that never started stayed attached to its parent"


def test_registration_failure_releases_the_leased_credential(monkeypatch):
    pool = _Pool()
    child = _Child(pool, subagent_id="sa-start-ownership")
    parent = _parent(child)

    def refuse(*_args, **_kwargs):
        raise RuntimeError("subagent registry unavailable")

    monkeypatch.setattr(delegate_tool, "_register_child", refuse)

    result = delegate_tool._run_single_child(task_index=0, goal="never starts", child=child, parent_agent=parent)

    assert result["status"] == "error"
    assert "subagent registry unavailable" in result["error"]
    assert child.conversations == 0
    assert pool.leased == ["cred-1"]
    assert pool.released == ["cred-1"], "the credential lease taken for a child that never started leaked"
    assert child.close_calls == 1
    assert parent._active_children == []


def test_successful_run_still_closes_and_detaches_exactly_once():
    pool = _Pool()
    child = _Child(pool)
    parent = _parent(child)

    result = delegate_tool._run_single_child(task_index=0, goal="runs", child=child, parent_agent=parent)

    assert result["status"] == "completed"
    assert child.conversations == 1
    assert pool.leased == pool.released == ["cred-1"]
    assert child.close_calls == 1
    assert parent._active_children == []
