"""Windows reliability contracts derived from observed tool failures."""
import json
import os
import shlex
import sys
import time
from unittest.mock import patch

import pytest

from tools.terminal_hints import annotate_failure


def test_powershell_cmdlet_is_shell_mismatch_not_missing_install():
    hint = annotate_failure('Get-ChildItem', 127,
                            '/usr/bin/bash: line 5: Get-ChildItem: command not found')
    assert 'PowerShell' in hint
    assert '-Command' in hint
    assert 'single quotes' in hint
    assert 'install' not in hint.lower()


def test_python_encoding_error_has_precise_recovery():
    hint = annotate_failure('python script.py', 1,
                            "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xcf")
    assert hint and 'encoding' in hint.lower()
    assert 'subprocess' in hint.lower()


@pytest.mark.windows_only
def test_windows_child_utf8_defaults_and_explicit_override():
    from tools.environments.local import _sanitize_subprocess_env
    env = _sanitize_subprocess_env({})
    assert env['PYTHONUTF8'] == '1'
    assert env['PYTHONIOENCODING'] == 'utf-8'
    override = _sanitize_subprocess_env({'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'gb18030'})
    assert override['PYTHONUTF8'] == '0'
    assert override['PYTHONIOENCODING'] == 'gb18030'


@pytest.mark.windows_only
def test_live_foreground_unicode_cwd_and_utf8_python(tmp_path, monkeypatch):
    from tools.environments.local import LocalEnvironment
    home = tmp_path / 'home'
    monkeypatch.setenv('STARDUST_HOME', str(home))
    monkeypatch.delenv('PYTHONUTF8', raising=False)
    monkeypatch.delenv('PYTHONIOENCODING', raising=False)
    cwd = tmp_path / '中文 路径'
    cwd.mkdir()
    env = LocalEnvironment(cwd=str(cwd), timeout=20)
    try:
        script = "import sys; print('中文 ┍'); print(sys.stdout.encoding); assert sys.flags.utf8_mode == 1"
        result = env.execute(f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}')
        assert result['returncode'] == 0, result
        assert '中文 ┍' in result['output']
        assert 'utf-8' in result['output'].lower()
        assert os.path.samefile(env.cwd, cwd)
    finally:
        env.cleanup()


@pytest.mark.windows_only
def test_live_background_python_utf8(tmp_path, monkeypatch):
    from tools.process_registry import ProcessRegistry
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('PYTHONUTF8', raising=False)
    monkeypatch.delenv('PYTHONIOENCODING', raising=False)
    reg = ProcessRegistry()
    script = "import sys; print('中文 ┍'); assert sys.flags.utf8_mode == 1; sys.exit(7)"
    session = reg.spawn_local(f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}', cwd=str(tmp_path))
    try:
        deadline = time.monotonic() + 20
        while not session.exited and time.monotonic() < deadline:
            time.sleep(.05)
        assert session.exited
        assert session.exit_code == 7
        assert '中文 ┍' in session.output_buffer
    finally:
        if not session.exited:
            reg.kill_process(session.id)


def test_capture_failure_advises_public_action_and_preserves_error():
    from tools.computer_use.cua_backend import CuaDriverBackend
    import tools.computer_use_tool  # register the public dispatch surface
    from tools.registry import registry
    backend = CuaDriverBackend()
    backend._active_pid, backend._active_window_id = 123, 456
    class Session:
        def call_tool(self, name, args):
            return {'isError': True, 'data': 'No window with window_id 456 exists. Call list_windows for candidates.'}
    backend._session = Session()
    with patch('tools.computer_use.tool._get_backend', return_value=backend):
        result = json.loads(registry.dispatch('computer_use', {'action': 'capture', 'pid': 123, 'window_id': 456}))
    assert 'No window' in result['error']
    assert "action='list_windows'" in result['hint']
    assert 'pid' in result['hint'] and 'window_id' in result['hint']
    assert backend._active_pid is None
    assert backend._active_window_id is None
