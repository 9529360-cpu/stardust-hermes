"""Explicit profile-local standing authorizations, never inferred from conversation text.

Management records only: these records are NOT used for automatic approval.
Purchase/payment grants are unsupported (no currency-bound payment integration).
Targets are exact strings, except command_pattern whole-command shell globs.
"""
import fnmatch
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

_lock = threading.RLock()


def _path():
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "approval_grants.json"


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return parsed


def list_grants():
    """Read afresh so revocation and profile switches cannot leave stale authority."""
    with _lock:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            return [g for g in data if isinstance(g, dict)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []


def _save(grants):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".approval-grants-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(grants, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def add_grant(action_kind, target, max_amount=None, expires_at=None):
    if not isinstance(action_kind, str) or not action_kind.strip():
        raise ValueError("action_kind is required")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target is required")
    if action_kind not in {"command_pattern", "send_message"} or max_amount is not None:
        raise ValueError("Only non-monetary command_pattern/send_message records are supported; automatic approval is disabled")
    if expires_at is not None:
        _timestamp(expires_at)
    grant = {"id": uuid.uuid4().hex, "action_kind": action_kind, "target": target,
             "max_amount": None,
             "expires_at": expires_at, "created_at": datetime.now(timezone.utc).isoformat()}
    with _lock:
        grants = list_grants()
        grants.append(grant)
        _save(grants)
    return grant


def revoke_grant(grant_id):
    with _lock:
        grants = list_grants()
        remaining = [g for g in grants if g.get("id") != grant_id]
        if len(remaining) == len(grants):
            return False
        _save(remaining)
        return True


def matching_grant(action_kind, target, amount=None):
    if action_kind not in {"command_pattern", "send_message"} or amount is not None:
        return None
    if not isinstance(target, str) or not target:
        return None
    for grant in list_grants():
        try:
            if grant["action_kind"] != action_kind:
                continue
            if action_kind == "command_pattern":
                matches = fnmatch.fnmatchcase(target, grant["target"])
            else:
                matches = target == grant["target"]
            if not matches:
                continue
            if grant.get("expires_at") and _timestamp(grant["expires_at"]) <= datetime.now(timezone.utc):
                continue
            maximum = grant.get("max_amount")
            if maximum is not None:
                continue
            return grant
        except (KeyError, TypeError, ValueError, AttributeError):
            # Malformed persisted records never confer authority.
            continue
    return None
