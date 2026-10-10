import json

import pytest

from tools.browser_use_snapshot import STRUCTURED_SNAPSHOT_PREAMBLE


class _FakeCdp:
    def __init__(self):
        self.loader_id = "loader-1"
        self.released = []
        self.calls = []
        self.nodes = [
            {
                "backendDOMNodeId": 11,
                "ignored": False,
                "role": {"value": "button"},
                "name": {"value": "Save"},
                "properties": [
                    {"name": "focusable", "value": {"value": True}},
                    {"name": "disabled", "value": {"value": False}},
                ],
            },
            {
                "backendDOMNodeId": 12,
                "ignored": False,
                "role": {"value": "textbox"},
                "name": {"value": "Email"},
                "properties": [
                    {"name": "focusable", "value": {"value": True}},
                ],
            },
            {
                "backendDOMNodeId": 13,
                "ignored": False,
                "role": {"value": "generic"},
                "name": {"value": "not actionable"},
                "properties": [],
            },
        ]

    def __call__(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {
                        "id": "frame-1",
                        "loaderId": self.loader_id,
                        "url": "https://example.test/form",
                    }
                }
            }
        if method == "Accessibility.getFullAXTree":
            return {"nodes": self.nodes}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "object-1"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": {"success": True, "length": 6}}}
        if method == "Runtime.releaseObject":
            self.released.append(kwargs["objectId"])
            return {}
        raise AssertionError(f"unexpected CDP method: {method}")


def _load_helpers(cdp):
    namespace = {"cdp": cdp}
    exec(
        compile(STRUCTURED_SNAPSHOT_PREAMBLE, "<structured-snapshot-preamble>", "exec"),
        namespace,
    )
    return namespace


def _ref_payload(ref):
    import base64

    encoded = ref.removeprefix("@ax1.")
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def test_snapshot_returns_navigation_scoped_structured_actionable_refs():
    cdp = _FakeCdp()
    helpers = _load_helpers(cdp)

    snapshot = helpers["browser_snapshot_refs"]()

    assert snapshot["contract"] == "stardust.browser-use.snapshot.v1"
    assert snapshot["url"] == "https://example.test/form"
    assert snapshot["total_elements"] == 2
    assert [element["role"] for element in snapshot["elements"]] == [
        "button",
        "textbox",
    ]
    assert all(element["ref"].startswith("@ax1.") for element in snapshot["elements"])
    payload = _ref_payload(snapshot["elements"][0]["ref"])
    assert payload["node"] == 11
    assert payload["frame"] == "frame-1"
    assert payload["loader"] == "loader-1"


def test_snapshot_ref_action_revalidates_node_and_does_not_echo_fill_text():
    cdp = _FakeCdp()
    helpers = _load_helpers(cdp)
    refs = helpers["browser_snapshot_refs"]()["elements"]

    clicked = helpers["browser_click_ref"](refs[0]["ref"])
    filled = helpers["browser_fill_ref"](refs[1]["ref"], "secret@example.test")

    assert clicked == {"success": True, "action": "click", "ref": refs[0]["ref"]}
    assert filled == {
        "success": True,
        "action": "fill",
        "ref": refs[1]["ref"],
        "value_length": 6,
    }
    assert "secret@example.test" not in json.dumps(filled)
    assert cdp.released == ["object-1", "object-1"]


def test_snapshot_ref_fails_closed_after_navigation():
    cdp = _FakeCdp()
    helpers = _load_helpers(cdp)
    ref = helpers["browser_snapshot_refs"]()["elements"][0]["ref"]
    cdp.loader_id = "loader-2"

    with pytest.raises(RuntimeError, match="Stale browser ref"):
        helpers["browser_click_ref"](ref)

    assert not any(method == "DOM.resolveNode" for method, _ in cdp.calls)


def test_snapshot_ref_rejects_malformed_refs():
    cdp = _FakeCdp()
    helpers = _load_helpers(cdp)

    with pytest.raises(ValueError, match="Invalid browser ref"):
        helpers["browser_click_ref"]("@e1")


def test_browser_exec_injects_the_live_snapshot_contract(monkeypatch):
    import tools.browser_use_cli as browser_use_cli
    import subprocess

    captured = {}
    # browser_exec asks a human before running host Python; approve it here so the test reaches the CLI launch.
    monkeypatch.setattr("tools.approval.request_tool_approval", lambda *a, **k: {"approved": True})
    monkeypatch.setattr(browser_use_cli, "_find_cli", lambda: ["browser-use"])
    monkeypatch.setattr(browser_use_cli, "_route_backend", lambda *args: None)
    monkeypatch.setattr(browser_use_cli, "_base_subprocess_env", lambda: {})
    monkeypatch.setattr(browser_use_cli, "_attach_vault_supervisor", lambda *args: None)
    monkeypatch.setattr(browser_use_cli, "_workspace_dir", lambda task_id: None)
    monkeypatch.setattr(
        browser_use_cli,
        "_run_cli_killing_process_group",
        lambda cmd, code, env, timeout: (
            captured.update(code=code) or subprocess.CompletedProcess(cmd, 0, "ok", "")
        ),
    )

    result = json.loads(browser_use_cli.browser_exec("print('model code')"))

    assert result["success"] is True
    assert "def browser_snapshot_refs" in captured["code"]
    assert "def browser_click_ref" in captured["code"]
    assert captured["code"].startswith("print('model code')")
