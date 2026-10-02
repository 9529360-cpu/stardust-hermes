"""#111105: a config.yaml replacement that keeps mtime and size (``cp -p``, ``rsync -t``, a
timestamp-pinning writer) must still invalidate the load_config() cache, while an unchanged
file keeps serving the cached object."""
import os
import shutil
from unittest.mock import patch

import pytest

from hermes_cli import config as config_mod
import utils


def _replace_pinning_mtime(path, content: str) -> None:
    before = path.stat()
    other = path.with_name("other.yaml")
    other.write_text(content, encoding="utf-8")
    shutil.copy2(other, path)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


@pytest.mark.skipif(os.name != "nt", reason="Windows ChangeTime collision")
def test_path_signature_detects_rewrite_even_when_windows_change_time_is_unchanged(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model: aaaa-route\n", encoding="utf-8")
    monkeypatch.setattr(utils, "_windows_change_time", lambda path: 12345)
    before = utils.path_signature(cfg)
    _replace_pinning_mtime(cfg, "model: bbbb-route\n")
    after = utils.path_signature(cfg)
    assert before != after


def test_load_config_sees_replacement_with_pinned_mtime_and_size(tmp_path):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        config_mod._LOAD_CONFIG_CACHE.clear()
        config_mod._RAW_CONFIG_CACHE.clear()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  default: aaaa-route\n", encoding="utf-8")
        first = config_mod._load_config_impl(want_deepcopy=False)
        assert config_mod._load_config_impl(want_deepcopy=False) is first  # unchanged file: cache hit
        _replace_pinning_mtime(cfg, "model:\n  default: bbbb-route\n")
        assert config_mod.load_config()["model"]["default"] == "bbbb-route"


def test_read_raw_config_sees_replacement_with_pinned_mtime_and_size(tmp_path):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        config_mod._RAW_CONFIG_CACHE.clear()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  default: aaaa-route\n", encoding="utf-8")
        assert config_mod.read_raw_config()["model"]["default"] == "aaaa-route"
        _replace_pinning_mtime(cfg, "model:\n  default: bbbb-route\n")
        assert config_mod.read_raw_config()["model"]["default"] == "bbbb-route"


def test_load_env_sees_replacement_with_pinned_mtime_and_size(tmp_path):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        config_mod.invalidate_env_cache()
        env = tmp_path / ".env"
        env.write_text("JARVIS_KEY=aaaa\n", encoding="utf-8")
        assert config_mod.load_env()["JARVIS_KEY"] == "aaaa"
        _replace_pinning_mtime(env, "JARVIS_KEY=bbbb\n")
        assert config_mod.load_env()["JARVIS_KEY"] == "bbbb"
