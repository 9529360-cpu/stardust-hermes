"""Consent policy for browser-control steps that act on a real browser.

Each requested step gets one pure decision: may this exact step run now? The caller supplies
the owner approval records, the clock and the stop-control state. The policy returns a decision
and an audit event. It never prompts, clicks, installs, or reads a profile, cookie, token or
config file.

Authority comes only from ``OwnerApproval`` records whose ``source`` is ``OWNER_APPROVAL_SOURCE``.
Page text, dialog wording and free-form request fields never grant anything. Each step is bound to
one exact ``ConnectionIdentity``. The Playwright MCP package is pinned to one version, cookies and
profile data are never copied, and credentials never reach the Playwright child. Design note:
docs/developer-guide/browser-control-consent.md.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Callable, ClassVar, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp"
# Exact pin. It must equal the version locked in experiments/playwright-mcp-host-bridge/package-lock.json
# (a test enforces this). Never ``@latest`` and never a range.
PLAYWRIGHT_MCP_VERSION = "0.0.83"
# The only accepted launcher: the lockfile-installed CLI. ``npx`` or a registry spec is refused.
LOCKFILE_LOCAL_LAUNCHER = "lockfile_local_cli"
# Environment names the Playwright child may inherit. Mirrors the prototype driver's allowlist.
CHILD_ENV_ALLOWLIST = frozenset({
    "PATH", "Path", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR",
})
# Command-line flags the Playwright child may receive.
CHILD_FLAG_ALLOWLIST = frozenset({"--extension", "--profile-dir-name"})
# Substrings that mark credential material in a child argument.
_CREDENTIAL_MARKERS = ("bearer", "authorization", "api_key", "api-key", "token", "secret", "--header")

# Only records written by the Stardust app count as owner approval.
OWNER_APPROVAL_SOURCE = "stardust_app"
# The only dialog origin that can grant a permission. Anything a page paints is not this origin.
NATIVE_UI_ORIGIN = "native_ui"
# The only dialog attribution accepted: the Desktop supervisor's launch record for a connection.
LAUNCH_RECORD_ATTRIBUTION = "bridge_launch_record"
# The only channel for page actions after connection. Native pixel input never drives a page.
CONTROLLER_CHANNEL = "controller"

_IDENTITY_FIELDS = (
    "principal_id", "profile_id", "session_id", "controller_id", "browser_profile_id", "transport_family",
)


@dataclass(frozen=True)
class ConnectionIdentity:
    """The exact connection a step belongs to. Every field must be set; any difference is another connection."""

    principal_id: str
    profile_id: str
    session_id: str
    controller_id: str
    browser_profile_id: str
    transport_family: str

    @classmethod
    def from_scope(cls, scope: Any) -> "ConnectionIdentity":
        """Copy the identity fields of a broker ``ControllerScope``; negotiated capabilities are not identity."""
        return cls(**{name: getattr(scope, name) or "" for name in _IDENTITY_FIELDS})

    def is_complete(self) -> bool:
        return all(getattr(self, name) for name in _IDENTITY_FIELDS)


class ApprovalAction(StrEnum):
    INSTALL_EXTENSION = "install_extension"
    ATTACH_CONTROLLER = "attach_controller"  # also covers launching the pinned child for the connection
    CLICK_NATIVE = "click_native"


@dataclass(frozen=True)
class OwnerApproval:
    """A standing owner grant for one connection. Written by the Stardust app, revocable, optionally expiring."""

    approval_id: str
    source: str
    identity: ConnectionIdentity
    actions: frozenset = frozenset()
    native_permissions: frozenset = frozenset()
    package: str = ""
    version: str = ""
    expires_at: Optional[float] = None
    revoked: bool = False


class _Request:
    """Base for step records. ``KIND`` names the step; ``AUDIT_FIELDS`` lists the non-secret scalars logged."""

    KIND: ClassVar[str] = ""
    AUDIT_FIELDS: ClassVar[tuple] = ()


@dataclass(frozen=True)
class InstallExtension(_Request):
    """Install the pinned official Playwright MCP extension into one isolated browser profile."""

    KIND: ClassVar[str] = "extension_install"
    AUDIT_FIELDS: ClassVar[tuple] = ("package", "version")
    identity: ConnectionIdentity
    package: str
    version: str
    profile_dir: str


@dataclass(frozen=True)
class LaunchPlaywrightChild(_Request):
    """Start the pinned Playwright MCP child that drives the extension for one connection."""

    KIND: ClassVar[str] = "mcp_launch"
    AUDIT_FIELDS: ClassVar[tuple] = ("package", "version", "launcher")
    identity: ConnectionIdentity
    package: str
    version: str
    launcher: str
    env_names: frozenset = frozenset()
    args: tuple = ()


@dataclass(frozen=True)
class AttachController(_Request):
    """Let the controller for one connection attach after its one-time pairing grant was consumed."""

    KIND: ClassVar[str] = "controller_attach"
    AUDIT_FIELDS: ClassVar[tuple] = ("pairing_grant_consumed",)
    identity: ConnectionIdentity
    pairing_grant_consumed: bool


@dataclass(frozen=True)
class NativeDialogClick(_Request):
    """Click a native (browser or OS) approval window. ``origin`` and ``attribution`` are set by the caller
    from the window's owner and the launch record; the dialog's wording is never an input."""

    KIND: ClassVar[str] = "native_dialog_click"
    AUDIT_FIELDS: ClassVar[tuple] = ("permission", "origin", "attribution")
    identity: ConnectionIdentity
    permission: str
    origin: str
    attribution: str


@dataclass(frozen=True)
class WebAction(_Request):
    """Run one page action after connection."""

    KIND: ClassVar[str] = "web_action"
    AUDIT_FIELDS: ClassVar[tuple] = ("capability", "channel", "controller_live")
    identity: ConnectionIdentity
    capability: str
    channel: str
    controller_live: bool


@dataclass(frozen=True)
class CopyProfileData(_Request):
    """Copy cookies or profile data between browser profiles. Never allowed."""

    KIND: ClassVar[str] = "profile_or_cookie_copy"
    AUDIT_FIELDS: ClassVar[tuple] = ()
    identity: ConnectionIdentity
    source_dir: str
    target_dir: str


@dataclass(frozen=True)
class ConsentContext:
    """Per-check inputs: the clock (for approval expiry), the isolated profile root and the stop control."""

    now: float
    isolated_profile_root: str
    kill_switch_engaged: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    kind: str
    reason: str
    approval_id: str = ""
    identity: Optional[ConnectionIdentity] = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    at: float = 0.0

    def audit_event(self) -> dict[str, Any]:
        """Non-secret record of the decision: no grants, tokens, cookies, page text or profile paths."""
        ident = self.identity
        return {
            "event": "browser_control.consent",
            "kind": self.kind,
            "allowed": self.allowed,
            "reason": self.reason,
            "approval_id": self.approval_id,
            "principal_id": ident.principal_id if ident else "",
            "controller_id": ident.controller_id if ident else "",
            "browser_profile_id": ident.browser_profile_id if ident else "",
            "detail": dict(self.detail),
            "at": self.at,
        }


@dataclass(frozen=True)
class _Outcome:
    allowed: bool
    reason: str
    approval_id: str = ""


def _deny(reason: str) -> _Outcome:
    return _Outcome(allowed=False, reason=reason)


def _allow(reason: str, approval: OwnerApproval) -> _Outcome:
    return _Outcome(allowed=True, reason=reason, approval_id=approval.approval_id)


def _is_pinned(package: Any, version: Any) -> bool:
    return package == PLAYWRIGHT_MCP_PACKAGE and version == PLAYWRIGHT_MCP_VERSION


def _identity_gap(request: Any) -> Optional[_Outcome]:
    identity = getattr(request, "identity", None)
    if isinstance(identity, ConnectionIdentity) and identity.is_complete():
        return None
    return _deny("connection identity is missing or incomplete")


def _find(approvals: tuple[OwnerApproval, ...], identity: ConnectionIdentity, action: ApprovalAction,
          context: ConsentContext, predicate: Optional[Callable[[OwnerApproval], bool]] = None) -> Optional[OwnerApproval]:
    """First live owner record for exactly this connection and action: app-sourced, unrevoked, unexpired."""
    for approval in approvals:
        if (approval.source == OWNER_APPROVAL_SOURCE
                and not approval.revoked
                and (approval.expires_at is None or context.now <= approval.expires_at)
                and approval.identity == identity
                and action in approval.actions
                and (predicate is None or predicate(approval))):
            return approval
    return None


def _is_inside(path: str, root: str) -> bool:
    """True only when ``path`` resolves strictly beneath ``root``, after ``..`` and symlink resolution."""
    if not path or not root:
        return False
    child = os.path.normcase(os.path.realpath(path))
    base = os.path.normcase(os.path.realpath(root))
    if child == base:
        return False
    try:
        return os.path.commonpath([child, base]) == base
    except ValueError:  # different drives cannot share a base
        return False


def _argv_is_clean(args: Iterable[Any]) -> bool:
    """Every argument is a string with no credential marker, and every flag is on the allowlist."""
    for arg in args:
        if not isinstance(arg, str) or any(marker in arg.lower() for marker in _CREDENTIAL_MARKERS):
            return False
        if arg.startswith("--") and arg not in CHILD_FLAG_ALLOWLIST:
            return False
    return True


def _env_is_clean(names: Iterable[Any]) -> bool:
    return all(isinstance(name, str) and name in CHILD_ENV_ALLOWLIST for name in names)


def _controller_capabilities() -> frozenset:
    # Lazy: importing the gateway package loads its config, session and delivery modules.
    from gateway.browser_control_broker import BROWSER_CONTROL_CAPABILITIES

    return BROWSER_CONTROL_CAPABILITIES


def _check_install(request: InstallExtension, approvals: tuple[OwnerApproval, ...], context: ConsentContext) -> _Outcome:
    if (gap := _identity_gap(request)) is not None:
        return gap
    if not _is_pinned(request.package, request.version):
        return _deny("extension install must use the exact pinned Playwright MCP version")
    if not _is_inside(request.profile_dir, context.isolated_profile_root):
        return _deny("install target is not inside the isolated browser profile root")
    approval = _find(approvals, request.identity, ApprovalAction.INSTALL_EXTENSION, context,
                     predicate=lambda record: _is_pinned(record.package, record.version))
    if approval is None:
        return _deny("no owner approval for this extension install")
    return _allow("owner approved this install", approval)


def _check_launch(request: LaunchPlaywrightChild, approvals: tuple[OwnerApproval, ...],
                  context: ConsentContext) -> _Outcome:
    if (gap := _identity_gap(request)) is not None:
        return gap
    if not _is_pinned(request.package, request.version):
        return _deny("Playwright child must run the exact pinned version, never @latest or a range")
    if request.launcher != LOCKFILE_LOCAL_LAUNCHER:
        return _deny("Playwright child must start from the lockfile-installed CLI, not npx or a registry spec")
    if not _env_is_clean(request.env_names):
        return _deny("Playwright child environment carries a variable outside the allowlist")
    if not _argv_is_clean(request.args):
        return _deny("Playwright child arguments must be allowlisted flags without credential material")
    approval = _find(approvals, request.identity, ApprovalAction.ATTACH_CONTROLLER, context)
    if approval is None:
        return _deny("no owner approval for this connection")
    return _allow("owner approved this connection", approval)


def _check_attach(request: AttachController, approvals: tuple[OwnerApproval, ...],
                  context: ConsentContext) -> _Outcome:
    if (gap := _identity_gap(request)) is not None:
        return gap
    if request.pairing_grant_consumed is not True:
        return _deny("pairing bypass: the one-time pairing grant was not consumed")
    approval = _find(approvals, request.identity, ApprovalAction.ATTACH_CONTROLLER, context)
    if approval is None:
        return _deny("no owner approval for this connection")
    return _allow("owner approved this connection", approval)


def _check_native(request: NativeDialogClick, approvals: tuple[OwnerApproval, ...],
                  context: ConsentContext) -> _Outcome:
    if (gap := _identity_gap(request)) is not None:
        return gap
    if request.origin != NATIVE_UI_ORIGIN:
        return _deny("dialog is rendered by page content; page content cannot grant a permission")
    if request.attribution != LAUNCH_RECORD_ATTRIBUTION:
        return _deny("dialog is not attributed to the launch record of an approved connection")
    if not request.permission:
        return _deny("dialog names no permission")
    approval = _find(approvals, request.identity, ApprovalAction.CLICK_NATIVE, context,
                     predicate=lambda record: request.permission in record.native_permissions)
    if approval is None:
        return _deny("no owner approval for this native permission on this connection")
    return _allow("owner approved this permission for this connection", approval)


def _check_web(request: WebAction, approvals: tuple[OwnerApproval, ...], context: ConsentContext) -> _Outcome:
    if (gap := _identity_gap(request)) is not None:
        return gap
    if request.channel != CONTROLLER_CHANNEL:
        return _deny("page actions after connection must go through the browser controller")
    if request.capability not in _controller_capabilities():
        return _deny("capability is outside the browser-controller allowlist")
    if request.controller_live is not True:
        return _deny("controller for this connection is not live")
    approval = _find(approvals, request.identity, ApprovalAction.ATTACH_CONTROLLER, context)
    if approval is None:
        return _deny("no owner approval for this connection")
    return _allow("owner approved this connection; the action goes to its controller", approval)


def _check_copy(request: CopyProfileData, approvals: tuple[OwnerApproval, ...], context: ConsentContext) -> _Outcome:
    return _deny("copying cookies or profile data is never allowed")


_CHECKERS: dict[type, Callable[[Any, tuple[OwnerApproval, ...], ConsentContext], _Outcome]] = {
    InstallExtension: _check_install,
    LaunchPlaywrightChild: _check_launch,
    AttachController: _check_attach,
    NativeDialogClick: _check_native,
    WebAction: _check_web,
    CopyProfileData: _check_copy,
}


def evaluate(request: Any, approvals: Iterable[OwnerApproval], *, context: ConsentContext) -> Decision:
    """Decide one step. Pure: no prompting, no mutation, no I/O beyond path normalization."""
    records = tuple(approvals)
    if context.kill_switch_engaged:
        outcome = _deny("stop control is engaged; browser-control steps are halted")
    else:
        checker = _CHECKERS.get(type(request))
        outcome = checker(request, records, context) if checker else _deny("unknown browser-control request")
    return Decision(
        allowed=outcome.allowed,
        kind=getattr(request, "KIND", "unknown"),
        reason=outcome.reason,
        approval_id=outcome.approval_id,
        identity=getattr(request, "identity", None),
        detail={name: getattr(request, name) for name in getattr(request, "AUDIT_FIELDS", ())},
        at=context.now,
    )


def authorize(request: Any, approvals: Iterable[OwnerApproval], *, context: ConsentContext,
              audit_sink: Callable[[dict[str, Any]], Any]) -> Decision:
    """Decide, then record the decision before the caller may act. If the audit write fails, the step is denied."""
    decision = evaluate(request, approvals, context=context)
    try:
        audit_sink(decision.audit_event())
    except Exception:
        logger.warning("browser-control consent audit write failed; step not performed", exc_info=True)
        return replace(decision, allowed=False, reason="audit log unavailable; step not performed")
    return decision
