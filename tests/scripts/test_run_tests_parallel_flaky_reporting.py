"""Regression coverage for complete, durable retry-only flake diagnostics."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_flaky_report_keeps_path_and_full_first_attempt_traceback(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    report_dir = tmp_path / "reports"
    spec = importlib.util.spec_from_file_location("run_tests_parallel_flaky_test", runner)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv(module._FLAKY_REPORT_DIR_ENV, str(report_dir))

    failing_file = Path("tests/example_flaky.py")
    first_attempt = (
        "--- first-attempt output ---\n"
        "Traceback (most recent call last):\n"
        "  File \"tests/example_flaky.py\", line 10, in test_race\n"
        "AssertionError: diagnostic-flake-sentinel\n"
        "--- retry output ---\n1 passed\n"
    )
    formatted = module._format_flaky_report(
        [(failing_file, first_attempt)], repo_root
    )
    assert "tests/example_flaky.py" in formatted
    assert "diagnostic-flake-sentinel" in formatted
    assert "Traceback (most recent call last)" in formatted
    assert "first-attempt output" in formatted
    assert "retry output" in formatted
    assert module._write_flaky_report(
        [(failing_file, first_attempt)], repo_root
    ) is not None
    reports = list(report_dir.glob("flaky-report-*.txt"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert "tests/example_flaky.py" in report
    assert "diagnostic-flake-sentinel" in report
    assert "Traceback (most recent call last)" in report
    assert "first-attempt output" in report
    assert "retry output" in report


def test_flaky_report_requires_explicit_directory(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    (tmp_path / "evidence" / "round2").mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("run_tests_parallel_no_report_test", runner)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv(module._FLAKY_REPORT_DIR_ENV, raising=False)

    result = module._write_flaky_report(
        [(Path("tests/example_flaky.py"), "complete traceback")], tmp_path
    )

    assert result is None
    assert list((tmp_path / "evidence" / "round2").iterdir()) == []


def test_flaky_details_are_printed_once_at_file_completion(tmp_path: Path, capsys) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    spec = importlib.util.spec_from_file_location("run_tests_parallel_print_test", runner)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = (
        Path("tests/example_flaky.py"),
        "--- first-attempt output ---\nAssertionError: instant-diagnostic-sentinel\n"
        "--- retry output ---\n1 passed\n",
    )

    module._print_flaky_details(result, repo_root)
    module._print_flaky_summary(1, tmp_path / "flaky-report.txt")
    output = capsys.readouterr().out

    assert output.count("instant-diagnostic-sentinel") == 1
    assert output.count("=== ⚠ 1 FLAKY file(s)") == 1
    assert "Full flake report:" in output
