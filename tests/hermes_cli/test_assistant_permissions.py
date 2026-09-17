from gateway.session_context import clear_session_vars, set_session_vars
from hermes_cli import assistant_permissions as permissions
from hermes_cli import lifecycle


def test_desktop_source_activates_permission_policy(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    tokens = set_session_vars(platform="", source="desktop")
    try:
        assert permissions.personal_assistant_permissions_active() is True
        directive = permissions.pre_tool_call_directive(
            "send_message", {"message": "hello"}
        )
        assert directive is not None
        assert directive["action"] == "approve"
        assert directive["rule_key"].startswith("stardust:external-write:send_message")
    finally:
        clear_session_vars(tokens)


def test_non_assistant_surface_keeps_legacy_behavior(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    tokens = set_session_vars(platform="", source="cli")
    try:
        assert permissions.personal_assistant_permissions_active() is False
        assert permissions.pre_tool_call_directive("send_message", {}) is None
    finally:
        clear_session_vars(tokens)


def test_dynamic_connector_read_and_write_classification():
    assert permissions.classify_tool_permission(
        "connectors__gmail__search_messages", {}
    ).level == permissions.ALLOW
    assert permissions.classify_tool_permission(
        "connectors__calendar__list_events", {}
    ).level == permissions.ALLOW

    send = permissions.classify_tool_permission(
        "connectors__slack__send_message", {}
    )
    assert send.level == permissions.CONFIRM
    assert send.rule_key.startswith("stardust:connector-write:")

    unknown_mutation = permissions.classify_tool_permission(
        "connectors__github__merge_pull_request", {}
    )
    assert unknown_mutation.level == permissions.CONFIRM

    destructive = permissions.classify_tool_permission(
        "connectors__drive__delete_file", {}
    )
    assert destructive.level == permissions.CONFIRM
    assert ":destructive:" in destructive.rule_key


def test_local_mutations_notify_without_interrupting():
    assert permissions.classify_tool_permission("read_file", {}).level == permissions.ALLOW
    assert permissions.classify_tool_permission("write_file", {"path": "x"}).level == permissions.NOTIFY


def test_unknown_extensible_write_actions_fail_toward_confirmation():
    for action in (
        "create_event", "update_record", "edit_item", "upload_file",
        "share_document", "invite_member", "react_message",
    ):
        decision = permissions.classify_tool_permission("calendar_plugin", {"action": action})
        assert decision.level == permissions.CONFIRM, action
        assert decision.rule_key.startswith("stardust:external-write:calendar_plugin"), action

    # Known Stardust/local surfaces retain execute-then-notify semantics even when they use
    # a verb that would be consequential on an unknown external tool.
    local = permissions.classify_tool_permission("desktop_project", {"action": "create"})
    assert local.level == permissions.NOTIFY

    # An unknown read-like operation stays non-interrupting.
    read = permissions.classify_tool_permission("calendar_plugin", {"action": "search_events"})
    assert read.level == permissions.ALLOW


def test_plugin_rewrite_is_classified_before_execution(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    monkeypatch.setattr(lifecycle, "_observe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "_plugin_hooks",
        lambda *_args, **_kwargs: [
            {"action": "modify", "args": {"action": "send_message"}}
        ],
    )

    results = lifecycle.invoke_hook(
        "pre_tool_call",
        tool_name="discord",
        args={"action": "fetch_messages"},
    )

    assert results[0]["action"] == "modify"
    assert results[-1]["action"] == "approve"
    assert "send_message" in results[-1]["message"]


def test_plugin_rewrite_to_read_stays_unprompted(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    monkeypatch.setattr(lifecycle, "_observe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "_plugin_hooks",
        lambda *_args, **_kwargs: [
            {"action": "modify", "args": {"action": "fetch_messages"}}
        ],
    )
    results = lifecycle.invoke_hook(
        "pre_tool_call", tool_name="discord", args={"action": "send_message"}
    )
    assert results == [{"action": "modify", "args": {"action": "fetch_messages"}}]


def test_permission_directive_reuses_existing_approval_gate(monkeypatch):
    from hermes_cli import plugins

    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    monkeypatch.setattr(lifecycle, "_observe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "_plugin_hooks", lambda *_args, **_kwargs: [])
    seen = {}

    def approve(tool_name, reason, *, rule_key="", approval_callback=None):
        seen.update(tool_name=tool_name, reason=reason, rule_key=rule_key)
        return {"approved": True}

    monkeypatch.setattr("tools.approval.request_tool_approval", approve)
    block, modified = plugins._dispatch_pre_tool_call_hooks(
        "send_message", {"target": "client", "message": "hello"}
    )

    assert block is None
    assert modified is None
    assert seen["tool_name"] == "send_message"
    assert seen["rule_key"].startswith("stardust:external-write:send_message")
