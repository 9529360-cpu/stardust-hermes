"""Browser-control consent policy: allowed and denied paths, with fakes only.

Nothing here starts a browser, installs an extension, clicks a dialog or reads a profile.
Profile paths are temporary directories under pytest's tmp_path.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from gateway.browser_control_broker import (
    BROWSER_CONTROL_ARTIFACT_CAPABILITIES,
    BROWSER_CONTROL_CAPABILITIES,
    BROWSER_CONTROL_DEVELOPER_CAPABILITIES,
    ControllerScope,
)
from tools import browser_control_consent as consent
from tools.browser_control_consent import (
    ApprovalAction,
    AttachController,
    ConnectionIdentity,
    ConsentContext,
    CopyProfileData,
    InstallExtension,
    LaunchPlaywrightChild,
    NativeDialogClick,
    OwnerApproval,
    WebAction,
    authorize,
    evaluate,
)

PACKAGE = consent.PLAYWRIGHT_MCP_PACKAGE
VERSION = consent.PLAYWRIGHT_MCP_VERSION
NOW = 1_000.0
NATIVE_PERMISSION = "extension_install"
INSTALL = ApprovalAction.INSTALL_EXTENSION
ATTACH = ApprovalAction.ATTACH_CONTROLLER
CLICK = ApprovalAction.CLICK_NATIVE
ALL_ACTIONS = (INSTALL, ATTACH, CLICK)

IDENTITY = ConnectionIdentity(
    principal_id="principal:desktop:0f1e2d3c",
    profile_id="default",
    session_id="session-a",
    controller_id="controller-a",
    browser_profile_id="stardust-isolated-1",
    transport_family="local-api",
)
OTHER_SESSION = dataclasses.replace(IDENTITY, session_id="session-b")


def approval(*actions, identity=IDENTITY, permissions=(NATIVE_PERMISSION,), package=PACKAGE, version=VERSION,
             source=consent.OWNER_APPROVAL_SOURCE, expires_at=None, revoked=False, approval_id="ap-1"):
    """A standing owner record for one connection. With no actions given, it approves every action."""
    return OwnerApproval(
        approval_id=approval_id, source=source, identity=identity,
        actions=frozenset(actions or ALL_ACTIONS), native_permissions=frozenset(permissions),
        package=package, version=version, expires_at=expires_at, revoked=revoked,
    )


def install(root, **overrides):
    fields = {
        "identity": IDENTITY, "package": PACKAGE, "version": VERSION,
        "profile_dir": str(Path(root) / "stardust-isolated-1"),
    }
    return InstallExtension(**{**fields, **overrides})


def launch(**overrides):
    fields = {
        "identity": IDENTITY, "package": PACKAGE, "version": VERSION,
        "launcher": consent.LOCKFILE_LOCAL_LAUNCHER, "env_names": frozenset({"PATH", "TEMP", "HOME"}),
        "args": ("--extension", "--profile-dir-name", "stardust-isolated-1"),
    }
    return LaunchPlaywrightChild(**{**fields, **overrides})


def attach(**overrides):
    return AttachController(**{"identity": IDENTITY, "pairing_grant_consumed": True, **overrides})


def native(**overrides):
    fields = {
        "identity": IDENTITY, "permission": NATIVE_PERMISSION,
        "origin": consent.NATIVE_UI_ORIGIN, "attribution": consent.LAUNCH_RECORD_ATTRIBUTION,
    }
    return NativeDialogClick(**{**fields, **overrides})


def web(**overrides):
    fields = {
        "identity": IDENTITY, "capability": "browser_click",
        "channel": consent.CONTROLLER_CHANNEL, "controller_live": True,
    }
    return WebAction(**{**fields, **overrides})


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "isolated-profiles"
    base.mkdir()
    return str(base)


def decide(request, records, root, *, kill_switch_engaged=False):
    context = ConsentContext(now=NOW, isolated_profile_root=root, kill_switch_engaged=kill_switch_engaged)
    return evaluate(request, records, context=context)


# -- Pin and invariants ----------------------------------------------------------------------

def test_playwright_pin_is_exact_and_equals_the_locked_prototype_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)
    repo = Path(__file__).resolve().parents[2]
    lock = json.loads((repo / "experiments" / "playwright-mcp-host-bridge" / "package-lock.json")
                      .read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/@playwright/mcp"]["version"] == VERSION


def test_child_env_allowlist_names_no_credentials():
    markers = ("KEY", "TOKEN", "SECRET", "GRANT", "COOKIE", "PASSWORD", "STARDUST")
    assert not [name for name in consent.CHILD_ENV_ALLOWLIST if any(m in name.upper() for m in markers)]


def test_connection_identity_mirrors_the_broker_scope_identity():
    scope_fields = {f.name for f in dataclasses.fields(ControllerScope)} - {"capabilities"}
    assert {f.name for f in dataclasses.fields(ConnectionIdentity)} == scope_fields
    scope = ControllerScope(principal_id="p", profile_id="prof", session_id="s", controller_id="c",
                            browser_profile_id="b", transport_family="local-api",
                            capabilities=frozenset({"browser_click"}))
    assert ConnectionIdentity.from_scope(scope) == ConnectionIdentity("p", "prof", "s", "c", "b", "local-api")


# -- Extension install -----------------------------------------------------------------------

def test_install_allowed_for_pinned_version_in_isolated_root_with_owner_approval(root):
    decision = decide(install(root), [approval()], root)
    assert decision.allowed
    assert decision.approval_id == "ap-1"


@pytest.mark.parametrize("records", [
    pytest.param([], id="no-record"),
    pytest.param([approval(INSTALL, identity=OTHER_SESSION)], id="other-session"),
    pytest.param([approval(ATTACH)], id="attach-only"),
    pytest.param([approval(INSTALL, revoked=True)], id="revoked"),
    pytest.param([approval(INSTALL, expires_at=NOW - 1)], id="expired"),
    pytest.param([approval(INSTALL, version="0.0.82")], id="approved-other-version"),
    pytest.param([approval(INSTALL, source="web_page")], id="page-sourced-record"),
])
def test_install_denied_without_a_matching_owner_approval(root, records):
    assert not decide(install(root), records, root).allowed


@pytest.mark.parametrize("profile_dir", [
    pytest.param(lambda root: str(Path(root).parent / "Chrome" / "User Data"), id="sibling-of-root"),
    pytest.param(lambda root: str(Path(root) / ".." / "escape"), id="traversal"),
    pytest.param(lambda root: root, id="root-itself"),
    pytest.param(lambda root: "", id="no-target"),
])
def test_install_denied_outside_the_isolated_profile_root(root, profile_dir):
    decision = decide(install(root, profile_dir=profile_dir(root)), [approval()], root)
    assert not decision.allowed
    assert "isolated" in decision.reason


@pytest.mark.parametrize(("package", "version"), [
    pytest.param(PACKAGE, "latest", id="latest-tag"),
    pytest.param(PACKAGE, "^0.0.83", id="caret-range"),
    pytest.param(PACKAGE, "~0.0.83", id="tilde-range"),
    pytest.param(PACKAGE, "0.0.84", id="other-release"),
    pytest.param(PACKAGE, "", id="empty-version"),
    pytest.param(PACKAGE, " 0.0.83", id="padded-version"),
    pytest.param(f"{PACKAGE}@latest", VERSION, id="latest-spec-in-package"),
])
def test_install_denied_for_any_unpinned_package_or_version(root, package, version):
    assert not decide(install(root, package=package, version=version), [approval()], root).allowed


def test_install_denied_when_the_connection_identity_is_incomplete(root):
    partial = dataclasses.replace(IDENTITY, controller_id="")
    assert not decide(install(root, identity=partial), [approval(identity=partial)], root).allowed


def test_install_denied_when_no_isolated_root_is_configured(root):
    assert not decide(install(root), [approval()], "").allowed


# -- Playwright child launch -----------------------------------------------------------------

def test_launch_allowed_for_pinned_lockfile_cli_with_clean_env_and_flags(root):
    assert decide(launch(), [approval(ATTACH)], root).allowed


@pytest.mark.parametrize("request_", [
    pytest.param(launch(launcher="npx"), id="npx"),
    pytest.param(launch(launcher="registry-spec"), id="registry-spec"),
    pytest.param(launch(version="latest"), id="latest"),
    pytest.param(launch(env_names=frozenset({"PATH", "STARDUST_API_SERVER_KEY"})), id="bearer-in-env"),
    pytest.param(launch(env_names=frozenset({"PATH", "STARDUST_BROWSER_CONTROL_GRANT"})), id="grant-in-env"),
    pytest.param(launch(args=("--extension", "--header", "Authorization: Bearer abc")), id="bearer-in-argv"),
    pytest.param(launch(args=("--extension", "--unlisted-flag")), id="unlisted-flag"),
    pytest.param(launch(args=("--extension", 42)), id="non-string-argument"),
])
def test_launch_denied_for_unpinned_launcher_or_credential_bearing_child(root, request_):
    assert not decide(request_, [approval(ATTACH)], root).allowed


def test_launch_denied_without_owner_approval_for_this_connection(root):
    assert not decide(launch(), [approval(INSTALL)], root).allowed


# -- Controller attach (pairing) -------------------------------------------------------------

def test_attach_allowed_after_the_pairing_grant_is_consumed_and_owner_approved(root):
    assert decide(attach(), [approval(ATTACH)], root).allowed


@pytest.mark.parametrize("consumed", [False, "yes", 1])
def test_attach_denied_unless_the_pairing_grant_was_consumed(root, consumed):
    decision = decide(attach(pairing_grant_consumed=consumed), [approval(ATTACH)], root)
    assert not decision.allowed
    assert "pairing" in decision.reason


@pytest.mark.parametrize("records", [
    pytest.param([], id="no-record"),
    pytest.param([approval(ATTACH, identity=OTHER_SESSION)], id="other-session"),
    pytest.param([approval(INSTALL)], id="install-only"),
])
def test_attach_denied_without_owner_approval_for_this_connection(root, records):
    assert not decide(attach(), records, root).allowed


# -- Native approval dialogs -----------------------------------------------------------------

def test_native_click_allowed_for_native_dialog_of_approved_connection_and_permission(root):
    decision = decide(native(), [approval(CLICK)], root)
    assert decision.allowed
    assert decision.approval_id == "ap-1"


def test_page_rendered_dialog_is_denied_even_with_a_matching_owner_approval(root):
    """A page can paint an Allow prompt. A matching owner record must not turn that prompt into a grant."""
    decision = decide(native(origin="web_page", attribution="page_content"), [approval(CLICK)], root)
    assert not decision.allowed
    assert "page content" in decision.reason


def test_page_asking_for_approval_with_no_owner_record_is_denied(root):
    """Page text that asks for approval is data. With no owner record, the click is refused."""
    decision = decide(native(origin="web_page", attribution="page_content"), [], root)
    assert not decision.allowed


@pytest.mark.parametrize("attribution", ["window_title", "page_content", "", "owner_approved"])
def test_native_click_denied_unless_attributed_to_a_launch_record(root, attribution):
    assert not decide(native(attribution=attribution), [approval(CLICK)], root).allowed


def test_native_click_denied_for_a_permission_the_owner_did_not_approve(root):
    assert not decide(native(permission="debugger_attach"), [approval(CLICK)], root).allowed


def test_native_click_denied_when_the_record_lacks_the_click_action(root):
    assert not decide(native(), [approval(INSTALL, ATTACH)], root).allowed


@pytest.mark.parametrize("record", [
    pytest.param(approval(CLICK, identity=OTHER_SESSION), id="other-connection"),
    pytest.param(approval(CLICK, revoked=True), id="revoked"),
    pytest.param(approval(CLICK, expires_at=NOW - 1), id="expired"),
    pytest.param(approval(CLICK, source="web_page"), id="page-sourced-record"),
    pytest.param(approval(CLICK, permissions=()), id="no-permissions-listed"),
])
def test_native_click_denied_for_foreign_revoked_expired_or_forged_records(root, record):
    assert not decide(native(), [record], root).allowed


def test_native_click_denied_for_a_dialog_with_an_empty_permission_name(root):
    assert not decide(native(permission=""), [approval(CLICK, permissions=("",))], root).allowed


def test_authorize_records_the_click_before_allowing_it(root):
    records = []
    context = ConsentContext(now=NOW, isolated_profile_root=root)
    decision = authorize(native(), [approval(CLICK)], context=context, audit_sink=records.append)
    assert decision.allowed
    assert len(records) == 1
    assert records[0]["allowed"] is True
    assert records[0]["detail"] == {
        "permission": NATIVE_PERMISSION,
        "origin": consent.NATIVE_UI_ORIGIN,
        "attribution": consent.LAUNCH_RECORD_ATTRIBUTION,
    }


def test_authorize_denies_the_click_when_the_audit_write_fails(root):
    def failing_sink(event):
        raise OSError("audit store unavailable")

    context = ConsentContext(now=NOW, isolated_profile_root=root)
    decision = authorize(native(), [approval(CLICK)], context=context, audit_sink=failing_sink)
    assert not decision.allowed
    assert "audit" in decision.reason


def test_audit_event_carries_no_secret_cookie_page_text_or_path(root):
    event = decide(native(), [approval(CLICK)], root).audit_event()
    forbidden = ("token", "grant", "cookie", "text", "dir", "path")
    assert not [key for key in event if any(word in key for word in forbidden)]


# -- Page actions after connection -----------------------------------------------------------

@pytest.mark.parametrize("capability", sorted(BROWSER_CONTROL_CAPABILITIES))
def test_web_action_allowed_through_a_live_controller_for_each_allowlisted_capability(root, capability):
    assert decide(web(capability=capability), [approval(ATTACH)], root).allowed


def test_web_action_denied_when_driven_by_native_pixels(root):
    decision = decide(web(channel="computer_use_pixels"), [approval(ATTACH, CLICK)], root)
    assert not decision.allowed
    assert "controller" in decision.reason


def test_web_action_denied_when_the_controller_is_not_live(root):
    assert not decide(web(controller_live=False), [approval(ATTACH)], root).allowed


@pytest.mark.parametrize("capability", sorted(BROWSER_CONTROL_DEVELOPER_CAPABILITIES
                                              | BROWSER_CONTROL_ARTIFACT_CAPABILITIES))
def test_web_action_denied_for_raw_cdp_evaluation_and_file_transfer(root, capability):
    assert not decide(web(capability=capability), [approval(ATTACH)], root).allowed


def test_web_action_denied_without_owner_approval_for_this_connection(root):
    assert not decide(web(), [approval(INSTALL, CLICK)], root).allowed


@pytest.mark.parametrize(("name", "value"), [
    pytest.param("principal_id", "principal:desktop:other", id="principal"),
    pytest.param("profile_id", "other-profile", id="profile"),
    pytest.param("session_id", "session-b", id="session"),
    pytest.param("controller_id", "controller-b", id="controller"),
    pytest.param("browser_profile_id", "other-browser-profile", id="browser-profile"),
    pytest.param("transport_family", "remote", id="transport"),
])
def test_any_identity_field_difference_is_a_different_connection(root, name, value):
    other = dataclasses.replace(IDENTITY, **{name: value})
    assert not decide(web(identity=other), [approval(ATTACH)], root).allowed


# -- Cookie and profile copy -----------------------------------------------------------------

@pytest.mark.parametrize("records", [
    pytest.param([], id="no-approval"),
    pytest.param([approval()], id="fully-approved"),
])
def test_cookie_or_profile_copy_is_never_allowed(root, records):
    request = CopyProfileData(identity=IDENTITY, source_dir="source", target_dir="target")
    decision = decide(request, records, root)
    assert not decision.allowed
    assert "never" in decision.reason


# -- Stop control and unknown requests -------------------------------------------------------

ALLOWED_PAIRS = [
    pytest.param(lambda root: install(root), lambda: [approval()], id="install"),
    pytest.param(lambda root: launch(), lambda: [approval()], id="launch"),
    pytest.param(lambda root: attach(), lambda: [approval()], id="attach"),
    pytest.param(lambda root: native(), lambda: [approval()], id="native-click"),
    pytest.param(lambda root: web(), lambda: [approval()], id="web-action"),
]


@pytest.mark.parametrize(("make_request", "make_records"), ALLOWED_PAIRS)
def test_stop_control_halts_every_step_that_would_otherwise_run(root, make_request, make_records):
    assert decide(make_request(root), make_records(), root).allowed
    stopped = decide(make_request(root), make_records(), root, kill_switch_engaged=True)
    assert not stopped.allowed
    assert "stop control" in stopped.reason


def test_unknown_request_types_are_denied(root):
    assert not decide(object(), [approval()], root).allowed
