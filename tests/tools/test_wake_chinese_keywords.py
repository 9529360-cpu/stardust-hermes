"""Chinese KWS package integrity and official tokenizer contracts."""
import hashlib
import json

import pytest

from tools import wake_word_engines as engines


def test_chinese_package_missing_or_corrupt_refuses_before_native_load(tmp_path, monkeypatch):
    spec = {"files": {"tokens.txt": {"sha256": hashlib.sha256(b"valid").hexdigest()}}}
    monkeypatch.setattr(engines, "_chinese_manifest", lambda: spec, raising=False)
    with pytest.raises(RuntimeError, match="missing.*tokens.txt"):
        engines._verify_chinese_model(tmp_path)
    (tmp_path / "tokens.txt").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="SHA256.*tokens.txt"):
        engines._verify_chinese_model(tmp_path)
    (tmp_path / "tokens.txt").write_bytes(b"valid")
    engines._verify_chinese_model(tmp_path)


def test_chinese_keywords_use_official_ppinyin_and_validate_vocabulary(tmp_path, monkeypatch):
    import sys
    import types
    seen = []
    def convert(texts, **kwargs):
        seen.append((texts, kwargs))
        return [["official", "tokens"]]
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.SimpleNamespace(text2token=convert))
    (tmp_path / "tokens.txt").write_text("official 1\ntokens 2\n", encoding="utf-8")
    assert engines._chinese_tokens(["星尘"], tmp_path) == [["official", "tokens"]]
    assert seen[0][0] == ["星尘"]
    assert seen[0][1]["tokens_type"] == "ppinyin"
    (tmp_path / "tokens.txt").write_text("official 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="vocabulary"):
        engines._chinese_tokens(["星尘"], tmp_path)


def test_manifest_pins_all_native_inputs_and_license():
    spec = json.loads(engines.Path(engines.__file__).with_name("voice_model_manifest.json").read_text(encoding="utf-8"))
    model = spec["chinese_kws"]
    assert model["tokens_type"] == "ppinyin"
    assert set(model["roles"]) == {"tokens", "encoder", "decoder", "joiner"}
    for name in model["roles"].values():
        item = model["files"][name]
        assert len(item["sha256"]) == 64
        assert item["license"] == "Apache-2.0"
        assert item["source_url"].startswith("https://github.com/k2-fsa/")
