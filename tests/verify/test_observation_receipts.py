"""Observation metadata must preserve the runner's actual evidence boundary."""
import json
import socket
import sys

import pytest

from agent import task_acceptance
from agent.verification_evidence import (
    _connect, record_verify_run, verification_status,
)
from agent.verify.runner import PhaseResult, ReadinessResult, VerifyResult
from hermes_cli.verify_cmd import run_verify_command
from tests.verify.test_ledger_and_nudge_integration import make_args, _workspace


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STARDUST_VERIFY_ON_STOP", "1")
    monkeypatch.setenv("STARDUST_SESSION_ID", "observation-test")


def test_real_passing_test_and_failing_start_are_separate(tmp_path, capsys):
    python = '"' + sys.executable.replace("\\", "/") + '"'
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    project = _workspace(tmp_path, manifest_recipe={
        "name": "tiny-app", "test": [python + " -m unittest -v"],
        "start": python + " app.py", "port": port,
    })
    (project / "app.py").write_text(
        'def add(a, b):\n    return a + b\n'
        'if __name__ == "__main__":\n    raise RuntimeError("startup failed")\n', encoding="utf-8",
    )
    (project / "test_app.py").write_text(
        'import unittest\nfrom app import add\nclass TestApp(unittest.TestCase):\n'
        '    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n', encoding="utf-8",
    )
    assert run_verify_command(make_args(project, ready_timeout=0.1)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["phases"][0]["ok"] is True
    assert payload["readiness"]["ready"] is False
    status = verification_status(session_id="observation-test", cwd=project)
    assert status["status"] == "failed"
    receipt = status["evidence"]
    assert receipt["evidence_tier"] == "unknown"  # aggregate is not a test
    test, runtime = receipt["observations"]
    assert (test["tier"], test["status"], test["exit_code"]) == ("test", "passed", 0)
    assert test["source"] == "agent.verify.runner._run_phase_command"
    assert (runtime["tier"], runtime["status"], runtime["ready"]) == ("runtime", "failed", False)
    assert runtime["source"] == "agent.verify.runner._poll_readiness"
    assert runtime["observation"] == "http-reachability"
    contract = task_acceptance.begin("observation-test", "Acceptance criteria:\n- [user-flow] Click Save in real UI", project)
    contract["plan"] = {"steps": ["run verify", "click Save"]}
    assert any("requires user-flow; no current evidence" in item for item in task_acceptance.unmet(contract))


def test_skip_start_records_only_observed_phases(tmp_path, capsys):
    project = _workspace(tmp_path, manifest_recipe={"name": "test-only", "test": ["echo ok"], "start": "unused"})
    assert run_verify_command(make_args(project, skip_start=True)) == 0
    capsys.readouterr()
    receipt = verification_status(session_id="observation-test", cwd=project)["evidence"]
    assert receipt["scope"] == "targeted"
    assert [(o["phase"], o["tier"]) for o in receipt["observations"]] == [("test", "test")]


def test_phase_facts_and_reachability_are_not_health_or_ui(tmp_path):
    project = _workspace(tmp_path)
    result = VerifyResult("facts", [
        PhaseResult("bootstrap", "setup", 0, 0.1, ""),
        PhaseResult("build", "compile", 0, 0.2, ""),
        PhaseResult("test", "tests", None, 1.0, "", timed_out=True),
    ], ReadinessResult("http://127.0.0.1/", True, 503, 0.1))
    event = record_verify_run(root=project, ok=result.ok, result=result)
    assert event["status"] == "failed"
    observations = event["observations"]
    assert [o["tier"] for o in observations] == ["unknown", "build", "test", "runtime"]
    assert observations[2]["timed_out"] is True
    assert observations[2]["exit_code"] is None
    assert observations[3]["status"] == "passed"
    assert observations[3]["status_code"] == 503
    assert observations[3]["observation"] == "http-reachability"
    assert not any(o["tier"] == "user-flow" for o in observations)


def test_refused_compose_build_is_not_observed_build(tmp_path, monkeypatch):
    from agent.verify.recipes import Recipe
    from agent.verify import runner

    monkeypatch.setattr(runner, "_compose_live_state_reason", lambda root: "live containers")
    result = runner.run_verify(tmp_path, Recipe(name="compose", kind="compose", build=["docker compose build"]))
    observation, = result.observations()
    assert result.ok is False
    assert observation["executed"] is False
    assert observation["tier"] == "unknown"
    assert observation["source"] == "agent.verify.runner._compose_live_state_reason"


def test_legacy_null_tier_stays_unknown_after_migration(tmp_path):
    project = _workspace(tmp_path)
    event = record_verify_run(root=project, session_id="legacy", ok=True)
    with _connect() as conn:
        conn.execute("UPDATE verification_events SET evidence_tier=NULL WHERE id=?", (event["id"],))
        conn.execute("ALTER TABLE verification_events DROP COLUMN observations_json")
        conn.commit()
    status = verification_status(session_id="legacy", cwd=project)
    assert status["status"] == "passed"  # existing stop behavior is unchanged
    assert status["evidence"]["evidence_tier"] == "unknown"
    assert status["evidence"]["observations"] == []
    with _connect() as conn:
        assert conn.execute("SELECT evidence_tier, observations_json FROM verification_events WHERE id=?", (event["id"],)).fetchone()[:] == (None, None)
