from __future__ import annotations

from agent.context_compressor import (
    COMPRESSED_SUMMARY_HAS_USER_TURN_KEY,
    COMPRESSED_SUMMARY_METADATA_KEY,
)
from plugins.context_engine import load_context_engine
from plugins.context_engine.hierarchical import (
    HierarchicalContextEngine,
    _ContextBlock,
)


def _engine() -> HierarchicalContextEngine:
    engine = HierarchicalContextEngine()
    engine.update_model(
        model="test-model",
        context_length=200_000,
        base_url="",
        api_key="",
        provider="test",
        api_mode="chat_completions",
    )
    engine.quiet_mode = True
    return engine


def _plain_messages():
    return [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old question one"},
        {"role": "assistant", "content": "old answer one"},
        {"role": "user", "content": "old question two"},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent final"},
    ]


def _summary_message(engine: HierarchicalContextEngine, blocks):
    return {
        "role": "user",
        "content": engine._render_container(blocks),
        COMPRESSED_SUMMARY_METADATA_KEY: True,
        COMPRESSED_SUMMARY_HAS_USER_TURN_KEY: any(block.has_user_turn for block in blocks),
    }


def test_loader_returns_hierarchical_engine():
    engine = load_context_engine("hierarchical")
    assert isinstance(engine, HierarchicalContextEngine)
    assert engine.name == "hierarchical"


def test_first_compaction_creates_one_incremental_block(monkeypatch):
    engine = _engine()
    messages = _plain_messages()
    monkeypatch.setattr(engine, "_compress_window", lambda _messages: (1, 4))

    seen_previous = []
    seen_turns = []

    def fake_summary(turns, **_kwargs):
        seen_previous.append(engine._previous_summary)
        seen_turns.append([message["content"] for message in turns])
        return engine._with_summary_prefix("fresh immutable block one")

    monkeypatch.setattr(engine, "_generate_summary", fake_summary)

    result = engine.compress(messages, current_tokens=160_000, force=True)
    blocks = engine._extract_existing_blocks(result)

    assert seen_previous == [None]
    assert seen_turns == [[
        "old question one",
        "old answer one",
        "old question two",
    ]]
    assert len(blocks) == 1
    assert blocks[0].tier == 1
    assert blocks[0].body == "fresh immutable block one"
    assert blocks[0].has_user_turn is True

    visible_roles = [message["role"] for message in result if message["role"] != "system"]
    assert all(left != right for left, right in zip(visible_roles, visible_roles[1:]))


def test_second_compaction_keeps_old_block_verbatim_and_does_not_resummarize_it(monkeypatch):
    engine = _engine()
    old_block = _ContextBlock(
        block_id="b_1111111111111111",
        tier=1,
        body="immutable block one EXACT",
        has_user_turn=True,
    )
    messages = [
        {"role": "system", "content": "system"},
        _summary_message(engine, [old_block]),
        {"role": "assistant", "content": "unsummarized answer one"},
        {"role": "user", "content": "unsummarized question two"},
        {"role": "assistant", "content": "unsummarized answer two"},
        {"role": "user", "content": "protected recent question"},
        {"role": "assistant", "content": "protected recent answer"},
    ]
    monkeypatch.setattr(engine, "_compress_window", lambda _messages: (1, 5))

    seen_previous = []
    seen_payload = []

    def fake_summary(turns, **_kwargs):
        seen_previous.append(engine._previous_summary)
        seen_payload.append("\n".join(str(message.get("content", "")) for message in turns))
        return engine._with_summary_prefix("fresh immutable block two")

    monkeypatch.setattr(engine, "_generate_summary", fake_summary)

    result = engine.compress(messages, current_tokens=160_000, force=True)
    blocks = engine._extract_existing_blocks(result)

    assert seen_previous == [None]
    assert len(seen_payload) == 1
    assert "immutable block one EXACT" not in seen_payload[0]
    assert [block.body for block in blocks] == [
        "immutable block one EXACT",
        "fresh immutable block two",
    ]


def test_block_ceiling_promotes_oldest_four_instead_of_rewriting_every_block(monkeypatch):
    engine = _engine()
    old_blocks = [
        _ContextBlock(
            block_id=f"b_{index:016x}",
            tier=1,
            body=f"old block {index} EXACT",
            has_user_turn=True,
        )
        for index in range(8)
    ]
    messages = [
        {"role": "system", "content": "system"},
        _summary_message(engine, old_blocks),
        {"role": "assistant", "content": "unsummarized answer one"},
        {"role": "user", "content": "unsummarized question two"},
        {"role": "assistant", "content": "unsummarized answer two"},
        {"role": "user", "content": "protected recent question"},
        {"role": "assistant", "content": "protected recent answer"},
    ]
    monkeypatch.setattr(engine, "_compress_window", lambda _messages: (1, 5))
    monkeypatch.setattr(
        engine,
        "_generate_summary",
        lambda _turns, **_kwargs: engine._with_summary_prefix("new tier-one block"),
    )

    promoted_groups = []

    def fake_promotion(group, **_kwargs):
        promoted_groups.append(tuple(block.block_id for block in group))
        return "promoted oldest four"

    monkeypatch.setattr(engine, "_summarize_promotion", fake_promotion)

    result = engine.compress(messages, current_tokens=160_000, force=True)
    blocks = engine._extract_existing_blocks(result)

    assert promoted_groups == [tuple(block.block_id for block in old_blocks[:4])]
    assert len(blocks) == 6
    assert blocks[0].tier == 2
    assert blocks[0].parents == tuple(block.block_id for block in old_blocks[:4])
    assert blocks[0].body == "promoted oldest four"
    assert [block.body for block in blocks[1:5]] == [
        block.body for block in old_blocks[4:]
    ]
    assert blocks[-1].body == "new tier-one block"


def test_switching_from_legacy_handoff_preserves_it_as_first_block(monkeypatch):
    engine = _engine()
    legacy_summary = engine._with_summary_prefix("legacy checkpoint EXACT")
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": legacy_summary,
            COMPRESSED_SUMMARY_METADATA_KEY: True,
            COMPRESSED_SUMMARY_HAS_USER_TURN_KEY: True,
        },
        {"role": "assistant", "content": "unsummarized answer one"},
        {"role": "user", "content": "unsummarized question two"},
        {"role": "assistant", "content": "unsummarized answer two"},
        {"role": "user", "content": "protected recent question"},
        {"role": "assistant", "content": "protected recent answer"},
    ]
    monkeypatch.setattr(engine, "_compress_window", lambda _messages: (1, 5))
    monkeypatch.setattr(
        engine,
        "_generate_summary",
        lambda _turns, **_kwargs: engine._with_summary_prefix("new block"),
    )

    result = engine.compress(messages, current_tokens=160_000, force=True)
    blocks = engine._extract_existing_blocks(result)

    assert [block.body for block in blocks] == [
        "legacy checkpoint EXACT",
        "new block",
    ]


def test_summary_failure_preserves_raw_messages(monkeypatch):
    engine = _engine()
    messages = _plain_messages()
    monkeypatch.setattr(engine, "_compress_window", lambda _messages: (1, 4))
    monkeypatch.setattr(engine, "_generate_summary", lambda _turns, **_kwargs: None)

    result = engine.compress(messages, current_tokens=160_000, force=True)

    assert result == messages
    assert engine._last_compress_aborted is True
