"""SDK response regression tests for fact extraction."""
import json
from types import SimpleNamespace as NS

import pytest
from openai.types.chat import ChatCompletion
from openai.types.responses import Response

from tools import fact_pipeline as fp, fact_store as fs

from tools.fact_pipeline import _parse_model_facts


def test_real_chatcompletion_extracts_facts():
    response = ChatCompletion(
        id="f12-0", object="chat.completion", created=1, model="default",
        choices=[{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps({"facts": [
                {"subject": "report-language", "content": "User prefers Chinese reports."}
            ]})}}],
    )
    facts = _parse_model_facts(response)
    assert len(facts) == 1
    assert facts[0]["content"] == "User prefers Chinese reports."


PAYLOAD = {"facts": [{"subject": "report-language", "content": "User prefers Chinese reports."}]}
TEXT = json.dumps(PAYLOAD)


@pytest.mark.parametrize("reply", [
    TEXT, PAYLOAD, {"content": TEXT}, NS(content=TEXT),
    {"choices": [{"message": {"content": TEXT}}]},
    NS(choices=[NS(message=NS(content=TEXT))]),
    {"content": [{"type": "thinking", "text": "not facts"}, {"type": "text", "text": TEXT}]},
    NS(content=[NS(type="text", text=TEXT)]),
    {"output": [{"type": "message", "content": [{"type": "output_text", "text": TEXT}]}]},
    NS(output=[NS(type="message", content=[NS(type="output_text", text=TEXT)])]),
    NS(output_text=TEXT),
    Response.model_validate({"id": "r", "object": "response", "created_at": 1,
        "model": "default", "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
        "output": [{"id": "m", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": TEXT, "annotations": []}]}]}),
    {"content": [{"type": "text", "text": TEXT[:20]}, {"type": "text", "text": TEXT[20:]}]},
])
def test_supported_response_shapes(reply):
    assert _parse_model_facts(reply)[0]["content"] == PAYLOAD["facts"][0]["content"]


@pytest.mark.parametrize("reply", [
    None, "", "not JSON", "{broken", {}, {"facts": None}, {"choices": []},
    {"choices": "bad"}, {"choices": {}}, {"choices": [None]},
    {"choices": [{"message": None}]}, {"choices": [{"message": {"content": 42}}]},
    {"content": [{"type": "thinking", "text": TEXT}]},
    {"content": [{"type": "tool_use", "input": PAYLOAD}]},
    {"content": [{"type": [], "text": TEXT}]},
    {"content": {"unexpected": TEXT}}, NS(content=123),
    {"output": [{"type": "reasoning", "content": TEXT}]},
])
def test_empty_or_malformed_responses_skip(reply):
    assert _parse_model_facts(reply) == []


def test_cyclic_malformed_response_skips():
    reply = {}
    reply["content"] = reply
    assert _parse_model_facts(reply) == []


def test_chatcompletion_pipeline_keeps_provenance(tmp_path, monkeypatch):
    import tools.memory_tool as mt

    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": True})
    response = ChatCompletion(id="f12", object="chat.completion", created=1, model="default",
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": TEXT}}])
    result = fp.run_fact_extraction([{"role": "user", "content": "Reports in Chinese please."}],
        source_session_id="f12-source", call_llm=lambda **kw: response)
    assert result["counts"] == {"ADD": 1}
    row, = fs.load_facts()
    assert row["source"] == "fact-pipeline"
    assert row["source_session_id"] == "f12-source"
    assert row["valid_from"]


def test_flag_off_preserves_store_bytes(tmp_path, monkeypatch):
    import tools.memory_tool as mt

    for key in ("HOME", "USERPROFILE", "STARDUST_HOME", "HERMES_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    fs.add_fact(subject="language", predicate="preference", content="User prefers Chinese reports.")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(mt, "get_builtin_memory_config", lambda config=None: {"fact_pipeline": False})
    def forbidden(**kw):
        pytest.fail("flag OFF must never call model")
    assert fp.run_fact_extraction([{"role": "user", "content": "new preference"}], call_llm=forbidden) == {"skipped": "flag-off"}
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
