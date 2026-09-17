from types import SimpleNamespace

from hermes_cli import assistant_permissions
from tools import browser_escape_approval as approval
from tools import browser_extension_router as router


def _activate_personal_assistant(monkeypatch, *, durable=False):
    monkeypatch.setattr(assistant_permissions, "personal_assistant_permissions_active", lambda: True)
    monkeypatch.setattr(assistant_permissions, "_durable_worker_confirmation_active", lambda: durable)


def test_console_without_expression_stays_frictionless():
    risk = approval.inspect_browser_escape("browser_console", {"clear": True, "expression": None})
    assert risk.requires_approval is False


def test_console_expression_requires_exact_fingerprinted_approval():
    first = approval.inspect_browser_escape(
        "browser_console", {"expression": "document.querySelector('form').submit()"}
    )
    second = approval.inspect_browser_escape(
        "browser_console", {"expression": "document.querySelector('form').submit()"}
    )
    changed = approval.inspect_browser_escape(
        "browser_console", {"expression": "document.querySelector('form').remove()"}
    )

    assert first.requires_approval is True
    assert first.context_sha256 == second.context_sha256
    assert first.context_sha256 != changed.context_sha256
    assert "querySelector" not in first.reason
    assert first.rule_key.endswith(first.context_sha256[:20])


def test_only_explicit_read_only_cdp_methods_skip_approval():
    assert approval.inspect_browser_escape(
        "browser_cdp", {"method": "Browser.getVersion", "params": {}}
    ).requires_approval is False
    assert approval.inspect_browser_escape(
        "browser_cdp", {"method": "Page.getFrameTree", "params": {}}
    ).requires_approval is False

    evaluate = approval.inspect_browser_escape(
        "browser_cdp",
        {"method": "Runtime.evaluate", "params": {"expression": "location.href='https://example.test'"}},
    )
    navigate = approval.inspect_browser_escape(
        "browser_cdp",
        {"method": "Page.navigate", "params": {"url": "https://example.test"}},
    )
    assert evaluate.requires_approval is True
    assert navigate.requires_approval is True


def test_durable_approval_receives_only_opaque_fingerprint(monkeypatch):
    from tools import background_task_approval

    _activate_personal_assistant(monkeypatch, durable=True)
    captured = {}

    def authorize(tool_name, args, *, reason, rule_key):
        captured.update(tool_name=tool_name, args=dict(args), reason=reason, rule_key=rule_key)
        return SimpleNamespace(allowed=True, message="")

    monkeypatch.setattr(background_task_approval, "authorize_or_block_current_worker", authorize)
    raw_secret = "sk-test-secret-that-must-not-enter-ledger"
    args = {
        "method": "Runtime.evaluate",
        "params": {"expression": f"fetch('https://example.test/?token={raw_secret}')"},
    }

    assert approval.guard_browser_escape("browser_cdp", args) is None
    assert captured["tool_name"] == "browser_cdp"
    assert set(captured["args"]) == {"context_sha256"}
    assert len(captured["args"]["context_sha256"]) == 64
    assert raw_secret not in repr(captured)


def test_interactive_approval_is_invalidated_when_args_drift(monkeypatch):
    from tools import approval as interactive_approval

    _activate_personal_assistant(monkeypatch, durable=False)
    args = {"expression": "document.title"}

    def approve_then_mutate(tool_name, reason, *, rule_key=""):
        assert tool_name == "browser_console"
        args["expression"] = "document.querySelector('form').submit()"
        return {"approved": True, "status": "approved"}

    monkeypatch.setattr(interactive_approval, "request_tool_approval", approve_then_mutate)

    blocked = approval.guard_browser_escape("browser_console", args)
    assert blocked is not None
    assert "changed after approval" in blocked


def test_legacy_router_blocks_escape_before_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(
        approval,
        "guard_browser_escape",
        lambda action, args: '{"success": false, "status": "waiting_confirmation"}',
    )

    result = router.route_browser_tool(
        "browser_cdp",
        {"method": "Runtime.evaluate", "params": {"expression": "1+1"}},
        fallback=lambda: calls.append("fallback") or "unsafe",
        broker=object(),
        enabled=False,
    )

    assert "waiting_confirmation" in result
    assert calls == []


class _ControllerBroker:
    def __init__(self):
        self.calls = []

    def scope_for_session(self, **identity):
        self.calls.append(("scope", identity))
        return "scope"

    def select(self, scope, action):
        self.calls.append(("select", scope, action))
        return "controller"

    def dispatch(self, scope, *, action, arguments, tool_call_id=""):
        self.calls.append(("dispatch", scope, action, arguments, tool_call_id))
        return '{"success": true}'


def test_controller_router_blocks_escape_before_dispatch(monkeypatch):
    broker = _ControllerBroker()
    monkeypatch.setattr(
        approval,
        "guard_browser_escape",
        lambda action, args: '{"success": false, "status": "waiting_confirmation"}',
    )

    result = router.route_browser_tool(
        "browser_console",
        {"expression": "document.body.innerHTML = ''"},
        fallback=lambda: "unsafe-fallback",
        broker=broker,
        enabled=True,
        session_id="session-fixture",
        principal_id="principal-fixture",
        transport_family="local-api",
        tool_call_id="tool-call-fixture",
    )

    assert "waiting_confirmation" in result
    assert not any(call[0] == "dispatch" for call in broker.calls)
