"""The per-file runner's teardown watchdog: a child hung in interpreter exit gets a stack dump.

The dump is diagnostic only. A child that finishes keeps its result; a child killed by the file
timeout is still reported exactly as before (the watchdog never exits the process).
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DUMP_MARKER = "most recent call first"
DUMP_ENV = "HERMES_TEST_TEARDOWN_DUMP_SECONDS"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_tests_parallel", REPO_ROOT / "scripts" / "run_tests_parallel.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def _write_test_file(tmp_path: Path, name: str, teardown_seconds: float | None) -> Path:
    body = "def test_passes():\n    pass\n"
    if teardown_seconds is not None:
        # Runs after pytest has printed its summary and returned: an interpreter-exit hang.
        body = ("import atexit, time\n"
                f"atexit.register(time.sleep, {teardown_seconds})\n") + body
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_teardown_hang_writes_stack_dump_and_child_result_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv(DUMP_ENV, "2")
    test_file = _write_test_file(tmp_path, "test_hangs_then_finishes.py", teardown_seconds=8)
    runner = _load_runner()

    _, rc, output, _counts, _wall = runner._run_one_file_once(test_file, [], tmp_path, 60)

    assert rc == 0, output
    assert DUMP_MARKER in output, output
    assert "exceeded" not in output, output
    assert [f for f, _ in runner._TEARDOWN_DUMPS] == [test_file]


def test_teardown_hang_killed_by_file_timeout_is_dumped_and_reported_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv(DUMP_ENV, "1")
    test_file = _write_test_file(tmp_path, "test_hangs_until_killed.py", teardown_seconds=60)
    runner = _load_runner()

    _, rc, output, _counts, _wall = runner._run_one_file_once(test_file, [], tmp_path, 15)

    assert rc == 124, output
    assert "(15s exceeded; process tree SIGKILL'd)" in output, output
    assert DUMP_MARKER in output, output


def test_clean_run_writes_no_stack_dump(tmp_path, monkeypatch):
    monkeypatch.delenv(DUMP_ENV, raising=False)
    test_file = _write_test_file(tmp_path, "test_clean_exit.py", teardown_seconds=None)
    runner = _load_runner()

    _, rc, output, _counts, _wall = runner._run_one_file_once(test_file, [], tmp_path, 120)

    assert rc == 0, output
    assert DUMP_MARKER not in output, output
    assert runner._TEARDOWN_DUMPS == []


def test_child_imports_hermes_cli_main_while_the_watchdog_is_loaded(tmp_path, monkeypatch):
    # hermes_cli.main reads `-p <profile>` from sys.argv when it is imported. The plugin must reach
    # the child without a `-p` on its command line, or every import of hermes_cli.main exits it.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    test_file = tmp_path / "test_imports_hermes_cli_main.py"
    test_file.write_text("import hermes_cli.main\n\n\ndef test_imports():\n    pass\n", encoding="utf-8")
    runner = _load_runner()

    _, rc, output, _counts, _wall = runner._run_one_file_once(test_file, [], REPO_ROOT, 120)

    assert rc == 0, output
    assert "does not exist" not in output, output
