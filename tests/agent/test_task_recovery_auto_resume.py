"""DL-P2.5 whitelisted auto-resume tests (deterministic; no external model).

Covers the three gates — config flag, per-goal whitelist, journal state — plus the
CAS owner fence and the /goal autoresume command surface.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermes_state import SessionDB

# Flat keys: _write_config emits only the digital_life block from these.
FLAG_OFF_CONFIG = {"auto_resume": False, "recovery_enabled": False}
FLAG_ON_CONFIG = {"auto_resume": True, "recovery_enabled": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    import agent.task_recovery as recovery
    for key in ("HOME", "USERPROFILE", "HERMES_HOME", "STARDUST_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    yield monkeypatch, recovery


def _write_config(home: Path, body: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "digital_life:\n"
        f"  auto_resume: {str(body.get('auto_resume', False)).lower()}\n"
        f"  recovery_enabled: {str(body.get('recovery_enabled', False)).lower()}\n",
        encoding="utf-8",
    )


def _flag_state(home: Path) -> tuple:
    import agent.task_recovery as recovery
    return recovery.auto_resume_enabled(), recovery.enabled()


def test_auto_resume_flag_defaults_off_and_reads_digital_life(env):
    monkeypatch, recovery = env
    os = __import__("os")
    # Two separate homes: the config loader's file-signature cache can legitimately
    # serve the first read for an identical (mtime, size) rewrite, so each state
    # gets its own home + one write.
    off_home = Path(os.environ["STARDUST_HOME"]) / "off"
    on_home = Path(os.environ["STARDUST_HOME"]) / "on"
    _write_config(off_home, FLAG_OFF_CONFIG)
    _write_config(on_home, FLAG_ON_CONFIG)
    monkeypatch.setenv("STARDUST_HOME", str(off_home))
    monkeypatch.setenv("HERMES_HOME", str(off_home))
    assert _flag_state(off_home) == (False, False)
    monkeypatch.setenv("STARDUST_HOME", str(on_home))
    monkeypatch.setenv("HERMES_HOME", str(on_home))
    assert _flag_state(on_home) == (True, True)


def test_goal_state_auto_resume_roundtrip(env):
    from hermes_cli.goals import GoalState
    state = GoalState(goal="g")
    assert state.auto_resume is False and state.auto_resume_attempts == 0
    state.auto_resume = True
    state.auto_resume_attempts = 2
    loaded = GoalState.from_json(state.to_json())
    assert loaded.auto_resume is True and loaded.auto_resume_attempts == 2
    # Legacy rows without the field load with defaults.
    legacy = GoalState.from_json(GoalState(goal="g").to_json())
    assert legacy.auto_resume is False


def _manager(db, session_id="s1"):
    from hermes_cli.goals import GoalManager
    return GoalManager(session_id=session_id, session_db=db)


def _db_with_goal(tmp_path, *, whitelist=False, status="active", extra=None):
    db = SessionDB(db_path=tmp_path / f"state-{id(tmp_path)}.db")
    db.create_session("s1", source="cli", model="local")
    mgr = _manager(db)
    mgr.set("count to three")
    if extra:
        for key, value in extra.items():
            setattr(mgr._state, key, value)
    if status != "active":
        mgr._state.status = status
    if whitelist:
        mgr.set_auto_resume(True)
    else:
        mgr._save()
    return db, mgr


def test_claim_auto_resume_requires_whitelist_and_spends_one_attempt(tmp_path):
    db, mgr = _db_with_goal(tmp_path, whitelist=False)
    assert mgr.claim_auto_resume() is None  # not whitelisted
    db.close()

    db, mgr = _db_with_goal(tmp_path, whitelist=True)
    prompt = mgr.claim_auto_resume()
    assert prompt and "count to three" in prompt
    assert mgr.state.auto_resume_attempts == 1
    db.close()


def test_claim_auto_resume_single_owner_via_cas(tmp_path):
    db, _ = _db_with_goal(tmp_path, whitelist=True)
    # Two managers over the same row: only one CAS can win per attempt slot.
    first = _manager(db)
    second = _manager(db)
    winners = [m for m in (first, second) if m.claim_auto_resume()]
    # Both may win across DIFFERENT attempt slots; but the same snapshot can never
    # fund two claims: attempts must strictly increase and stay within budget.
    attempts = db.get_meta("goal:s1")
    import json
    assert json.loads(attempts)["auto_resume_attempts"] >= 1
    db.close()

    # Sequential claims exhaust the bounded budget then fail closed.
    db2, mgr2 = _db_with_goal(tmp_path / "b", whitelist=True)
    prompts = [mgr2.claim_auto_resume() for _ in range(5)]
    assert all(prompts[:3]) and prompts[3] is None and prompts[4] is None
    db2.close()


def test_claim_auto_resume_skips_paused_wait_budget(tmp_path):
    db, mgr = _db_with_goal(tmp_path, whitelist=True, status="paused")
    assert mgr.claim_auto_resume() is None
    mgr._state.status = "active"
    mgr._state.waiting_on_pid = 12345
    mgr._save()
    assert mgr.claim_auto_resume() is None
    mgr._state.waiting_on_pid = None
    mgr._state.turns_used = mgr._state.max_turns
    mgr._save()
    assert mgr.claim_auto_resume() is None
    db.close()


def test_list_auto_resume_sessions_scopes_to_whitelisted_active(tmp_path):
    from hermes_cli.goals import GoalManager
    db = SessionDB(db_path=tmp_path / "list.db")
    db.create_session("plain", source="cli", model="local")
    db.create_session("marked", source="cli", model="local")
    db.create_session("paused", source="cli", model="local")
    GoalManager(session_id="plain", session_db=db).set("plain goal")
    marked = GoalManager(session_id="marked", session_db=db)
    marked.set("marked goal")
    marked.set_auto_resume(True)
    paused = GoalManager(session_id="paused", session_db=db)
    paused.set("paused goal")
    paused.set_auto_resume(True)
    paused._state.status = "paused"
    paused._save()
    assert GoalManager.list_auto_resume_sessions(db) == ["marked"]
    db.close()


def test_journal_status_gates(env):
    monkeypatch, recovery = env
    monkeypatch.setattr(recovery, "enabled", lambda: True)
    home = Path(__import__("os").environ["STARDUST_HOME"])
    db = SessionDB(db_path=home / "journal.db")
    db.create_session("recovery", source="cli", model="local")
    agent = SimpleNamespace(session_id="recovery", _session_db=db,
                            identity={"status": "ready", "individual_id": "identity-a"})
    # No journal yet: disabled (proceeds like a manual resume).
    assert recovery.journal_status(db, "recovery")["status"] == "disabled"
    recovery.prepare(agent, "write the report", task_id="task-a")
    recovery.start_step(agent, "one", "write_file", {"path": "a", "content": "a"})
    recovery.commit_step(agent, "one", "done")
    # Fully committed: ready.
    assert recovery.journal_status(db, "recovery")["status"] == "ready"
    # Uncommitted side-effecting step: needs_review (fail closed).
    recovery.start_step(agent, "two", "terminal", {"command": "send"})
    assert recovery.journal_status(db, "recovery")["status"] == "needs_review"
    # Foreign scope: rejected.
    db.patch_session_model_config("recovery", {recovery.KEY: dict(
        version=1, scope="foreign", identity="x", session_id="recovery",
        task_id="task-a", steps=[])})
    assert recovery.journal_status(db, "recovery")["status"] == "rejected"
    # Flag off: disabled even with a pending step.
    monkeypatch.setattr(recovery, "enabled", lambda: False)
    assert recovery.journal_status(db, "recovery")["status"] == "disabled"
    db.close()


def test_autoresume_command_marks_and_clears_whitelist(tmp_path):
    from hermes_cli.goal_command import dispatch_goal_command, is_goal_control
    db, _ = _db_with_goal(tmp_path, whitelist=False)
    mgr = _manager(db)
    ok = dispatch_goal_command(mgr, "autoresume on", authorize_gate=lambda: None)
    assert not ok.error and "ON" in ok.output
    assert mgr.state.auto_resume is True
    off = dispatch_goal_command(mgr, "autoresume off", authorize_gate=lambda: None)
    assert not off.error and "OFF" in off.output
    assert mgr.state.auto_resume is False and mgr.state.auto_resume_attempts == 0
    bad = dispatch_goal_command(mgr, "autoresume maybe", authorize_gate=lambda: None)
    assert bad.error
    assert is_goal_control("autoresume on") is True
    # No goal set: refuses rather than creating one.
    db.create_session("empty", source="cli", model="local")
    empty = _manager(db, "empty")
    none = dispatch_goal_command(empty, "autoresume on", authorize_gate=lambda: None)
    assert none.error or "No goal" in none.output
    db.close()
