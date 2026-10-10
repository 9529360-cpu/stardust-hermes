from tools.browser_tool_manifest import build_manifest


def test_manifest_exposes_profile_identity_actions_and_fallback():
    result = build_manifest(
        task_id="task-1",
        configured_backend="cloud",
        profile_key="profile-a",
        actions=["navigate", "snapshot"],
        session={
            "session_name": "rp_abc",
            "current_url": "https://example.test/path",
            "features": {"local": True, "real_profile": True},
            "fallback_from_cloud": True,
            "fallback_provider": "CloudProvider",
            "fallback_reason": "provider unavailable",
        },
    )
    assert result["contract"] == "stardust.browser-session.v1"
    assert result["backend"] == "local"
    assert result["profile"] == {"key": "profile-a", "real_profile": True, "mode": "real-profile"}
    assert result["session"]["origin"] == "https://example.test"
    assert result["session"]["id"] == "rp_abc"
    assert result["state"]["degraded"] is True
    assert result["state"]["fallback"]["from"] == "CloudProvider"


def test_manifest_is_explicit_when_no_session_exists():
    result = build_manifest(task_id="task-2", session=None, configured_backend="local",
                            profile_key="profile-b", actions=["navigate"])
    assert result["state"] == {"active": False, "degraded": False, "fallback": None}
    assert result["session"] == {"id": None, "task_id": "task-2", "tab_id": None, "origin": None}
