"""Per-turn topic-summary recall is bounded and does not require an external provider."""

from __future__ import annotations

from types import SimpleNamespace

from agent.turn_context import _memory_turn_start_and_prefetch
from tools.topic_memory_store import upsert_topic_summary


def _agent():
    return SimpleNamespace(
        session_id="session-a",
        _user_turn_count=1,
        _memory_manager=None,
        _topic_summaries_enabled=True,
        _topic_recall_limit=2,
        _topic_recall_char_budget=1800,
        _topic_recall_session_id="",
        _topic_recall_versions={},
        _emit_status=lambda *_args, **_kwargs: None,
    )


def test_topic_summary_recall_works_without_external_memory_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    upsert_topic_summary(
        "stardust",
        "Stardust is the only active product line.",
        title="Stardust",
        keywords=["星尘", "Stardust"],
    )
    agent = _agent()

    context = _memory_turn_start_and_prefetch(agent, "继续弄星尘的记忆", None)

    assert "Stardust is the only active product line." in context


def test_unchanged_topic_is_injected_once_per_session_and_new_version_reappears(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    upsert_topic_summary("stardust", "Version one.", title="Stardust", keywords=["星尘"])
    agent = _agent()

    assert "Version one." in _memory_turn_start_and_prefetch(agent, "星尘怎么了", None)
    assert _memory_turn_start_and_prefetch(agent, "继续星尘", None) == ""

    upsert_topic_summary("stardust", "Version two.", title="Stardust", keywords=["星尘"])
    assert "Version two." in _memory_turn_start_and_prefetch(agent, "继续星尘", None)

    agent.session_id = "session-b"
    assert "Version two." in _memory_turn_start_and_prefetch(agent, "继续星尘", None)


def test_unrelated_turn_injects_no_topic_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    upsert_topic_summary("stardust", "Desktop state.", title="Stardust", keywords=["星尘"])
    agent = _agent()
    assert _memory_turn_start_and_prefetch(agent, "帮我算一下二加二", None) == ""
