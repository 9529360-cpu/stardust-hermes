"""Best-effort, profile-local approval ledger (one bounded rotated generation)."""
import json
import logging
import threading
import queue
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
MAX_AUDIT_BYTES = 5 * 1024 * 1024
_lock = threading.Lock()


def _path():
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "audit" / "approvals.jsonl"


def write_approval_audit(*, session_key, kind, tool_name, description, pattern_key,
                         outcome, mode, command_preview, _audit_path=None):
    """Append a redacted decision; filesystem/redaction failures never affect approval."""
    try:
        from agent.redact import redact_sensitive_text

        def scrub(value):
            return redact_sensitive_text(str(value or ""), force=True, redact_url_credentials=True)

        entry = {"ts": datetime.now(timezone.utc).isoformat(),
                 "session_key": scrub(session_key), "kind": kind, "tool_name": scrub(tool_name),
                 "description": scrub(description)[:1000], "pattern_key": scrub(pattern_key)[:300],
                 "outcome": outcome, "mode": mode, "command_preview": scrub(command_preview)[:300]}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _lock:
            path = _audit_path if _audit_path is not None else _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > MAX_AUDIT_BYTES:
                path.replace(path.with_name("approvals.jsonl.1"))
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except Exception:
        # Do not include exception text: filesystem paths may themselves contain secrets.
        logger.warning("Unable to write approval audit entry")


# A slow disk/redactor must never hold approval/interrupt notification delivery.
_pending = queue.Queue(maxsize=256)


def _writer():
    while True:
        item = _pending.get()
        try:
            if isinstance(item, threading.Event):
                item.set()
            else:
                write_approval_audit(**item)
        finally:
            _pending.task_done()


threading.Thread(target=_writer, name="approval-audit", daemon=True).start()


def enqueue_approval_audit(**entry):
    """Nonblocking best-effort enqueue, pinned to the caller's profile path."""
    entry["_audit_path"] = _path()
    try:
        _pending.put_nowait(entry)
    except queue.Full:
        # Drop telemetry rather than delaying security decisions.
        return False
    return True


def flush_approval_audit(timeout=2):
    """Bounded barrier for readers/tests, never called by an approval gate."""
    done = threading.Event()
    try:
        _pending.put_nowait(done)
    except queue.Full:
        return False
    return done.wait(timeout)


def read_approval_audit(limit=100, session_key=None):
    """Return newest-first entries, including the rotated generation; skip corrupt lines."""
    limit = max(0, min(int(limit), 10000))
    if not limit:
        return []
    entries = deque(maxlen=limit)
    with _lock:
        path = _path()
        for source in (path.with_name("approvals.jsonl.1"), path):
            try:
                with source.open("rb") as stream:
                    for line in stream:
                        try:
                            entry = json.loads(line.decode("utf-8", errors="strict"))
                            if isinstance(entry, dict) and (session_key is None or entry.get("session_key") == session_key):
                                entries.append(entry)
                        except (ValueError, TypeError):
                            continue
            except OSError:
                continue
    return list(reversed(entries))
