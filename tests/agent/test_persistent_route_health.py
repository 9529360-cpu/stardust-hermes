import json
import multiprocessing
import os
import time

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


def test_non_mapping_route_row_does_not_break_health_updates(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    key, _ = route_health.route_identity("p", "m", "https://x.test")
    route_health._write_state({"version": 1, "routes": {key: "corrupt"}})

    assert route_health.allow_route("p", "m", "https://x.test") == (True, 0, "healthy")
    assert route_health.record_failure("p", "m", "https://x.test", FailoverReason.timeout) == 30
    route_health.record_success("p", "m", "https://x.test")
    assert route_health.snapshot()["routes"][key]["status"] == "healthy"
