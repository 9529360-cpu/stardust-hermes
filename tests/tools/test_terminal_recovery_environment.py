"""Environment facts must belong to the terminal backend, not its host."""
from tools.terminal_hints import annotate_failure


def test_windows_powershell_missing_command_has_native_diagnostic():
    hint = annotate_failure('bash absent.sh', 1,
        "bash : The term 'bash' is not recognized as the name of a cmdlet",
        environment="Windows; shell=PowerShell")
    assert "Get-Command" in hint
    assert "Windows; shell=PowerShell" in hint
    assert "sudo" not in hint


def test_windows_native_path_failure_uses_drive_path():
    hint = annotate_failure('python /d/data/input.py', 2,
        "python: can't open file '/d/data/input.py': [Errno 2] No such file or directory",
        environment="Windows; shell=bash")
    assert "D:/" in hint
    assert "MSYS" in hint


def test_remote_without_environment_keeps_host_facts_out():
    from unittest.mock import patch
    from tools.terminal_tool_result import _failure_hint
    with patch('tools.env_probe.get_environment_probe_line', side_effect=AssertionError('host probe')):
        hint = _failure_hint('missing', 127, 'bash: missing: command not found', None, env_type='ssh')
    assert "Windows" not in hint


def test_windows_permission_does_not_suggest_sudo():
    hint = annotate_failure('type secret', 1, 'Access is denied',
                            environment="Windows; shell=PowerShell")
    assert "Get-Acl" in hint
    assert "sudo" not in hint
