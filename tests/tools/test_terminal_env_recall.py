"""Real terminal-boundary proof; no model/provider calls or personal state."""
import sqlite3
import os
import shlex
import shutil


def _restricted_path(tmp_path):
    if os.name == "nt":
        return "/usr/bin:/bin"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("grep", "mv", "rm"):
        (bin_dir / name).symlink_to(shutil.which(name))
    return str(bin_dir)


def test_real_missing_recovery_and_once_across_sessions(tmp_path, monkeypatch):
    from tools import terminal_env_recall as recall
    from tools.environments.local import LocalEnvironment

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("terminal:\n  environment_failure_recall: true\n")
    (tmp_path / "sample.txt").write_text("needle\n")
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        # The same actual environment is used by probes and dispatched commands.
        restricted = _restricted_path(tmp_path)
        env.execute("export PATH=" + shlex.quote(restricted))
        before = recall.before("rg needle sample.txt", env, "local", "session-a", str(tmp_path))
        result = env.execute("rg needle sample.txt")
        assert result["returncode"] == 127
        recall.after(before, result)
        alt = recall.before("grep needle sample.txt", env, "local", "session-a", str(tmp_path))
        result = env.execute("grep needle sample.txt")
        assert result["returncode"] == 0
        recall.after(alt, result)
        assert not recall.before("rg needle sample.txt", env, "local", "session-a", str(tmp_path)).hint
        first = recall.before("rg needle sample.txt", env, "local", "session-b", str(tmp_path))
        assert "grep" in first.hint and len(first.hint) <= 600
        assert not recall.before("rg needle sample.txt", env, "local", "session-c", str(tmp_path)).hint
        assert not recall.before("printf unrelated", env, "local", "session-c", str(tmp_path)).hint
        assert not recall.before("rg needle; echo private", env, "local", "session-c", str(tmp_path)).hint
    finally:
        env.cleanup()


def test_reprobe_expiry_privacy_and_profile_isolation(tmp_path, monkeypatch):
    from tools import terminal_env_recall as recall
    from tools.environments.local import LocalEnvironment
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("terminal:\n  environment_failure_recall: true\n")
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        restricted = _restricted_path(tmp_path)
        env.execute("export PATH=" + shlex.quote(restricted))
        obs = recall.before("rg private file", env, "local", "a", str(tmp_path))
        # Quoted diagnostics, other exit statuses, and successful commands are not facts.
        for result in ({"returncode": 0, "output": "rg: command not found"},
                       {"returncode": 127, "output": "quoted rg: command not found\nother"}):
            recall.after(obs, result)
        db_path = tmp_path / "environment_failures.db"
        with sqlite3.connect(db_path) as db:
            assert db.execute("select count(*) from facts").fetchone()[0] == 0
        recall.after(obs, env.execute("rg private file"))
        with sqlite3.connect(db_path) as db:
            assert [x[1] for x in db.execute("pragma table_info(facts)")] == [
                "scope", "command", "observed", "owner", "origin", "alternative", "hinted"]
            assert "private" not in repr(db.execute("select * from facts").fetchall())
        monkeypatch.setenv("STARDUST_SESSION_PROFILE", "other")
        assert not recall.before("rg private file", env, "local", "b", str(tmp_path)).hint
        monkeypatch.delenv("STARDUST_SESSION_PROFILE")
        env.execute("export PATH=" + shlex.quote(restricted + ":/changed"))
        assert not recall.before("rg private file", env, "local", "b", str(tmp_path)).hint
        with sqlite3.connect(db_path) as db:
            assert db.execute("select count(*) from facts").fetchone()[0] == 0
        fresh = recall.before("rg private file", env, "local", "a", str(tmp_path))
        recall.after(fresh, env.execute("rg private file"))
        with sqlite3.connect(db_path) as db:
            db.execute("update facts set observed = observed - 90000")
        assert not recall.before("rg private file", env, "local", "b", str(tmp_path)).hint
        assert not recall.before("rg private file", env, "ssh", "b", str(tmp_path)).scope
    finally:
        env.cleanup()


def test_registry_fresh_process_recall_and_install(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    (tmp_path / "config.yaml").write_text("terminal:\n  environment_failure_recall: true\n")
    (tmp_path / "sample.txt").write_text("needle\n")
    restricted = _restricted_path(tmp_path)
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    from tools.environments.local import _windows_to_msys_path
    restricted += ":" + _windows_to_msys_path(str(install_dir))
    script = r'''
import json,sys,shlex
from tools import terminal_tool
from tools.registry import registry
mode, path = sys.argv[1:]
def run(command):
    return json.loads(registry.dispatch('terminal', {'command':command, 'timeout':10}, task_id=mode))
try:
    run('export PATH=' + shlex.quote(path))
    results = [run(cmd) for cmd in (
        ['rg needle sample.txt', 'grep needle sample.txt'] if mode == 'seed' else
        ['rg needle sample.txt', 'rg needle sample.txt', 'printf unrelated'])]
    print(json.dumps(results))
finally:
    terminal_tool._atexit_cleanup()
'''
    def fresh(mode):
        result = subprocess.run([sys.executable, "-c", script, mode, restricted],
                                capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    seed = fresh("seed")
    assert [r["exit_code"] for r in seed] == [127, 0]
    recalled = fresh("later")
    assert "grep" in recalled[0]["environment_hint"]
    assert len(recalled[0]["environment_hint"]) <= 600
    assert all("environment_hint" not in r for r in recalled[1:])
    executable = install_dir / "rg"
    executable.write_text('#!/bin/bash\nexec grep "$@"\n')
    executable.chmod(0o755)
    available = fresh("installed")
    assert available[0]["exit_code"] == 0
    assert all("environment_hint" not in r for r in available)
    with sqlite3.connect(tmp_path / "environment_failures.db") as db:
        assert db.execute("select count(*) from facts").fetchone()[0] == 0
    # A readonly/corrupt fact store must fail open, preserving dispatch.
    (tmp_path / "environment_failures.db").write_bytes(b"invalid sqlite")
    assert fresh("corrupt")[0]["exit_code"] == 0


def test_default_off_does_not_probe_or_store(tmp_path, monkeypatch):
    from tools import terminal_env_recall as recall
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    class NeverExecute:
        def execute(self, *args, **kwargs):
            raise AssertionError("feature off must not probe")
    assert not recall.before("rg private", NeverExecute(), "local", "s", str(tmp_path)).hint
    assert not list(tmp_path.glob("*.db"))
