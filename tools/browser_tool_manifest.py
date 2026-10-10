"""Unified read-only browser capability/session manifest."""

import json
from urllib.parse import urlsplit


def build_manifest(*, task_id, session, configured_backend, profile_key, actions):
    """Build stable v1 manifest from authoritative browser session state."""
    session = dict(session) if session else None
    features = (session or {}).get("features") or {}
    backend = configured_backend
    if session:
        if features.get("lightpanda"):
            backend = "lightpanda"
        elif features.get("local"):
            backend = "local"
        elif features.get("cdp_override"):
            backend = "cdp"
        else:
            backend = (session.get("backend") or session.get("provider") or configured_backend)
    active_url = (session or {}).get("current_url")
    origin = None
    if active_url:
        parsed = urlsplit(active_url)
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
    degraded = bool((session or {}).get("fallback_from_cloud") or
                    (session or {}).get("lightpanda_fallback") or
                    (session or {}).get("fallback_reason"))
    return {
        "contract": "stardust.browser-session.v1",
        "backend": backend,
        "profile": {"key": profile_key, "real_profile": bool(features.get("real_profile")),
                    "mode": "real-profile" if features.get("real_profile") else "isolated"},
        "session": {"id": (session or {}).get("session_name"), "task_id": task_id,
                    "tab_id": (session or {}).get("tab_id"), "origin": origin},
        "actions": list(actions),
        "state": {"active": session is not None, "degraded": degraded,
                  "fallback": ({"from": (session or {}).get("fallback_provider"),
                                "reason": (session or {}).get("fallback_reason")} if degraded else None)},
    }


def serialize_manifest(**kwargs):
    return json.dumps(build_manifest(**kwargs))
