"""Captured command output: UTF-8 lines stay UTF-8, other lines decode with the Windows console code page.

Field case 2026-10-01 (Chinese Windows, OEM code page 936): PowerShell and cmd write GBK into the
terminal pipe, the readers decoded it as UTF-8, and every Chinese message (including error text)
reached the model as U+FFFD, so the agent could not read why a command failed and retried blind.
"""

import pytest


def _console_text():
    """Text the runner's console code page can encode (Chinese on the reporting machine)."""
    import ctypes

    code_page = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    for text in ("中文报错", "Café crème"):
        try:
            text.encode(code_page)
            return text
        except UnicodeEncodeError:
            continue
    pytest.skip(f"no sample text encodable in {code_page}")


def _powershell_echo(text):
    return f"powershell.exe -NoProfile -NonInteractive -Command \"Write-Output '{text}'\""


def test_mixed_utf8_and_code_page_lines_split_across_reads_decode_cleanly():
    from tools.environments.base_output import _ConsoleCodePageFallbackDecoder

    stream = "python 说：完成\n".encode("utf-8") + "错误：找不到路径\r\n".encode("gbk") + "尾巴".encode("gbk")
    decoder = _ConsoleCodePageFallbackDecoder("gbk")

    out = "".join(decoder.decode(stream[i:i + 3]) for i in range(0, len(stream), 3))
    out += decoder.decode(b"", final=True)

    assert out == "python 说：完成\n错误：找不到路径\r\n尾巴"


@pytest.mark.parametrize("raw, expected", [
    (b"ok \xe2\x82", "ok �"),  # a UTF-8 line cut mid-character at exit
    (b"before \xff\xfe after\n", "before �� after\n"),  # bytes no code page decodes
])
def test_bytes_that_are_not_a_clean_code_page_line_keep_the_replacement(raw, expected):
    from tools.environments.base_output import _ConsoleCodePageFallbackDecoder

    assert _ConsoleCodePageFallbackDecoder("gbk").decode(raw, final=True) == expected


def test_an_unterminated_ascii_prompt_is_not_held_back():
    from tools.environments.base_output import _ConsoleCodePageFallbackDecoder

    decoder = _ConsoleCodePageFallbackDecoder("gbk")

    assert decoder.decode(b"Password: ") == "Password: "


@pytest.mark.windows_only
def test_foreground_powershell_output_reaches_the_model_readable(tmp_path):
    from tools.environments.local import LocalEnvironment

    text = _console_text()
    result = LocalEnvironment(cwd=str(tmp_path), timeout=60).execute(_powershell_echo(text))

    assert text in result["output"]


@pytest.mark.windows_only
def test_background_powershell_output_reaches_the_model_readable(tmp_path):
    from tools.process_registry import ProcessRegistry

    text = _console_text()
    child = ProcessRegistry().spawn_local(_powershell_echo(text), cwd=str(tmp_path), task_id="console-decoding")
    child._reader_thread.join(timeout=60)

    assert text in child.output_buffer
