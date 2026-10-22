"""A partial continuity checkpoint must never replace the original transcript."""
import pytest
from agent.context_compressor import ContextCompressor


def test_incomplete_checkpoint_cannot_replace_history():
    from agent.context_compressor_summary import validate_continuity_summary
    with pytest.raises(ValueError, match="Resource State"):
        validate_continuity_summary("## Current Request\nBuild app\n## Satisfied Conditions\nTests passed\n"
                                    "## Unsatisfied Conditions\nRuntime check\n## Evidence\nreport.txt\n"
                                    "## Next Step\nCheck runtime")


def test_checkpoint_requires_explicit_unknown_instead_of_empty_field():
    from agent.context_compressor_summary import validate_continuity_summary
    with pytest.raises(ValueError, match="Evidence"):
        validate_continuity_summary("## Current Request\nBuild app\n## Satisfied Conditions\nNone\n"
                                    "## Unsatisfied Conditions\nRuntime check\n## Evidence\n"
                                    "## Resource State\nUnknown\n## Next Step\nCheck runtime")


def test_invalid_checkpoint_preserves_transcript_and_previous_summary():
    from unittest.mock import patch
    compressor = ContextCompressor(model="test", config_context_length=100000,
                                   quiet_mode=True, protect_first_n=0, protect_last_n=2,
                                   abort_on_summary_failure=True)
    compressor.require_continuity_fields = True
    compressor._previous_summary = "previous verified checkpoint"
    messages = [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"turn {i}: " + "sample " * 1000} for i in range(20)]
    with patch.object(compressor, "_call_summary_llm", return_value="## Goal\npartial checkpoint"):
        result = compressor.compress(messages, current_tokens=99999, force=True)
    assert result == messages
    assert compressor._last_compress_aborted
    assert compressor._previous_summary == "previous verified checkpoint"
    assert "Incomplete continuity summary" in compressor._last_summary_error


def test_summary_request_itself_obeys_budget():
    compressor = object.__new__(ContextCompressor)
    compressor.model = "test"
    compressor.provider = "custom"
    compressor.base_url = "http://localhost"
    compressor.api_key = "local"
    compressor.api_mode = "chat_completions"
    compressor.request_input_budget_tokens = 10
    with pytest.raises(ValueError, match="Compression input budget exceeded"):
        compressor._call_summary_llm("payload " * 1000, 0)


def test_summary_prompt_requests_recoverable_fields():
    compressor = object.__new__(ContextCompressor)
    compressor._previous_summary = ""
    compressor.tail_mode = "lean"
    prompt = compressor._build_summary_prompt("process p17 started; test passed", 1000, None, "", True)
    for heading in ["Current Request", "Satisfied Conditions", "Unsatisfied Conditions", "Evidence",
                    "Resource State", "Next Step"]:
        assert f"## {heading}" in prompt
