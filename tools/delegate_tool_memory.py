"""Read-only parent personal context for same-boundary delegated children."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from tools.delegate_tool_config import _inherit_parent_base_url

logger = logging.getLogger("tools.delegate_tool")

_CHILD_MEMORY_SNAPSHOT_MAX_CHARS = 8_000
_ROUTE_ATTR_DEFAULTS = (
    ("providers_allowed", None),
    ("providers_ignored", None),
    ("providers_order", None),
    ("provider_sort", None),
    ("provider_require_parameters", False),
    ("provider_data_collection", ""),
    ("openrouter_min_coding_score", None),
)


def _normalized_endpoint(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _parent_api_key(parent_agent) -> Any:
    key = getattr(parent_agent, "api_key", None)
    client_kwargs = getattr(parent_agent, "_client_kwargs", None)
    if not key and isinstance(client_kwargs, dict):
        key = client_kwargs.get("api_key")
    return key


def _credential_pool_boundary_matches(parent_agent, child_credential_pool: Any) -> bool:
    """Require exact pool identity and one credential whenever a pool participates."""
    parent_pool = getattr(parent_agent, "_credential_pool", None)
    if parent_pool is None and child_credential_pool is None:
        return True
    if parent_pool is None or child_credential_pool is not parent_pool:
        return False
    entries_fn = getattr(parent_pool, "entries", None)
    if not callable(entries_fn):
        return False
    try:
        return len(entries_fn()) == 1
    except Exception:
        logger.debug("subagent: could not verify credential-pool privacy boundary", exc_info=True)
        return False


def _same_inference_privacy_boundary(
    parent_agent,
    child_runtime: Dict[str, Any],
    child_request_overrides: Optional[Dict[str, Any]] = None,
    child_credential_pool: Any = None,
) -> bool:
    """True only when personal context stays on the parent's inference/privacy route."""
    parent_provider = str(getattr(parent_agent, "provider", "") or "").strip().lower()
    child_provider = str(child_runtime.get("provider") or "").strip().lower()
    if parent_provider != child_provider:
        return False

    parent_endpoint = _inherit_parent_base_url(parent_agent, getattr(parent_agent, "base_url", None))
    if _normalized_endpoint(parent_endpoint) != _normalized_endpoint(child_runtime.get("base_url")):
        return False

    if str(getattr(parent_agent, "model", "") or "").strip() != str(child_runtime.get("model") or "").strip():
        return False

    parent_api_mode = str(getattr(parent_agent, "api_mode", "") or "").strip().lower()
    child_api_mode = str(child_runtime.get("api_mode") or "").strip().lower()
    if parent_api_mode != child_api_mode:
        return False

    if _parent_api_key(parent_agent) != child_runtime.get("api_key"):
        return False
    if not _credential_pool_boundary_matches(parent_agent, child_credential_pool):
        return False

    parent_fallback = getattr(parent_agent, "_fallback_chain", None)
    if not isinstance(parent_fallback, list):
        parent_fallback = None
    child_fallback = child_runtime.get("fallback_model")
    if not isinstance(child_fallback, list):
        child_fallback = None
    if parent_fallback != child_fallback:
        return False

    parent_request_overrides = dict(getattr(parent_agent, "request_overrides", {}) or {})
    if parent_request_overrides != dict(child_request_overrides or {}):
        return False

    for attr, default in _ROUTE_ATTR_DEFAULTS:
        parent_value = getattr(parent_agent, attr, default)
        child_value = child_runtime.get(attr, parent_value)
        if parent_value != child_value:
            return False

    parent_command = str(getattr(parent_agent, "acp_command", "") or "").strip()
    child_command = str(child_runtime.get("acp_command") or "").strip()
    if parent_command != child_command:
        return False
    if list(getattr(parent_agent, "acp_args", []) or []) != list(child_runtime.get("acp_args") or []):
        return False
    return True


def _read_only_parent_memory_snapshot(
    parent_agent,
    child_runtime: Dict[str, Any],
    child_request_overrides: Optional[Dict[str, Any]] = None,
    child_credential_pool: Any = None,
) -> Optional[str]:
    """Return the parent's already-loaded builtin memory prompt as bounded read-only context."""
    if not getattr(parent_agent, "_memory_persistence_enabled", True):
        return None
    if not _same_inference_privacy_boundary(
        parent_agent,
        child_runtime,
        child_request_overrides=child_request_overrides,
        child_credential_pool=child_credential_pool,
    ):
        return None

    store = getattr(parent_agent, "_memory_store", None)
    formatter = getattr(store, "format_for_system_prompt", None)
    if not callable(formatter):
        return None

    targets = []
    if getattr(parent_agent, "_user_profile_enabled", True):
        targets.append("user")
    if getattr(parent_agent, "_memory_enabled", True):
        targets.append("memory")

    blocks = []
    for target in targets:
        try:
            block = formatter(target)
        except Exception:
            logger.debug("subagent: failed to read parent %s snapshot", target, exc_info=True)
            continue
        if isinstance(block, str) and block.strip():
            blocks.append(block.strip())
    if not blocks:
        return None

    snapshot = "\n\n".join(blocks)
    if len(snapshot) > _CHILD_MEMORY_SNAPSHOT_MAX_CHARS:
        snapshot = (
            snapshot[:_CHILD_MEMORY_SNAPSHOT_MAX_CHARS].rstrip()
            + "\n[... parent memory snapshot truncated ...]"
        )
    return snapshot
