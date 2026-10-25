"""Selective automatic extraction at the real store/pipeline boundary."""
import json

import pytest

from tools import fact_pipeline, fact_store


@pytest.fixture
def selective_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setattr("tools.memory_tool.get_builtin_memory_config", lambda: {
        "fact_pipeline": True, "fact_selectivity": True,
    })
    return tmp_path


@pytest.mark.parametrize("text", [
    "你好", "谢谢！", "ok", "我的密码是 hunter2", "My salary is 4500 euros",
    "我患有糖尿病", "My sister's phone number is 1234567890",
])
def test_prefilter_does_not_call_model_or_write(selective_home, text):
    def forbidden(**kwargs):
        pytest.fail("filtered content reached the model")
    result = fact_pipeline.run_fact_extraction([{"role": "user", "content": text}], call_llm=forbidden)
    assert result.get("skipped") == "selectivity-prefilter"
    assert fact_store.load_facts() == []


def test_model_updates_cross_subject_and_ignores_transient(selective_home):
    old = fact_store.add_fact("home_city", "location", "用户住在米兰。")
    old_id = fact_store.load_facts()[0]["id"]
    def model(**kwargs):
        assert old_id in kwargs["messages"][1]["content"]
        return json.dumps({"facts": [
            {"subject": "residence", "content": "用户现居罗马。", "category": "correction-of-existing", "action": "UPDATE", "target_id": old_id},
            {"subject": "weather", "content": "今天下雨。", "category": "transient", "action": "ADD"},
        ]})
    result = fact_pipeline.run_fact_extraction([{"role": "user", "content": "我已经从米兰搬到罗马，以后就住这里。今天下雨。"}], call_llm=model)
    assert result["counts"] == {"UPDATE": 1}
    assert [x["content"] for x in fact_store.load_facts()] == ["用户现居罗马。"]
    assert len(fact_store.load_facts(include_history=True)) == 2


def test_duplicate_paraphrase_noop_and_unknown_target_rejected(selective_home):
    fact_store.add_fact("language", "preference", "用户喜欢中文回答。")
    fid = fact_store.load_facts()[0]["id"]
    def model(**kwargs):
        return {"facts": [
            {"subject": "reply_language", "content": "用户偏好中文。", "category": "durable", "action": "NOOP", "target_id": fid},
            {"subject": "language", "content": "用户喜欢英文。", "category": "durable", "action": "UPDATE", "target_id": "invented"},
        ]}
    fact_pipeline.run_fact_extraction([{"role": "user", "content": "我喜欢用中文交流。"}], call_llm=model)
    assert [x["content"] for x in fact_store.load_facts()] == ["用户喜欢中文回答。"]


@pytest.mark.parametrize("item", [
    {"subject": "health", "content": "User has diabetes", "category": "durable", "action": "ADD"},
    {"subject": "weather", "content": "Today is sunny", "category": "transient", "action": "ADD"},
    {"subject": "language", "content": "中文", "action": "ADD"},
    {"subject": "language", "content": "中文", "category": "durable", "action": "DELETE"},
])
def test_invalid_or_sensitive_model_output_cannot_write(selective_home, item):
    fact_pipeline.run_fact_extraction([{"role": "user", "content": "请记住我的长期偏好。"}], call_llm=lambda **kw: {"facts": [item]})
    assert fact_store.load_facts() == []


def test_assistant_claims_are_not_extraction_evidence(selective_home):
    def model(**kwargs):
        assert "虚构" not in kwargs["messages"][1]["content"]
        return {"facts": []}
    fact_pipeline.run_fact_extraction([
        {"role": "user", "content": "我偏好简洁回答。"},
        {"role": "assistant", "content": "虚构用户偏好英文。"},
    ], call_llm=model)


def test_flag_off_keeps_original_prompt_and_results(selective_home, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_builtin_memory_config", lambda: {"fact_pipeline": True, "fact_selectivity": False})
    def model(**kwargs):
        assert kwargs["messages"][0]["content"] == fact_pipeline._EXTRACTION_SYSTEM
        assert kwargs["messages"][1]["content"] == "Conversation:\nuser: 你好\n\nExtract facts."
        return {"facts": []}
    assert fact_pipeline.run_fact_extraction([{"role": "user", "content": "你好"}], call_llm=model)["skipped"] == "no-facts"


@pytest.mark.parametrize("reply", [None, "not JSON", {"facts": {}}, {"choices": []}])
def test_malformed_reply_leaves_store_unchanged(selective_home, reply):
    fact_store.add_fact("city", "location", "用户住在米兰。")
    path = selective_home / "memories" / "FACTS.json"
    before = path.read_bytes()
    fact_pipeline.run_fact_extraction([{"role": "user", "content": "我现在住在罗马。"}], call_llm=lambda **kw: reply)
    assert path.read_bytes() == before


def test_same_subject_correction_updates_instead_of_adding(selective_home):
    fact_store.add_fact("city", "location", "用户住在米兰。")
    fid = fact_store.load_facts()[0]["id"]
    result = fact_pipeline.run_fact_extraction([{"role": "user", "content": "我现在长期住在罗马。"}], call_llm=lambda **kw: {"facts": [
        {"subject": "city", "predicate": "location", "content": "用户住在罗马。", "category": "correction-of-existing", "action": "UPDATE", "target_id": fid},
    ]})
    assert result["counts"] == {"UPDATE": 1}
    current = fact_store.load_facts()
    assert len(current) == 1 and current[0]["content"] == "用户住在罗马。"
    assert len(fact_store.load_facts(include_history=True)) == 2


@pytest.mark.parametrize("reply", [
    {"choices": [{"message": {"content": '{"facts": [{"subject": "language", "content": "用户偏好中文。", "category": "durable", "action": "ADD"}]}'}}]},
    {"output_text": '{"facts": [{"subject": "language", "content": "用户偏好中文。", "category": "durable", "action": "ADD"}]}'},
])

def test_provider_envelopes_store_facts(selective_home, reply):
    fact_pipeline.run_fact_extraction([{"role": "user", "content": "我长期偏好中文。"}], call_llm=lambda **kw: reply)
    assert [r["content"] for r in fact_store.load_facts()] == ["用户偏好中文。"]


def test_exact_content_across_subjects_is_noop(selective_home):
    fact_store.add_fact("language", "preference", "用户偏好中文。")
    result = fact_pipeline.run_fact_extraction([{"role": "user", "content": "我偏好中文。"}], call_llm=lambda **kw: {"facts": [
        {"subject": "reply_language", "content": "用户偏好中文。", "category": "durable", "action": "ADD"},
    ]})
    assert result["counts"] == {"NOOP": 1}
    assert len(fact_store.load_facts()) == 1


def test_repeated_update_target_has_one_current_value(selective_home):
    fact_store.add_fact("city", "location", "用户住在米兰。")
    fid = fact_store.load_facts()[0]["id"]
    result = fact_pipeline.run_fact_extraction([{"role": "user", "content": "我已搬到罗马，更正，现在住在都灵。"}], call_llm=lambda **kw: {"facts": [
        {"subject": "city", "content": "用户住在罗马。", "category": "durable", "action": "UPDATE", "target_id": fid},
        {"subject": "city", "content": "用户住在都灵。", "category": "durable", "action": "UPDATE", "target_id": fid},
    ]})
    assert result["counts"] == {"UPDATE": 1}
    assert [r["content"] for r in fact_store.load_facts()] == ["用户住在都灵。"]
