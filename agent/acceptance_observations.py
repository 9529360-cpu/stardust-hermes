"""Opt-in, advisory provenance for committed tool observations (no verdict changes)."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
_UI_ACTIONS = {"browser_click", "browser_type", "browser_press", "browser_fill_form",
               "browser_select_option", "browser_drag", "browser_press_key"}
_FILE_TOOLS = {"read_file", "write_file", "patch"}
_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def enabled():
    from hermes_cli.config import load_config_readonly
    try:
        config = load_config_readonly()
        return config.get("agent", {}).get("bind_acceptance_observations") is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS acceptance_observations (
        session_id TEXT NOT NULL, observation_id TEXT NOT NULL, contract_id TEXT NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY(session_id, observation_id))""")


def capture(name, args, result):
    """Hash only explicit file references, at execution time, before spilling/projection.

    A file merely existing at acceptance time is never evidence of an observation.
    Remote/inaccessible artifacts stay unverified rather than hashing a guessed path.
    """
    paths = []
    if name in _FILE_TOOLS and isinstance(args.get("path"), str):
        paths.append(args["path"])
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            result = {}
    def collect(value):
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in {"path", "screenshot_path", "image_path"} and isinstance(item, str):
                paths.append(item)
            elif key in {"data", "result"}:
                collect(item)
    if name.startswith("browser_"):
        collect(result)
    artifacts = {}
    for raw in paths[:16]:
        try:
            path = Path(raw).expanduser()
            if path.is_absolute() and path.is_file() and path.stat().st_size <= 64 * 1024 * 1024:
                artifacts[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    artifact_times = {}
    for path in artifacts:
        try:
            artifact_times[path] = Path(path).stat().st_mtime
        except OSError:
            continue
    ui_action = name in _UI_ACTIONS
    screenshot = "screenshot" in name or name == "browser_vision"
    return {"tier": "user-flow" if ui_action or screenshot else "runtime",
            "ui_action": ui_action, "screenshot": screenshot, "artifacts": artifacts,
            "artifact_times": artifact_times}


def record(agent, message, name, args, result, call_id, task_id, *, captured=None):
    """Only called after the canonical tool-result flush; missing rows are not invented."""
    if not enabled():
        return
    from agent.task_acceptance import load
    from agent.verification_evidence import _transaction
    row_id = message.get("_row_id")
    db = getattr(agent, "_session_db", None)
    if not isinstance(row_id, int) or db is None:
        return
    try:
        contract = load(agent.session_id)
        if not contract or not contract.get("active"):
            return
        with sqlite3.connect(f"{Path(db.db_path).as_uri()}?mode=ro", uri=True) as state:
            row = state.execute("SELECT timestamp FROM messages WHERE id=? AND session_id=? "
                                "AND role='tool' AND tool_call_id=? AND tool_name=?",
                                (row_id, agent.session_id, call_id, name)).fetchone()
        if row is None:
            return
        facts = captured if captured is not None else capture(name, args, result)
        observation_id = f"message:{row_id}"
        payload = dict(facts, observation_id=observation_id, timestamp=row[0],
                       action={"tool": name, "tool_call_id": call_id, "task_id": task_id})
        with _transaction() as conn:
            _schema(conn)
            conn.execute("INSERT OR IGNORE INTO acceptance_observations VALUES (?, ?, ?, ?)",
                         (agent.session_id, observation_id, contract["created_at"], json.dumps(payload)))
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        # Provenance is advisory: inability to record must not block tool completion.
        logger.warning("Acceptance observation unavailable: %s", type(exc).__name__)


def resolve(contract, source, artifact, tier):
    """Resolve a source exactly within the current session and explicit task contract."""
    from agent.verification_evidence import _transaction
    unknown = {"verification": "self-reported/unverified", "reason": "missing committed observation"}
    try:
        with _transaction() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='acceptance_observations'").fetchone():
                return unknown
            rows = conn.execute("SELECT payload FROM acceptance_observations WHERE session_id=? AND contract_id=?",
                                (contract["session_id"], contract["created_at"])).fetchall()
        records = [json.loads(row[0]) for row in rows]
        matches = [r for r in records if source in {r["observation_id"], r["action"]["tool_call_id"]}]
        if len(matches) != 1:
            return unknown
        observation = matches[0]
        path = Path(artifact).resolve()
        digest = observation["artifacts"].get(str(path))
        if not digest or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return dict(unknown, reason="artifact was not observed or its bytes changed")
        if observation["tier"] != tier:
            return dict(unknown, reason="recorded observation tier does not match claimed tier")
        if observation["screenshot"] or path.suffix.lower() in _IMAGES or tier == "user-flow":
            prior = [r for r in records if r["ui_action"]
                     and r["action"]["task_id"] == observation["action"]["task_id"]
                     and r["timestamp"] < observation["timestamp"]
                     and r["timestamp"] <= observation.get("artifact_times", {}).get(str(path), 0)]
            if not prior:
                return dict(unknown, reason="no preceding recorded UI action in the same task")
        return {"verification": "verified", "observation_id": observation["observation_id"],
                "timestamp": observation["timestamp"], "tier": observation["tier"],
                "artifact_sha256": digest, "action": observation["action"]}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return dict(unknown, reason="committed observation unavailable")
