"""Isolated homes, hermetic child environments, and read-only views of the state graders need.

Every scenario gets its own HERMES_HOME (sessions, memory, cron store, delegation ledger all live
there) and its own workspace. The model's key reaches the backend only through ``EXAM_KEY_ENV`` in
the child environment — never written to disk — and host secrets, profile selection, and approval
bypasses (``HERMES_YOLO_MODE`` …) are stripped so the exam measures shipped defaults.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from evals.assistant_scenarios.grading import TestRun

EXAM_KEY_ENV = "STARDUST_EXAM_API_KEY"
PROVIDER_NAME = "exam"

_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"})
_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
_KEEP_HERMES_VARS = frozenset({"HERMES_GIT_BASH_PATH"})
# Pointers to other tools' login stores; the profile redirect covers their default locations.
_CREDENTIAL_DIR_VARS = frozenset({"PYTHONPATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "GH_CONFIG_DIR"})


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    model: str
    api_mode: str = "chat_completions"
    api_key: str = ""
    context_length: Optional[int] = None
    label: str = ""


def write_home_config(home: Path, endpoint: ModelEndpoint, extra: Optional[Dict[str, Any]] = None) -> None:
    """Route the backend's main model to ``endpoint``; everything else stays at shipped defaults
    unless ``extra`` (top-level sections) overrides it."""
    provider: Dict[str, Any] = {"api": endpoint.base_url, "name": "Stardust exam model", "api_mode": endpoint.api_mode,
                                "key_env": EXAM_KEY_ENV, "models": {endpoint.model: {}}}
    if endpoint.context_length:
        provider["context_length"] = endpoint.context_length
    config: Dict[str, Any] = {"model": {"default": endpoint.model, "provider": PROVIDER_NAME},
                              "providers": {PROVIDER_NAME: provider}}
    config.update(extra or {})
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def child_env(base: Mapping[str, str], *, home: Path, repo_root: Path, api_key: str,
              tool_bin: Optional[Path], profile: Path) -> Dict[str, str]:
    """The backend's environment. ``profile`` becomes an empty user profile: Stardust's credential pool
    also discovers logins outside env vars (``gh auth token``, ~/.claude, ~/.codex, ~/.qwen), and the
    exam must neither use nor refresh them."""
    env = {k: v for k, v in base.items() if not _stripped(k)}
    profile.mkdir(parents=True, exist_ok=True)
    env.update({"HERMES_HOME": str(home), "PYTHONPATH": str(repo_root), "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8", EXAM_KEY_ENV: api_key,
                "HOME": str(profile), "USERPROFILE": str(profile), "GH_CONFIG_DIR": str(profile / "gh"),
                # An explicitly set (here: unusable) Copilot token makes copilot_auth skip its `gh auth token`
                # fallback, which on Windows reads the credential manager regardless of GH_CONFIG_DIR.
                "COPILOT_GITHUB_TOKEN": "stardust-exam-no-copilot"})
    if tool_bin is not None:
        env["PATH"] = os.pathsep.join(p for p in (str(tool_bin), env.get("PATH", "")) if p)
    return env


def _stripped(name: str) -> bool:
    upper = name.upper()
    if upper.startswith("HERMES_"):
        return upper not in _KEEP_HERMES_VARS
    return upper in _CREDENTIAL_DIR_VARS or upper.endswith(_SECRET_SUFFIXES)


def snapshot(root: Path) -> Dict[str, str]:
    """``relative/posix/path -> sha256`` for every workspace file, skipping caches and VCS internals."""
    out: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            path = Path(dirpath) / name
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def read_cron_jobs(home: Path) -> List[Dict[str, Any]]:
    path = home / "cron" / "jobs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    return [j for j in jobs if isinstance(j, dict)]


def read_delegations(home: Path) -> List[Dict[str, Any]]:
    """Rows of the async-delegation ledger (``state.db``), read-only so a live backend is never blocked."""
    path = home / "state.db"
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute("SELECT delegation_id, state, delivery_state, task_json FROM async_delegations "
                            "ORDER BY dispatched_at, delegation_id").fetchall()
    except sqlite3.OperationalError as exc:
        # Only a ledger that was never created means "no delegations"; anything else (schema drift, a lock
        # that outlived the timeout) must surface, because an empty ledger passes "no process-local work".
        if "no such table" in str(exc):
            return []
        raise
    finally:
        conn.close()
    return [{"delegation_id": d, "state": s, "delivery_state": ds, "goal": _goal(task)} for d, s, ds, task in rows]


def _goal(task_json: Optional[str]) -> str:
    task = json.loads(task_json or "{}")
    return str(task.get("goal") or "; ".join(str(g) for g in task.get("goals") or []))


def run_unit_tests(workspace: Path, python: str) -> TestRun:
    """The exam's own verdict on the workspace's unit tests — never the agent's claim about them."""
    try:
        result = subprocess.run([python, "-m", "unittest", "discover", "-s", "tests"], cwd=workspace,
                                capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return TestRun(passed=False, output="unit tests timed out after 120s")
    return TestRun(passed=result.returncode == 0, output=(result.stderr or result.stdout)[-2000:])


def make_tool_python(dest: Path, base_python: str) -> Path:
    """A throwaway virtualenv whose ``python``/``pip`` the agent's shell finds first: anything the agent
    installs lands here and is deleted with the run, never in the host's interpreters."""
    subprocess.run([base_python, "-m", "venv", str(dest)], check=True, capture_output=True, timeout=300)
    return dest / ("Scripts" if os.name == "nt" else "bin")
