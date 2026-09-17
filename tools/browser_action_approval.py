"""Semantic approval guard for consequential browser clicks in Stardust assistant sessions.

Normal browser navigation stays frictionless. Before clicking a referenced accessibility
node, this module takes a fresh read-only snapshot and upgrades only obvious commit/write
controls (Send, Submit, Buy, Delete, Create, etc.) to the existing human/durable approval
flow. Durable grants bind to an opaque hash of URL + ref + role + accessible name, so a
restarted worker or remapped ref cannot consume consent for a different target.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BrowserClickRisk:
    requires_approval: bool
    context_sha256: str = ""
    reason: str = ""
    rule_key: str = ""
    role: str = ""
    name: str = ""
    url: str = ""


_INTERACTIVE_ROLES = (
    "button", "link", "menuitem", "tab", "checkbox", "radio", "switch",
    "textbox", "searchbox", "combobox", "slider",
)
_ROLE_RE = re.compile(r"\b(" + "|".join(map(re.escape, _INTERACTIVE_ROLES)) + r")\b", re.IGNORECASE)
_QUOTED_NAME_RE = re.compile(r'"((?:\\.|[^"\\])*)"')

# Deliberately limited to controls whose accessible name itself says the next click commits
# or changes external state. Navigation words such as Next/Continue/Search/Open are absent.
_COMMIT_EN_RE = re.compile(
    r"(?:^|\b)(?:send|submit|publish|post|share|invite|create|register|save|update|"
    r"delete|remove|revoke|buy|purchase|pay|transfer|donate|subscribe|unsubscribe|"
    r"follow|unfollow|book|reserve|schedule|apply|checkout|place\s+order|order\s+now|"
    r"create\s+account|sign\s+up|save\s+changes|confirm\s+order|complete\s+purchase)(?:\b|$)",
    re.IGNORECASE,
)
_COMMIT_CJK_RE = re.compile(
    r"发送|提交|发布|分享|邀请|创建|注册|保存|更新|删除|移除|撤销|购买|付款|支付|转账|"
    r"捐赠|订阅|取消订阅|关注|取消关注|预订|预约|申请|结账|下单|确认订单|创建账户|立即购买"
)


def _clean_ref(ref: Any) -> str:
    return str(ref or "").strip().lstrip("@").strip()


def _ref_line(snapshot: str, ref: str) -> str:
    """Return the exact accessibility line containing ``ref`` across both supported formats."""
    clean = _clean_ref(ref)
    if not clean:
        return ""
    token = re.escape(clean)
    patterns = (
        re.compile(rf"\[(?:ref=)?@?{token}\](?![\w-])", re.IGNORECASE),
        re.compile(rf"(?<![\w-])@{token}(?![\w-])", re.IGNORECASE),
    )
    for line in str(snapshot or "").splitlines():
        if any(pattern.search(line) for pattern in patterns):
            return line.strip()
    return ""


def _element_identity(snapshot: str, ref: str) -> tuple[str, str, str]:
    line = _ref_line(snapshot, ref)
    if not line:
        return "", "", ""
    role_match = _ROLE_RE.search(line)
    name_match = _QUOTED_NAME_RE.search(line)
    role = role_match.group(1).lower() if role_match else ""
    name = bytes(name_match.group(1), "utf-8").decode("unicode_escape") if name_match else ""
    return role, name.strip(), line


def _looks_consequential(name: str) -> bool:
    text = str(name or "").strip()
    return bool(text) and bool(_COMMIT_EN_RE.search(text) or _COMMIT_CJK_RE.search(text))


def _safe_url_label(url: str) -> str:
    """Human display URL without credentials/query/fragment; never used for authorization."""
    try:
        parsed = urlsplit(str(url or ""))
        if not parsed.scheme or not parsed.hostname:
            return "the current page"
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = parsed.path or "/"
        label = f"{parsed.scheme}://{host}{path}"
        return label[:180]
    except Exception:
        return "the current page"


def _snapshot_non_camofox(task_id: Optional[str]) -> tuple[str, str]:
    """Fresh snapshot of an already-active agent-browser session; never creates a new session."""
    from tools import browser_tool as bt

    raw_task = task_id or "default"
    session_key = bt._last_active_session_key.get(raw_task)
    if not session_key:
        return "", ""
    with bt._cleanup_lock:
        info = bt._active_sessions.get(session_key)
        if not info or not bt._session_info_owned_by_task(info, raw_task, session_key):
            return "", ""

    result = bt._session._run_browser_command(session_key, "snapshot", ["-c"])
    if not result.get("success"):
        return "", ""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    snapshot = str(data.get("snapshot") or "")
    url = str(data.get("url") or "")
    if not url:
        try:
            current = bt._session._run_browser_command(
                session_key, "eval", ["window.location.href"], timeout=5, _engine_override="auto"
            )
            if current.get("success"):
                url = str((current.get("data") or {}).get("result") or "").strip().strip('"').strip("'")
        except Exception:
            url = ""
    return snapshot, url


def _snapshot_camofox(task_id: Optional[str]) -> tuple[str, str]:
    """Fresh Camofox snapshot. Session adoption is read-only; no tab is created here."""
    from tools import browser_camofox as cf

    session = cf._get_session(task_id)
    if not session.get("tab_id"):
        return "", ""
    data = cf._snapshot_data(session)
    return str(data.get("snapshot") or ""), str(data.get("url") or "")


def inspect_browser_click(ref: Any, task_id: Optional[str] = None) -> BrowserClickRisk:
    """Inspect one referenced element without clicking it."""
    clean = _clean_ref(ref)
    if not clean:
        return BrowserClickRisk(False)
    try:
        from tools import browser_tool as bt

        snapshot, url = _snapshot_camofox(task_id) if bt._is_camofox_mode() else _snapshot_non_camofox(task_id)
    except Exception:
        return BrowserClickRisk(False)

    role, name, _line = _element_identity(snapshot, clean)
    if not role or not _looks_consequential(name):
        return BrowserClickRisk(False, role=role, name=name, url=url)

    material = json.dumps(
        {"ref": clean.lower(), "role": role, "name": name, "url": url},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    context_sha256 = hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()
    label = _safe_url_label(url)
    reason = (
        f"Stardust wants to click the {role} '{name}' on {label}. "
        "This control appears to submit, publish, purchase, delete, create, or otherwise change external state; confirm first."
    )
    return BrowserClickRisk(
        True,
        context_sha256=context_sha256,
        reason=reason,
        rule_key=f"stardust:browser-external-write:click:{context_sha256[:20]}",
        role=role,
        name=name,
        url=url,
    )


def _blocked_payload(message: str, *, status: str = "waiting_confirmation") -> str:
    return json.dumps({"success": False, "status": status, "error": message}, ensure_ascii=False)


def guard_browser_click(ref: Any, task_id: Optional[str] = None) -> Optional[str]:
    """Return a finished blocked-result JSON for a risky click, else ``None`` to execute it."""
    try:
        from hermes_cli.assistant_permissions import (
            _durable_worker_confirmation_active,
            personal_assistant_permissions_active,
        )
    except Exception:
        return _blocked_payload("Stardust could not evaluate browser action permissions.", status="blocked")

    if not personal_assistant_permissions_active():
        return None

    first = inspect_browser_click(ref, task_id)
    if not first.requires_approval:
        return None

    approval_args = {"ref": _clean_ref(ref), "context_sha256": first.context_sha256}
    if _durable_worker_confirmation_active():
        try:
            from tools.background_task_approval import authorize_or_block_current_worker

            result = authorize_or_block_current_worker(
                "browser_click",
                approval_args,
                reason=first.reason,
                rule_key=first.rule_key,
            )
        except Exception:
            return _blocked_payload(
                "Stardust could not persist the required browser approval. The click was not executed.",
                status="blocked",
            )
        if not result.allowed:
            return _blocked_payload(result.message)
    else:
        try:
            from tools.approval import request_tool_approval

            decision = request_tool_approval("browser_click", first.reason, rule_key=first.rule_key)
        except Exception:
            return _blocked_payload(
                "Stardust could not request approval for this browser action. The click was not executed.",
                status="blocked",
            )
        if not decision.get("approved"):
            message = decision.get("user_summary") or decision.get("message") or "The browser action was not approved."
            return _blocked_payload(str(message), status=str(decision.get("status") or "blocked"))

    # Interactive approvals may wait for minutes, and durable grants may resume in a new worker.
    # Re-snapshot immediately before the click. Any semantic drift invalidates the approval.
    second = inspect_browser_click(ref, task_id)
    if (
        not second.requires_approval
        or not second.context_sha256
        or second.context_sha256 != first.context_sha256
    ):
        return _blocked_payload(
            "BLOCKED: the page or target element changed after approval. Refresh the snapshot and retry so consent applies to the current control.",
            status="blocked",
        )
    return None


__all__ = ["BrowserClickRisk", "inspect_browser_click", "guard_browser_click"]
