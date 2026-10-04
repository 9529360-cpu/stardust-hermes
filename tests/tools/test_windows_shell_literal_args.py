"""Non-path shell arguments reach Git Bash verbatim on Windows.

``_escape_shell_arg`` quotes PATHS: on Windows it rewrites drive paths to the Git
Bash ``/c/...`` form and every backslash to ``/``. Search patterns and ``python -c``
source went through it too, so on Windows every escaped regex (``call\\(``,
``foo\\.bar``) reached rg/grep with ``/`` in place of ``\\`` — an unclosed group or a
different pattern — and the UTF-16 rescue read lost its ``\\n`` / ``\\r\\n`` /
``\\ufeff`` escapes. These drive the real Git Bash pipeline, so they run on Windows.
"""

import pytest

from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations

pytestmark = pytest.mark.windows_only


@pytest.fixture
def ops(tmp_path):
    (tmp_path / "calls.ts").write_text("call(x)\nfoo.bar\nfooXbar\n", encoding="utf-8")
    return ShellFileOperations(LocalEnvironment(str(tmp_path), timeout=60), cwd=str(tmp_path))


def _hits(result):
    assert result.error is None, result.error
    return sorted((match.line_number, match.content.strip()) for match in result.matches)


def test_escaped_regex_reaches_ripgrep_verbatim(ops, tmp_path):
    if not ops._resolve_command("rg"):
        pytest.skip("ripgrep is not installed on this machine")
    assert _hits(ops.search(r"call\(x\)", path=str(tmp_path))) == [(1, "call(x)")]
    assert _hits(ops.search(r"foo\.bar", path=str(tmp_path))) == [(2, "foo.bar")]


def test_escaped_regex_reaches_grep_fallback_verbatim(ops, tmp_path, monkeypatch):
    resolve = ops._resolve_command
    monkeypatch.setattr(ops, "_resolve_command", lambda command: None if command == "rg" else resolve(command))
    assert _hits(ops.search(r"call\(x\)", path=str(tmp_path))) == [(1, "call(x)")]
    assert _hits(ops.search(r"foo\.bar", path=str(tmp_path))) == [(2, "foo.bar")]


def test_utf16_file_reads_as_lines(ops, tmp_path):
    """PowerShell 5.1 ``>`` and Notepad write UTF-16 with a BOM and CRLF."""
    if not (ops._has_command("python3") or ops._has_command("python")):
        pytest.skip("no python on the Git Bash PATH")
    target = tmp_path / "ps-output.txt"
    target.write_bytes("one\r\ntwo\r\nthree".encode("utf-16"))

    result = ops.read_file(str(target))

    assert result.error is None, result.error
    assert result.total_lines == 3
    assert [line.split("|", 1)[-1] for line in result.content.splitlines()] == ["one", "two", "three"]
    assert "﻿" not in result.content and "\r" not in result.content
