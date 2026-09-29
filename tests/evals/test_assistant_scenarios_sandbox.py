"""The exam's sandbox: an isolated HERMES_HOME that routes the agent to the chosen endpoint, a
hermetic child environment (no host secrets, no approval bypass), and readers for the state the
graders need. Readers are exercised against files written by the real cron store and the real
state.db schema, so a storage-format change breaks these tests instead of silently blinding the exam.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from evals.assistant_scenarios import fixtures, sandbox

REPO = Path(__file__).resolve().parents[2]


def in_home(home, code, **env):
    """Run ``code`` in a fresh interpreter whose HERMES_HOME is ``home`` (import-time paths included)."""
    full_env = {**os.environ, "HERMES_HOME": str(home), "PYTHONPATH": str(REPO), **env}
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=full_env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_snapshot_ignores_caches_and_git_but_sees_real_edits(tmp_path):
    fixtures.calc_project(tmp_path)
    before = sandbox.snapshot(tmp_path)
    (tmp_path / "calc" / "__pycache__").mkdir()
    (tmp_path / "calc" / "__pycache__" / "stats.cpython-311.pyc").write_bytes(b"x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "index").write_bytes(b"x")
    assert sandbox.snapshot(tmp_path) == before

    (tmp_path / "calc" / "stats.py").write_text("changed", encoding="utf-8")
    after = sandbox.snapshot(tmp_path)
    assert sorted(p for p in before if before[p] != after.get(p)) == ["calc/stats.py"]


def test_home_config_routes_the_agent_to_the_exam_endpoint(tmp_path):
    endpoint = sandbox.ModelEndpoint(base_url="http://127.0.0.1:9/v1", model="m1", api_key="sk-exam")
    sandbox.write_home_config(tmp_path, endpoint)
    out = in_home(tmp_path, (
        "import json\n"
        "from hermes_cli.config import load_config\n"
        "from hermes_cli.runtime_provider import resolve_runtime_provider\n"
        "rt = resolve_runtime_provider()\n"
        "print(json.dumps([load_config()['model']['default'], rt['base_url'], rt['api_mode'], rt['api_key']]))"),
        **{sandbox.EXAM_KEY_ENV: "sk-exam"})
    assert json.loads(out.strip().splitlines()[-1]) == ["m1", "http://127.0.0.1:9/v1", "chat_completions", "sk-exam"]
    assert "sk-exam" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_child_env_carries_no_host_secrets_or_approval_bypass(tmp_path):
    base = {"PATH": "/usr/bin", "OPENAI_API_KEY": "host-key", "TELEGRAM_BOT_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s",
            "HERMES_PROFILE": "work", "HERMES_YOLO_MODE": "1", "HERMES_HOME": "/real/home",
            "HERMES_GIT_BASH_PATH": "C:/Git/bin/bash.exe", "PYTHONPATH": "/elsewhere"}
    env = sandbox.child_env(base, home=tmp_path, repo_root=REPO, api_key="sk-exam", tool_bin=tmp_path / "bin",
                            profile=tmp_path / "profile")

    assert {k for k in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "AWS_SECRET_ACCESS_KEY",
                        "HERMES_PROFILE", "HERMES_YOLO_MODE") if k in env} == set()
    assert (env["HERMES_HOME"], env["PYTHONPATH"], env[sandbox.EXAM_KEY_ENV]) == (str(tmp_path), str(REPO), "sk-exam")
    assert env["HERMES_GIT_BASH_PATH"] == "C:/Git/bin/bash.exe"
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")


def test_child_env_hides_logins_other_tools_keep_in_the_user_profile(tmp_path):
    """Stardust's credential pool also discovers logins outside env vars (``gh auth token``, ~/.claude,
    ~/.codex, ~/.qwen). The exam's backend gets an empty profile so it neither uses nor refreshes them."""
    base = {"PATH": "/usr/bin", "HOME": "/home/me", "USERPROFILE": "C:/Users/me", "CODEX_HOME": "/home/me/.codex",
            "CLAUDE_CONFIG_DIR": "/home/me/.claude", "GH_CONFIG_DIR": "/home/me/.config/gh",
            "LOCALAPPDATA": "C:/Users/me/AppData/Local"}
    profile = tmp_path / "profile"
    env = sandbox.child_env(base, home=tmp_path / "home", repo_root=REPO, api_key="", tool_bin=None, profile=profile)

    assert (env["HOME"], env["USERPROFILE"], env["GH_CONFIG_DIR"]) == (str(profile), str(profile), str(profile / "gh"))
    assert {"CODEX_HOME", "CLAUDE_CONFIG_DIR"} & set(env) == set()
    assert env["LOCALAPPDATA"] == "C:/Users/me/AppData/Local"  # Git Bash and pip still resolve through it
    assert profile.is_dir()


