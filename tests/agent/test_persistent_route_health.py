import json
from pathlib import Path
import subprocess
import sys
import time

from agent.error_classifier import FailoverReason
from agent import route_health


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _spawn_route_health_child(expression: str) -> subprocess.Popen:
    code = (
        "import json, sys\n"
        "from agent import route_health\n"
        "from agent.error_classifier import FailoverReason\n"
        "print('ready', flush=True)\n"
        "sys.stdin.readline()\n"
        f"print(json.dumps({expression}), flush=True)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _start_blocked_children(children: list[subprocess.Popen]) -> None:
    for proc in children:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
    for proc in children:
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.flush()


def _child_result(proc: subprocess.Popen):
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, stderr
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, stderr
    return json.loads(lines[-1])


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    route_health.reset_for_tests()


def test_failure_persists_and_fresh_caller_skips(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    cooldown = route_health.record_failure(
        "google", "gemini-test", "https://example.test/v1", FailoverReason.timeout,
    )
    assert cooldown == 30
    allowed, retry_after, state = route_health.allow_route(
        "google", "gemini-test", "https://example.test/v1",
    )
    assert allowed is False
    assert retry_after > 0
    assert state == "open"


def test_request_specific_failure_does_not_open_route(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert route_health.record_failure(
        "google", "gemini-test", "https://example.test/v1", FailoverReason.context_overflow,
    ) == 0
    assert route_health.snapshot()["routes"] == {}


def test_route_identity_strips_url_secrets(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure(
        "custom", "model", "https://user:secret@example.test/v1/?token=secret#x",
        FailoverReason.server_error,
    )
    raw = route_health.state_path().read_text(encoding="utf-8")
    assert "secret" not in raw
    assert "token" not in raw
    row = next(iter(json.loads(raw)["routes"].values()))
    assert row["base_url"] == "https://example.test/v1"


def test_expired_open_route_allows_one_half_open_probe(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    state = route_health.snapshot()
    row = next(iter(state["routes"].values()))
    row["cooldown_until"] = time.time() - 1
    route_health._write_state(state)

    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "half_open_probe")
    allowed, retry_after, status = route_health.allow_route("p", "m", "https://x.test")
    assert allowed is False
    assert retry_after > 0
    assert status == "half_open_busy"


def test_cross_process_half_open_probe_has_one_lease(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    state = route_health.snapshot()
    row = next(iter(state["routes"].values()))
    row["cooldown_until"] = time.time() - 1
    route_health._write_state(state)

    children = [
        _spawn_route_health_child(
            "route_health.allow_route('p', 'm', 'https://x.test')"
        )
        for _ in range(2)
    ]
    try:
        with route_health._state_file_lock(timeout=1.0):
            _start_blocked_children(children)
            # Let both children reach allow_route while this process owns the
            # cross-process lock. Neither may read/claim the lease yet.
            time.sleep(0.15)
            assert all(proc.poll() is None for proc in children)
        results = [_child_result(proc) for proc in children]
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    assert sorted(result[2] for result in results) == [
        "half_open_busy",
        "half_open_probe",
    ]


def test_cross_process_failure_counts_do_not_overwrite(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    children = [
        _spawn_route_health_child(
            "route_health.record_failure("
            "'p', 'm', 'https://x.test', "
            "FailoverReason.timeout)"
        )
        for _ in range(2)
    ]
    try:
        with route_health._state_file_lock(timeout=1.0):
            _start_blocked_children(children)
            time.sleep(0.15)
            assert all(proc.poll() is None for proc in children)
        results = [_child_result(proc) for proc in children]
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    assert sorted(results) == [30, 60]
    row = next(iter(route_health.snapshot()["routes"].values()))
    assert row["consecutive_failures"] == 2


def test_success_closes_circuit(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.rate_limit)
    route_health.record_success("p", "m", "https://x.test")
    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
    row = next(iter(route_health.snapshot()["routes"].values()))
    assert row["consecutive_failures"] == 0
    assert row["status"] == "healthy"


def test_malformed_state_fails_open(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.state_path().write_text("not-json", encoding="utf-8")
    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
