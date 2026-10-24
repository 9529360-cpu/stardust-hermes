"""Privacy and lifecycle contracts for opt-in voice timing."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_disabled_never_creates_logs(tmp_path):
    from tools.voice_trace import VoiceTrace
    trace = VoiceTrace.begin({}, home=tmp_path)
    assert trace is None
    assert not (tmp_path / "logs").exists()


def test_complete_once_and_no_content(tmp_path):
    from tools.voice_trace import VoiceTrace, STAGES
    trace = VoiceTrace.begin({"voice": {"trace_enabled": True}}, home=tmp_path)
    trace.mark("listen_start")
    trace.mark("eos")
    trace.mark("stt_request")
    trace.mark("stt_result")
    trace.link_task("bg_123")
    trace.finish("not_observed")
    trace.finish("not_observed")
    rows = (tmp_path / "logs" / "voice-trace.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["turn_id"] == trace.turn_id
    assert row["task_id"] == "bg_123"
    assert set(row["stages"]) == set(STAGES)
    for stage in row["stages"].values():
        assert (stage["timestamp_ms"] is not None) != (stage["reason"] is not None)
    assert row["stages"]["eos"] != row["stages"]["network_first_audio_byte"]
    assert not any(key in rows[0] for key in ('transcript', 'data:audio', 'base64', 'pcm'))


def test_first_observation_wins_and_bad_input_rejected(tmp_path):
    from tools.voice_trace import VoiceTrace
    trace = VoiceTrace.begin({"voice": {"trace_enabled": True}}, home=tmp_path)
    trace.mark("eos", timestamp_ms=123.0, clock="renderer")
    trace.mark("eos", timestamp_ms=456.0, clock="renderer")
    with pytest.raises(ValueError):
        trace.mark("transcript")
    with pytest.raises(ValueError):
        trace.mark("stt_result", timestamp_ms=float("nan"))
    trace.finish("cancelled")
    row = json.loads((tmp_path / "logs" / "voice-trace.jsonl").read_text())
    assert row["stages"]["eos"]["timestamp_ms"] == 123


@pytest.mark.parametrize("flag", ["true", 1, None, [], {}])
def test_flag_requires_boolean_true(tmp_path, flag):
    from tools.voice_trace import VoiceTrace
    assert VoiceTrace.begin({"voice": {"trace_enabled": flag}}, home=tmp_path) is None
    assert not (tmp_path / "logs").exists()


def test_parallel_records_are_valid_and_profile_scoped(tmp_path):
    from tools.voice_trace import VoiceTrace
    def turn(_):
        trace = VoiceTrace.begin({"voice": {"trace_enabled": True}}, home=tmp_path)
        trace.finish("not_observed")
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(turn, range(10)))
    rows = [json.loads(line) for line in (tmp_path / "logs" / "voice-trace.jsonl").read_text().splitlines()]
    assert len({row["turn_id"] for row in rows}) == 10


def test_trace_endpoint_validates_content_and_is_off_by_default(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    from hermes_cli.web_routers import audio
    from tools.voice_trace import STAGES
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    enabled = {}
    monkeypatch.setattr(audio, "load_user_config_effective", lambda: enabled)
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    payload = {"turn_id": "test-http-turn", "task_id": "task-42", "stages": {
        stage: {"timestamp_ms": None, "reason": "not_observed"} for stage in STAGES
    }}
    assert client.post("/api/audio/trace", json=payload).json()["recorded"] is False
    assert not (home / "logs" / "voice-trace.jsonl").exists()
    enabled["voice"] = {"trace_enabled": True}
    assert client.post("/api/audio/trace", json={**payload, "transcript": "private"}).status_code == 422
    bad = {**payload, "stages": {**payload["stages"], "eos": {"reason": "private"}}}
    assert client.post("/api/audio/trace", json=bad).status_code == 400
    assert client.post("/api/audio/trace", json=payload).json()["recorded"] is True
    assert client.post("/api/audio/trace", json=payload).json()["recorded"] is False
    rows = (home / "logs" / "voice-trace.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["task_id"] == "task-42"
    assert set(row["stages"]) == set(STAGES)
