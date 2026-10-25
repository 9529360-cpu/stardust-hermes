"""Background-review hook tests (DL-P2.9): the fact pipeline runs inside the
review thread only when memory.fact_pipeline is ON; failures never propagate.
"""
from __future__ import annotations

import json

import pytest

from agent import background_review
from tools import fact_store as fs


@pytest.fixture()
def store_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(home))
    yield home


@pytest.fixture()
def flag_on(store_home, monkeypatch):
    import tools.memory_tool as mt

    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": True})


class _FakeAgent:
    """Only what _run_review_in_thread touches before the fork is mocked away."""

    session_id = "session-test-1"

    def _emit_auxiliary_failure(self, *a, **k):
        pass


def _messages(*turns):
    return [{"role": role, "content": text} for role, text in turns]


def _run_thread(agent, messages, monkeypatch):
    # Stop the real review fork right after the fact hook: make the thread bail
    # through its normal guard path (parent cannot emit tool calls -> skipped).
    monkeypatch.setattr(background_review, "_parent_can_emit_tool_calls", lambda a: False)
    monkeypatch.setattr(
        background_review, "_resolve_review_runtime", lambda a, t=None: {"routed": False}
    )
    background_review._run_review_in_thread(agent, messages, "prompt")


def test_thread_hook_runs_pipeline_when_flag_on(store_home, flag_on, monkeypatch):
    import tools.fact_pipeline as fp

    seen = {}

    def fake_extraction(messages, **kw):
        seen["messages"] = messages
        seen["session"] = kw.get("source_session_id")
        return {"counts": {"ADD": 1}}

    monkeypatch.setattr(fp, "run_fact_extraction", fake_extraction)
    snapshot = _messages(("user", "我搬到都灵了。"), ("assistant", "记住了。"))
    _run_thread(_FakeAgent(), snapshot, monkeypatch)
    assert seen.get("messages") is snapshot
    assert seen.get("session") == "session-test-1"


def test_thread_hook_survives_pipeline_error(store_home, flag_on, monkeypatch):
    import tools.fact_pipeline as fp

    def boom(*a, **k):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(fp, "run_fact_extraction", boom)
    # Must not raise: the hook is wrapped in suppress(Exception).
    _run_thread(_FakeAgent(), _messages(("user", "hi")), monkeypatch)


def test_flag_off_pipeline_short_circuits(store_home, monkeypatch):
    import tools.fact_pipeline as fp

    def must_not_call_model(**kw):
        raise AssertionError("model must not be called with the flag off")

    result = fp.run_fact_extraction(_messages(("user", "hi")), call_llm=must_not_call_model)
    assert result == {"skipped": "flag-off"}


def test_hook_end_to_end_writes_store(store_home, flag_on, monkeypatch):
    """Real pipeline through the thread hook (model call mocked at the boundary)."""
    reply = json.dumps({"facts": [{"subject": "residence", "content": "阿豪住在都灵。"}]})

    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm",
        lambda **kw: reply,
        raising=False,
    )
    _run_thread(_FakeAgent(), _messages(("user", "我搬到都灵了。")), monkeypatch)
    assert [r["content"] for r in fs.load_facts()] == ["阿豪住在都灵。"]
