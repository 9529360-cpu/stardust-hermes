"""Opt-in, local-shell missing-command facts. Never retain argv or output.

Only simple allowlisted executable invocations are eligible. Recovery attribution
is deliberately narrower: rg -> grep with identical literal pattern/file operands.
Remote backends are skipped until they expose a reliable environment identity.
"""
from dataclasses import dataclass
import hashlib
import json
import logging
import re
import shlex
import sqlite3
import time
import threading
import platform
from contextlib import closing

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)
TTL_SECONDS = 24 * 60 * 60
_COMMANDS = frozenset({"rg", "grep", "fd", "python3", "python", "node", "gh"})
_LOCK = threading.RLock()
# Volatile association only; session, environment, and argument digest never go
# into durable recovery data. Cap this map along with the durable fact store.
_PENDING = {}


@dataclass
class Observation:
    hint: str = ""
    scope: str = ""
    owner: str = ""
    command: str = ""
    session: str = ""
    operands: tuple = ()
    missing: bool = False


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _invocation(command):
    # Skip shell syntax, assignments, executable paths, wrappers and substitutions.
    if not isinstance(command, str) or len(command) > 4096 or re.search(r"[\n\r;&|<>`$\\]", command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or words[0] not in _COMMANDS:
        return None
    return words


def _literal_search(operands):
    return (len(operands) == 2
            and re.fullmatch(r"[A-Za-z0-9_ ]{1,128}", operands[0]) is not None
            and bool(operands[1]) and not operands[1].startswith("-"))


def _enabled():
    from hermes_cli.config_effective import load_user_config_effective
    return load_user_config_effective().get("terminal", {}).get("environment_failure_recall") is True


def _connect():
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "environment_failures.db", timeout=0.1)
    db.execute("CREATE TABLE IF NOT EXISTS facts (scope TEXT, command TEXT, observed REAL, "
               "owner TEXT, origin TEXT, alternative TEXT, hinted INTEGER DEFAULT 0, PRIMARY KEY(scope, command))")
    db.execute("DELETE FROM facts WHERE observed < ? OR observed > ?", (time.time() - TTL_SECONDS, time.time()))
    db.execute("DELETE FROM facts WHERE rowid NOT IN (SELECT rowid FROM facts ORDER BY observed DESC LIMIT 64)")
    return db


def _probe(env, command, cwd):
    # Execute through the exact backend snapshot, not controller os.environ.
    # Read only: the internal execute path does not enter terminal dispatch again.
    script = ('printf "W24_ENV\\n%s\\n%s\\n" "$PATH" "${PATHEXT-}"; '
              f'if command -v {command} >/dev/null 2>&1; then printf "available\\n"; '
              'else printf "missing\\n"; fi')
    result = env.execute(script, timeout=2, cwd=cwd, bounded_capture=True)
    lines = result.get("output", "").strip().splitlines()
    if result.get("returncode") != 0 or len(lines) != 4 or lines[0] != "W24_ENV" or lines[3] not in {"available", "missing"}:
        return None
    return _digest("\n".join(lines[1:3])), lines[3] == "missing"


def before(command, env, backend, session, cwd):
    """Re-probe before eligible dispatch; claim at most one persistent hint."""
    empty = Observation()
    try:
        words = _invocation(command)
        if backend != "local" or not session or not words or not _enabled():
            return empty
        probe = _probe(env, words[0], cwd)
        if probe is None:
            return empty
        from gateway.session_context import get_session_env
        # Home identity prevents cross-profile reuse even if labels are absent.
        identity = "\0".join((str(get_hermes_home().resolve()),
                                get_session_env("STARDUST_SESSION_PROFILE", ""),
                                platform.system(), platform.machine(), platform.node(), backend,
                                str(getattr(env, "env", {}).get("SHELL", ""))))
        from tools.environments.local import _find_bash
        owner = _digest(identity)
        scope = _digest(identity + probe[0] + str(cwd) + _find_bash())
        obs = Observation(scope=scope, owner=owner, command=words[0], session=_digest(session),
                          operands=tuple(words[1:]), missing=probe[1])
        with _LOCK, closing(_connect()) as db, db:
            # PATH changes invalidate previous facts for this profile/host/backend.
            # An installed executable is re-probed even with unchanged PATH.
            db.execute("DELETE FROM facts WHERE owner = ? AND scope != ?", (owner, scope))
            if not obs.missing:
                db.execute("DELETE FROM facts WHERE scope = ? AND command = ?", (scope, obs.command))
                return obs
            row = db.execute("SELECT origin, alternative, hinted FROM facts WHERE scope = ? AND command = ?",
                             (scope, obs.command)).fetchone()
            if row and row[0] != obs.session and not row[2]:
                changed = db.execute("UPDATE facts SET hinted = 1 WHERE scope = ? AND command = ? AND hinted = 0",
                                     (scope, obs.command)).rowcount
                if changed:
                    alternative = row[1] if row[1] == "grep" else None
                    if alternative:
                        alternative_probe = _probe(env, alternative, cwd)
                        if alternative_probe != (probe[0], False):
                            alternative = None
                    obs.hint = (f"[Environment recall] {obs.command} was verified unavailable in this matching "
                                "environment (24h TTL); re-probe still finds it missing. "
                                + (f"Prior verified alternative: {alternative} (simple literal file search only). " if alternative else "")
                                + "Advisory only; command runs unchanged.")[:600]
        return obs
    except Exception:  # Advisory failure must never prevent the requested command.
        logger.debug("Environment recall unavailable", exc_info=True)
        return empty


def after(obs, result):
    """Accept only actual missing exit/status plus an anchored shell diagnostic."""
    if not obs.scope:
        return
    try:
        code = result.get("returncode")
        output = result.get("output", "")
        missing = (obs.missing and code == 127 and isinstance(output, str)
                   and re.fullmatch(r"(?:[^\r\n]*: )?" + re.escape(obs.command)
                                    + r": command not found\s*", output.strip()) is not None)
        with _LOCK, closing(_connect()) as db, db:
            if missing:
                db.execute("INSERT OR IGNORE INTO facts(scope,command,observed,owner,origin) VALUES(?,?,?,?,?)",
                           (obs.scope, obs.command, time.time(), obs.owner, obs.session))
                db.execute("DELETE FROM facts WHERE rowid NOT IN "
                           "(SELECT rowid FROM facts ORDER BY observed DESC LIMIT 64)")
                # Do not persist even a digest of private operands.
                if obs.command == "rg" and _literal_search(obs.operands):
                    if len(_PENDING) >= 64:
                        _PENDING.clear()
                    _PENDING[(obs.scope, obs.session)] = (_digest(json.dumps(obs.operands)), time.time())
            elif code == 0 and not obs.missing and obs.command == "grep":
                pending = _PENDING.pop((obs.scope, obs.session), None)
                if pending and time.time() - pending[1] < 300 and pending[0] == _digest(json.dumps(obs.operands)):
                    db.execute("UPDATE facts SET alternative = 'grep' WHERE scope = ? AND command = 'rg' AND origin = ?",
                               (obs.scope, obs.session))
    except Exception:  # A failed metadata write must not replace the real result.
        logger.debug("Environment recall observation unavailable", exc_info=True)
