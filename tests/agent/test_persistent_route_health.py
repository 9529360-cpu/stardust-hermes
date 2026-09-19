import errno
import json
import multiprocessing
import os
import time

import pytest

from agent.error_classifier import FailoverReason
from agent import route_health


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    route_health.reset_for_tests()


def _claim_probe_with_held_write(home, writer_entered, release_writer, results):
    os.environ["HERMES_HOME"] = home
    from agent import route_health as child_route_health

    original_write = child_route_health._write_state

    def held_write(state, path=None):
        writer_entered.set()
        assert release_writer.wait(timeout=10)
        return original_write(state, path)

    child_route_health._write_state = held_write
    results.put(("first", child_route_health.allow_route("p", "m", "https://x.test")))


def _claim_probe(home, results):
    os.environ["HERMES_HOME"] = home
    from agent import route_health as child_route_health

    results.put(("second", child_route_health.allow_route("p", "m", "https://x.test")))


def test_relative_health_file_is_scoped_to_active_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(
        route_health,
        "_config",
        lambda: {"persistent_health": True, "health_file": "runtime/route-health.json"},
    )
    first = tmp_path / "profile-a"
    second = tmp_path / "profile-b"

    monkeypatch.setenv("HERMES_HOME", str(first))
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    first_path = first / "runtime" / "route-health.json"
    assert route_health.state_path() == first_path
    assert first_path.exists()

    monkeypatch.setenv("HERMES_HOME", str(second))
    second_path = second / "runtime" / "route-health.json"
    assert route_health.state_path() == second_path
    assert second_path != first_path
    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")

    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    assert second_path.exists()
    assert first_path.read_bytes() != b""
    assert second_path.read_bytes() != b""


def test_relative_health_file_follows_context_scoped_profile_override(monkeypatch, tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process-default"))
    monkeypatch.setattr(
        route_health,
        "_config",
        lambda: {"persistent_health": True, "health_file": "runtime/route-health.json"},
    )
    first = tmp_path / "profiles" / "first"
    second = tmp_path / "profiles" / "second"

    first_token = set_hermes_home_override(first)
    try:
        route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
        assert route_health.state_path() == (first / "runtime" / "route-health.json").resolve(strict=False)
    finally:
        reset_hermes_home_override(first_token)

    second_token = set_hermes_home_override(second)
    try:
        assert route_health.state_path() == (second / "runtime" / "route-health.json").resolve(strict=False)
        assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
    finally:
        reset_hermes_home_override(second_token)


def test_relative_health_file_cannot_escape_profile(monkeypatch, tmp_path, caplog):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        route_health,
        "_config",
        lambda: {"persistent_health": True, "health_file": "../shared/route-health.json"},
    )
    route_health._WARNED_UNSAFE_RELATIVE_PATHS.clear()

    expected = home / "route-health.json"
    assert route_health.state_path() == expected
    assert "must stay inside the active HERMES_HOME" in caplog.text

    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)

    assert expected.exists()
    assert not (tmp_path / "shared" / "route-health.json").exists()


def test_absolute_health_file_override_remains_explicit(monkeypatch, tmp_path):
    explicit = tmp_path / "shared-operator-state.json"
    monkeypatch.setattr(
        route_health,
        "_config",
        lambda: {"persistent_health": True, "health_file": str(explicit)},
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))

    assert route_health.state_path() == explicit


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


def test_half_open_probe_claim_is_serialized_across_processes(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout)
    state = route_health.snapshot()
    row = next(iter(state["routes"].values()))
    row["cooldown_until"] = time.time() - 1
    route_health._write_state(state)

    context = multiprocessing.get_context("spawn")
    writer_entered = context.Event()
    release_writer = context.Event()
    results = context.Queue()
    first = context.Process(
        target=_claim_probe_with_held_write,
        args=(str(tmp_path), writer_entered, release_writer, results),
    )
    second = context.Process(target=_claim_probe, args=(str(tmp_path), results))

    first.start()
    assert writer_entered.wait(timeout=10)
    second.start()
    time.sleep(0.25)
    assert second.is_alive()
    release_writer.set()

    for process in (first, second):
        process.join(timeout=15)
        assert process.exitcode == 0

    claimed = dict(results.get(timeout=2) for _ in range(2))
    assert claimed["first"] == (True, 0, "half_open_probe")
    assert claimed["second"][0] is False
    assert claimed["second"][2] == "half_open_busy"


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


def test_structurally_corrupt_route_row_fails_open(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    key, identity = route_health.route_identity("p", "m", "https://x.test")
    route_health._write_state({
        "version": 1,
        "routes": {
            key: {
                **identity,
                "status": "healthy",
                "cooldown_until": "not-a-number",
                "probe_until": {"bad": "shape"},
                "consecutive_failures": "broken",
            },
        },
    })

    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
    assert route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout) == 30
    assert route_health.snapshot()["routes"][key]["consecutive_failures"] == 1


def test_unknown_status_and_nonfinite_values_fail_open(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    key, identity = route_health.route_identity("p", "m", "https://x.test")
    route_health._write_state({
        "version": 1,
        "routes": {
            key: {
                **identity,
                "status": "mystery-state",
                "cooldown_until": float("inf"),
                "probe_until": float("nan"),
                "consecutive_failures": float("inf"),
            },
        },
    })

    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
    rows = route_health.health_rows("p", "m", "https://x.test")
    assert rows[0]["status"] == "healthy"
    assert rows[0]["consecutive_failures"] == 0
    assert route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout) == 30
    assert route_health.snapshot()["routes"][key]["consecutive_failures"] == 1


def test_lock_error_classification_retries_only_contention():
    assert route_health._is_lock_contention_errno(OSError(errno.EAGAIN, "busy"))
    assert route_health._is_lock_contention_errno(OSError(errno.EACCES, "busy"))
    assert not route_health._is_lock_contention_errno(OSError(errno.EMFILE, "too many files"))
    assert not route_health._is_lock_contention_errno(OSError(errno.ENOSPC, "disk full"))


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="host has no no-follow open flag")
def test_state_lock_refuses_symlink_without_touching_referent(tmp_path):
    state = tmp_path / "route-health.json"
    lock = tmp_path / ".route-health.json.lock"
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"do-not-touch")
    lock.symlink_to(outside)

    with pytest.raises(OSError):
        with route_health._state_file_lock(state):
            pass

    assert outside.read_bytes() == b"do-not-touch"


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "fchmod"), reason="POSIX fd mode contract")
def test_state_lock_tightens_preexisting_permissions(tmp_path):
    state = tmp_path / "route-health.json"
    lock = tmp_path / ".route-health.json.lock"
    lock.write_bytes(b"")
    lock.chmod(0o666)

    with route_health._state_file_lock(state):
        assert lock.stat().st_mode & 0o777 == 0o600


def test_non_mapping_route_row_does_not_break_health_updates(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    key, _ = route_health.route_identity("p", "m", "https://x.test")
    route_health._write_state({"version": 1, "routes": {key: "corrupt"}})

    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
    assert route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout) == 30
    route_health.record_success("p", "m", "https://x.test")
    assert route_health.snapshot()["routes"][key]["status"] == "healthy"
