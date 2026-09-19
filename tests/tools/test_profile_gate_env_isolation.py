"""Foreign-profile children must not inherit launcher authorization gates."""

import json
import os
import subprocess
import sys

import pytest

from tools.environments.local import served_profile_child_env

_GATES = {
    "DISCORD_ALLOWED_CHANNELS": "111",
    "DISCORD_ALLOWED_ROLES": "r1",
    "DISCORD_IGNORED_CHANNELS": "222",
    "DISCORD_ALLOW_BOTS": "all",
    "TELEGRAM_GROUP_ALLOWED_CHATS": "-100",
    "SLACK_ALLOWED_CHANNELS": "C1",
    "WHATSAPP_GROUP_ALLOW_FROM": "+1555",
    "GATEWAY_ALLOW_ALL_USERS": "true",
}
_PROBE = "import json,os;print(json.dumps({k:os.environ.get(k) for k in %r}))" % sorted(_GATES)


def _seen_by_child(env: dict) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return {key for key, value in json.loads(out.stdout.strip().splitlines()[-1]).items() if value is not None}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    launch = tmp_path / ".hermes"
    routed = launch / "profiles" / "b"
    routed.mkdir(parents=True)
    (launch / ".env").write_text("A_MARKER=a\n", encoding="utf-8")
    (routed / ".env").write_text("B_MARKER=b\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    for key, value in _GATES.items():
        monkeypatch.setenv(key, value)
    return launch, routed


def test_routed_child_drops_gates_without_multiplex_flag(homes):
    launch, routed = homes
    assert _seen_by_child(served_profile_child_env(base=os.environ, target_home=routed)) == set()
    assert _seen_by_child(served_profile_child_env(base=os.environ, target_home=launch)) == set(_GATES)


def test_update_recovery_drops_gates_only_for_foreign_profile(homes):
    from hermes_cli import update_restart_recovery as recovery

    routed = recovery._child_environment("b")
    same = recovery._child_environment("default")

    assert not any(key in routed for key in _GATES)
    assert all(same[key] == value for key, value in _GATES.items())
    for env in (routed, same):
        assert env[recovery._RECOVERY_ENV] == "1"
        assert not any(marker in env for marker in recovery._GATEWAY_MARKERS)
