"""Regression tests for the session -> cron handoff connection."""

from __future__ import annotations


def test_render_recent_dialogue_keeps_only_user_and_assistant_text():
    from cron.session_handoff import _render_recent_dialogue

    rendered = _render_recent_dialogue([
        {"role": "system", "content": "PRIVATE SYSTEM"},
        {"role": "user", "content": "Plan the dentist follow-up."},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I found two suitable times."},
            {"type": "image_url", "image_url": {"url": "secret"}},
        ]},
        {"role": "tool", "content": "RAW TOOL PAYLOAD"},
    ])

    assert rendered == (
        "USER: Plan the dentist follow-up.\n\n"
        "ASSISTANT: I found two suitable times."
    )
    assert "PRIVATE SYSTEM" not in rendered
    assert "RAW TOOL PAYLOAD" not in rendered
    assert "secret" not in rendered


def test_render_recent_dialogue_is_bounded_and_prefers_recent(monkeypatch):
    import cron.session_handoff as handoff

    monkeypatch.setattr(handoff, "MAX_HANDOFF_MESSAGES", 2)
    monkeypatch.setattr(handoff, "MAX_HANDOFF_CHARS", 90)

    rendered = handoff._render_recent_dialogue([
        {"role": "user", "content": "old context must drop"},
        {"role": "assistant", "content": "recent assistant " + "a" * 80},
        {"role": "user", "content": "latest instruction " + "b" * 80},
    ])

    assert rendered is not None
    assert len(rendered) <= 90
    assert "old context must drop" not in rendered
    assert "latest instruction" in rendered


def test_capture_session_handoff_reuses_registry_and_releases(monkeypatch):
    import cron.session_handoff as handoff
    import hermes_state_registry

    calls = []

    class FakeDB:
        def get_messages_as_conversation(self, session_id, **kwargs):
            calls.append((session_id, kwargs))
            return [
                {"role": "user", "content": "Keep tracking the repair."},
                {"role": "assistant", "content": "Waiting for the technician reply."},
            ]

    fake = FakeDB()
    monkeypatch.setattr(hermes_state_registry, "acquire", lambda: fake)
    monkeypatch.setattr(
        hermes_state_registry, "release_or_close", lambda db: calls.append(("release", db))
    )

    rendered = handoff.capture_session_handoff("session-123")

    assert "Keep tracking the repair." in rendered
    assert calls[0] == (
        "session-123",
        {"include_ancestors": True, "include_compacted": True},
    )
    assert calls[-1] == ("release", fake)


def test_capture_session_handoff_failure_is_non_blocking(monkeypatch):
    import cron.session_handoff as handoff
    import hermes_state_registry

    released = []
    fake = object()
    monkeypatch.setattr(hermes_state_registry, "acquire", lambda: fake)
    monkeypatch.setattr(
        hermes_state_registry,
        "release_or_close",
        lambda db: released.append(db),
    )

    assert handoff.capture_session_handoff("session-123") is None
    assert released == [fake]


def test_build_job_prompt_injects_creation_time_handoff():
    from cron.scheduler_prompt import _build_job_prompt

    prompt = _build_job_prompt({
        "id": "abcdef123456",
        "name": "Follow up",
        "prompt": "Check whether the repair company replied and continue the task.",
        "handoff_context": (
            "USER: The washing machine repair is still pending.\n\n"
            "ASSISTANT: I contacted the repair company and am waiting for a reply."
        ),
    })

    assert "## Creation-time task handoff" in prompt
    assert "washing machine repair is still pending" in prompt
    assert "Check whether the repair company replied" in prompt
