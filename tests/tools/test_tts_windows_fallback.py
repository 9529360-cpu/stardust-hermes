"""Edge outages must produce usable local audio and observable fallback metadata."""
import json
import wave
from pathlib import Path

import pytest

from tools import tts_tool as tts


def wav(path):
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\x01\x00' * 1600)


@pytest.mark.parametrize('failure', ['403', 'timeout', 'empty'])
def test_edge_outage_returns_local_audio_and_reason(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(tts, '_load_tts_config', lambda: {'provider': 'edge'})
    monkeypatch.setattr(tts, '_select_builtin_engine', lambda p: (p, None))
    def edge(*args):
        if failure == '403':
            raise RuntimeError('HTTP 403')
        if failure == 'timeout':
            raise TimeoutError('Edge timeout')
        Path(args[1]).write_bytes(b'')
    monkeypatch.setattr(tts, '_run_edge_tts', edge)
    def local(text, path, cfg):
        path = str(Path(path).with_suffix('.wav'))
        wav(path)
        return path
    monkeypatch.setattr(tts, '_generate_windows_tts', local, raising=False)
    monkeypatch.setattr(tts, '_windows_tts_available', lambda: True, raising=False)
    result = json.loads(tts.text_to_speech_tool('你好，星尘。', str(tmp_path / 'speech.mp3')))
    assert result['success'], result
    assert result['provider'] == 'windows'
    assert result['fallback']['from'] == 'edge'
    assert result['fallback']['to'] == 'windows'
    assert result['fallback']['reason']
    with wave.open(result['file_path']) as f:
        assert f.getnframes() == 1600


@pytest.mark.parametrize('provider,enabled', [(None, False), ('openai', True)])
def test_fallback_opt_out_and_explicit_provider_are_honored(tmp_path, monkeypatch, provider, enabled):
    monkeypatch.setattr(tts, '_load_tts_config', lambda: {'provider': 'edge', 'fallback': {'enabled': enabled}})
    monkeypatch.setattr(tts, '_select_builtin_engine', lambda p: (p, None))
    def fail(*a, **kw):
        raise RuntimeError('HTTP 403')
    monkeypatch.setattr(tts, '_synthesize_builtin', fail)
    def forbidden(*a):
        pytest.fail('Local fallback must not replace an explicit non-Edge provider or opt-out')
    monkeypatch.setattr(tts, '_generate_windows_tts', forbidden, raising=False)
    result = json.loads(tts.text_to_speech_tool('你好', str(tmp_path / 'speech.mp3'), provider=provider))
    assert not result['success']


def test_empty_local_audio_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, '_load_tts_config', lambda: {'provider': 'edge'})
    monkeypatch.setattr(tts, '_select_builtin_engine', lambda p: (p, None))
    def fail(*a):
        raise RuntimeError('HTTP 403')
    monkeypatch.setattr(tts, '_run_edge_tts', fail)
    def empty(text, path, cfg):
        Path(path).write_bytes(b'')
        return path
    monkeypatch.setattr(tts, '_generate_windows_tts', empty, raising=False)
    monkeypatch.setattr(tts, '_windows_tts_available', lambda: True, raising=False)
    result = json.loads(tts.text_to_speech_tool('你好', str(tmp_path / 'speech.mp3')))
    assert not result['success']


def test_http_reports_fallback_and_rejects_empty_audio(tmp_path, monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_cli.web_routers import audio
    path = tmp_path / 'reply.wav'
    wav(path)
    async def scoped(profile, fn):
        return await asyncio.to_thread(fn)
    monkeypatch.setattr(audio, '_run_config_scoped', scoped)
    fallback = {'from': 'edge', 'to': 'windows', 'reason': 'http_403'}
    monkeypatch.setattr(tts, 'text_to_speech_tool', lambda text: json.dumps({
        'success': True, 'provider': 'windows', 'file_path': str(path), 'fallback': fallback}))
    app = FastAPI()
    app.include_router(audio.router)
    with TestClient(app) as client:
        result = client.post('/api/audio/speak', json={'text': '你好'}).json()
        assert result['ok']
        assert result['provider'] == 'windows'
        assert result['fallback'] == fallback
        assert result['data_url'].startswith('data:audio/wav;base64,')
        assert not path.exists()
        path.write_bytes(b'')
        response = client.post('/api/audio/speak', json={'text': '你好'})
        assert response.status_code == 500
        assert not path.exists()