def test_backend_env_cannot_discover_a_copilot_login(tmp_path):
    """On a machine logged in to the GitHub CLI (Windows keeps that token in the credential manager, so a
    fresh GH_CONFIG_DIR alone does not hide it) the backend must still find no Copilot token. Only a
    boolean crosses the process boundary, so a failure never prints the token."""
    env = sandbox.child_env(dict(os.environ), home=tmp_path / "home", repo_root=REPO, api_key="", tool_bin=None,
                            profile=tmp_path / "profile")
    code = ("import json\nfrom hermes_cli.copilot_auth import resolve_copilot_token\n"
            "try:\n    token, _ = resolve_copilot_token()\nexcept ValueError:\n    token = 'unusable'\n"
            "print(json.dumps(bool(token)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, check=True)
    found = json.loads(out.stdout.strip().splitlines()[-1])
    assert found is False


def test_cron_reader_sees_jobs_created_by_the_real_cron_store(tmp_path):
    in_home(tmp_path, "from cron.jobs import create_job\ncreate_job('检查邮箱，有重要变化告诉我', '0 8 * * *', name='mail')")
    [job] = sandbox.read_cron_jobs(tmp_path)
    assert (job["schedule"]["kind"], job["schedule"]["expr"], job.get("enabled", True)) == ("cron", "0 8 * * *", True)
    assert "检查邮箱" in job["prompt"]


def test_cron_reader_is_empty_without_a_store(tmp_path):
    assert sandbox.read_cron_jobs(tmp_path) == []


def test_delegation_reader_reads_the_real_ledger_schema(tmp_path):
    in_home(tmp_path, "from hermes_state import SessionDB\nSessionDB().close()")
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("INSERT INTO async_delegations (delegation_id, origin_session, state, dispatched_at, updated_at, "
                     "task_json, delivery_state) VALUES ('d1', 'k', 'running', 1.0, 1.0, ?, 'pending')",
                     (json.dumps({"goal": "research tinylib"}),))
    assert sandbox.read_delegations(tmp_path) == [
        {"delegation_id": "d1", "state": "running", "delivery_state": "pending", "goal": "research tinylib"}]


def test_delegation_reader_is_empty_without_a_database(tmp_path):
    assert sandbox.read_delegations(tmp_path) == []


def test_delegation_reader_fails_loudly_when_the_ledger_schema_drifts(tmp_path):
    """An empty ledger would pass "no process-local work" by default, so a ledger the reader cannot
    understand must be an error, never an empty list."""
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("CREATE TABLE async_delegations (delegation_id TEXT, status TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        sandbox.read_delegations(tmp_path)


def test_unit_test_run_reports_the_workspace_verdict(tmp_path):
    fixtures.calc_project(tmp_path)
    assert sandbox.run_unit_tests(tmp_path, sys.executable).passed is False
    stats = tmp_path / "calc" / "stats.py"
    stats.write_text(stats.read_text(encoding="utf-8").replace(
        "    return ordered[mid]\n",
        "    if len(ordered) % 2 == 0:\n        return (ordered[mid - 1] + ordered[mid]) / 2\n    return ordered[mid]\n"),
        encoding="utf-8")
    assert sandbox.run_unit_tests(tmp_path, sys.executable).passed is True


def test_disposable_python_is_what_shell_commands_run(tmp_path):
    tool_bin = sandbox.make_tool_python(tmp_path / "toolpy", sys.executable)
    env = sandbox.child_env(dict(os.environ), home=tmp_path, repo_root=REPO, api_key="", tool_bin=tool_bin,
                            profile=tmp_path / "profile")
    out = subprocess.run("python -c \"import sys; print(sys.prefix)\"", shell=True, env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert Path(out).resolve() == (tmp_path / "toolpy").resolve()
