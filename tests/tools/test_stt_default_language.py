"""Chinese-first STT defaults, with measured bilingual auto-detection."""

from copy import deepcopy

import pytest

from hermes_cli.config import DEFAULT_CONFIG, load_config
from tools.transcription_local import build_local_transcribe_kwargs
from tools.transcription_tools import _resolve_stt_language


@pytest.mark.parametrize("stored", [None, "en", "zh", "", "auto"])
def test_default_and_existing_language_settings(tmp_path, monkeypatch, stored):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("STARDUST_LOCAL_STT_LANGUAGE", raising=False)
    if stored is not None:
        (tmp_path / "config.yaml").write_text(
            f'stt:\n  language: "{stored}"\n', encoding="utf-8"
        )
    stt = load_config()["stt"]
    assert stt["language"] == ("auto" if stored is None else stored)
    expected = stored if stored in ("en", "zh") else None
    assert _resolve_stt_language("local", stt) == expected
    assert build_local_transcribe_kwargs(stt).get("language") == expected


@pytest.mark.parametrize("provider", ["local", "groq", "openai", "mistral", "xai", "elevenlabs", "deepinfra"])
def test_provider_language_overrides_bilingual_default(provider):
    stt = deepcopy(DEFAULT_CONFIG["stt"])
    stt[provider] = {"language": "en"}
    assert _resolve_stt_language(provider, stt) == "en"


@pytest.mark.parametrize("value", ["auto", " AUTO "])
def test_provider_auto_stops_global_and_environment_fallback(monkeypatch, value):
    monkeypatch.setenv("STARDUST_LOCAL_STT_LANGUAGE", "ja")
    stt = {"language": "zh", "local": {"language": value}}
    assert _resolve_stt_language("local", stt) is None
    assert "language" not in build_local_transcribe_kwargs(stt)


def test_elevenlabs_language_code_alias():
    stt = {"language": "auto", "elevenlabs": {"language_code": "eng"}}
    assert _resolve_stt_language("elevenlabs", stt, extra_keys=("language_code",)) == "eng"
