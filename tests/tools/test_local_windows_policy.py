"""Native Windows terminal policy assertions (no simulated host platform)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.environments.local import LocalEnvironment, _resolve_shell_init_files


@pytest.mark.windows_only
def test_windows_temp_cache_ignores_posix_overrides_and_is_shell_safe(tmp_path, monkeypatch):
    home = tmp_path / "profile with spaces"
    monkeypatch.setenv("STARDUST_HOME", str(home))
    for key in ("TERMINAL_TEMP_DIR", "TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(key, str(tmp_path))
    with patch.object(LocalEnvironment, "init_session", return_value=None):
        env = LocalEnvironment(cwd=str(tmp_path))
    try:
        result = env.get_temp_dir()
        assert Path(result) == home / "cache" / "terminal"
        assert "\\" not in result
        assert Path(result).is_dir()
        # The actual shell must create/read the snapshot under the spaced path.
        env.init_session()
        first = env.execute("export STARDUST_TEMP_POLICY_PROBE=kept")
        second = env.execute('printf "%s" "$STARDUST_TEMP_POLICY_PROBE"')
        assert first["returncode"] == second["returncode"] == 0
        assert second["output"] == "kept"
    finally:
        env.cleanup()


@pytest.mark.windows_only
def test_windows_auto_source_disabled_but_explicit_init_allowed(tmp_path, monkeypatch):
    rc = tmp_path / ".bashrc"
    rc.write_text("export STARDUST_INIT_POLICY=explicit\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with patch("tools.environments.local._read_terminal_shell_init_config", return_value=([], True)):
        assert _resolve_shell_init_files() == []
    with patch("tools.environments.local._read_terminal_shell_init_config", return_value=([str(rc)], True)):
        assert _resolve_shell_init_files() == [str(rc)]
