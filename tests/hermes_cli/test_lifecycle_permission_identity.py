from hermes_cli import assistant_permissions
from hermes_cli import lifecycle


def test_pre_tool_policy_receives_effective_args_and_tool_call_identity(monkeypatch):
    seen = {}
    monkeypatch.setattr(lifecycle, "_observe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "_plugin_hooks",
        lambda *_args, **_kwargs: [
            {"action": "modify", "args": {"command": "effective-command"}},
        ],
    )

    def policy(tool_name, args, *, tool_call_id=""):
        seen.update(tool_name=tool_name, args=dict(args), tool_call_id=tool_call_id)
        return None

    monkeypatch.setattr(assistant_permissions, "pre_tool_call_directive", policy)
    results = lifecycle.invoke_hook(
        "pre_tool_call",
        tool_name="terminal",
        args={"command": "original-command"},
        tool_call_id="call-identity-123",
    )

    assert results == [{"action": "modify", "args": {"command": "effective-command"}}]
    assert seen == {
        "tool_name": "terminal",
        "args": {"command": "effective-command"},
        "tool_call_id": "call-identity-123",
    }
