"""Saved-file previews must expose buried diagnostics without growing the budget."""
import json
from pathlib import Path


def log_text():
    return "noise row\n" * 1000 + "Traceback (most recent call last):\nValueError: buried problem\nFAILED tests/test_build.py::test_compile\nwarning: deprecated input\nexit code: 7\n" + "noise row\n" * 1000


def test_saved_preview_indexes_middle_within_budget():
    from tools.tool_output_truncate import saved_output_preview
    text = log_text()
    preview = saved_output_preview(text, 1500)
    assert len(preview) <= 1500
    for number, line in [(1001, "Traceback"), (1002, "ValueError"), (1003, "FAILED"), (1004, "warning"), (1005, "exit code")]:
        assert f"L{number}: {line}" in preview
    assert preview.startswith("noise row") and preview.endswith("noise row\n")


def test_persisted_result_uses_same_preview_budget(tmp_path, monkeypatch):
    from tools.budget_config import BudgetConfig
    from tools.tool_result_storage import maybe_persist_tool_result
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    text = log_text()
    result = maybe_persist_tool_result(text, "terminal", "summary", config=BudgetConfig(default_result_size=2000))
    assert "L1002: ValueError: buried problem" in result
    assert (tmp_path / "cache/spillover/summary.txt").read_text(encoding="utf-8") == text


def test_execute_code_summary_matches_saved_file(tmp_path, monkeypatch):
    from tools import code_execution_tool as code
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setattr(code, "MAX_STDOUT_BYTES", 1500)
    preview, meta = code._truncate_stdout_text(log_text())
    assert "L1002: ValueError: buried problem" in preview
    assert len(preview.encode("utf-8")) <= 1500
    assert Path(meta["stdout_spill_path"]).read_text(encoding="utf-8").splitlines()[1001] == "ValueError: buried problem"


def test_terminal_summary_redacted_and_exit_unchanged(tmp_path, monkeypatch):
    from tools.terminal_tool import terminal_tool
    from tests.tools._shell_helpers import PYTHON
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    from hermes_constants import hermes_home_key
    from tools import tool_output_limits as limits
    monkeypatch.setattr(limits, "_cached_limits", {hermes_home_key(): {"max_bytes": 2000, "max_lines": 2000, "max_line_length": 2000}})
    result = json.loads(terminal_tool(PYTHON + ' -c "print(\'noise\\n\'*1000); print(\'ValueError: buried problem\'); print(\'noise\\n\'*1000)"', task_id="summary-test"))
    assert result["exit_code"] == 0 and result["error"] is None
    saved = Path(result["full_output_path"]).read_text(encoding="utf-8").splitlines()
    import re
    number = int(re.search(r"L(\d+): ValueError: buried problem", result["output"])[1])
    assert saved[number - 1] == "ValueError: buried problem"
    assert len(result["output"]) <= 2000


def test_saved_preview_line_endings_and_candidate_overflow():
    from tools.tool_output_truncate import saved_output_preview
    text = "noise\r" * 1000 + "ValueError: middle\r" + "noise\r" * 1000
    assert "L1001: ValueError: middle" in saved_output_preview(text, 1500)
    many = "".join(f"warning: item {i}\n" for i in range(10000))
    result = saved_output_preview(many, 3000)
    assert len(result) <= 3000
    assert "L1: warning: item 0" in result and "L10000: warning: item 9999" in result
    assert result.count("\nL") <= 12


def test_unicode_summary_is_bounded_and_no_match_keeps_legacy():
    from tools.tool_output_truncate import saved_output_preview
    text = '普通行\n' * 1000 + 'ValueError: 中文问题\n' + '尾部\n' * 1000
    result = saved_output_preview(text, 1000, byte_budget=True)
    assert len(result.encode('utf-8')) <= 1000
    assert 'L1001: ValueError: 中文问题' in result
    assert saved_output_preview('ok\n' * 1000, 1000, fallback='legacy') == 'legacy'


def test_unwritable_archive_does_not_claim_saved(tmp_path, monkeypatch):
    from tools.tool_result_storage import maybe_persist_tool_result
    from tools.budget_config import BudgetConfig
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('block')
    monkeypatch.setenv('STARDUST_HOME', str(blocked))
    result = maybe_persist_tool_result(log_text(), 'terminal', 'blocked', config=BudgetConfig(default_result_size=2000))
    assert 'could not be saved' in result
    assert 'Full output saved to:' not in result and 'Key lines' not in result


def test_extraction_failure_keeps_saved_preview(tmp_path, monkeypatch):
    from tools.tool_result_storage import maybe_persist_tool_result
    from tools.budget_config import BudgetConfig
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path))
    def fail(*args, **kwargs):
        raise ValueError('extractor failed')
    monkeypatch.setattr('tools.tool_output_truncate.saved_output_preview', fail)
    result = maybe_persist_tool_result(log_text(), 'terminal', 'fallback', config=BudgetConfig(default_result_size=2000))
    assert 'Full output saved to:' in result and 'Key lines' not in result
    assert (tmp_path / 'cache/spillover/fallback.txt').exists()
