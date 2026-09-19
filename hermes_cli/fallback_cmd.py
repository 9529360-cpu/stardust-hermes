"""hermes fallback — manage the fallback provider chain and its persistent route health.

Subcommands: ``list`` (default), ``add`` (same picker as `hermes model`), ``remove``,
``clear``, ``health`` and ``reset-health``.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from hermes_cli.fallback_config import get_fallback_chain

# Normalized fallback chain (merges legacy ``fallback_model``); always a fresh copy.
_read_chain = get_fallback_chain


_MISSING_ACTIVE_PROVIDER = object()


def _identity(entry: Dict[str, Any]):
    """BackendIdentity for a ``{provider, model, base_url?}`` entry."""
    from agent.backend_identity import BackendIdentity
    return BackendIdentity.build(provider=entry.get("provider"), model=entry.get("model"), base_url=entry.get("base_url"))


def _write_chain(config: Dict[str, Any], chain: List[Dict[str, Any]]) -> None:
    """Persist the chain to ``fallback_providers``; drop the legacy key so there is one source of truth."""
    config["fallback_providers"] = chain
    config.pop("fallback_model", None)


def _format_entry(entry: Dict[str, Any]) -> str:
    """One-line human-readable rendering of a fallback entry."""
    base = entry.get("base_url")
    return f"{entry.get('model', '?')}  (via {entry.get('provider', '?')}){f'  [{base}]' if base else ''}"


def _extract_fallback_from_model_cfg(model_cfg: Any) -> Optional[Dict[str, Any]]:
    """Pull the ``{provider, model, base_url?, api_mode?}`` dict from a ``config["model"]`` snapshot."""
    if not isinstance(model_cfg, dict):
        return None
    provider = (model_cfg.get("provider") or "").strip()
    model = (model_cfg.get("default") or model_cfg.get("model") or "").strip()  # the picker writes ``model.default``
    if not provider or not model:
        return None
    entry: Dict[str, Any] = {"provider": provider, "model": model}
    entry.update({key: value for key in ("base_url", "api_mode") if (value := (model_cfg.get(key) or "").strip())})
    return entry


def _snapshot_auth_active_provider() -> Any:
    """Return the current ``active_provider`` in auth.json."""
    from hermes_cli.auth import _auth_store_lock, _load_auth_store

    with _auth_store_lock():
        store = _load_auth_store()
        return store.get("active_provider", _MISSING_ACTIVE_PROVIDER)


def _restore_auth_active_provider(value: Any) -> None:
    """Write back a previously snapshotted ``active_provider`` value."""
    from hermes_cli.auth import _auth_store_lock, _load_auth_store, _save_auth_store

    with _auth_store_lock():
        store = _load_auth_store()
        if value is _MISSING_ACTIVE_PROVIDER:
            store.pop("active_provider", None)
        else:
            store["active_provider"] = value
        _save_auth_store(store)


def _restore_model_cfg(model_before: Any) -> None:
    """Restore ``config["model"]`` to a previously-captured snapshot."""
    from hermes_cli.config import load_config, save_config
    cfg = load_config()
    cfg.pop("model", None)
    if model_before is not None:
        cfg["model"] = copy.deepcopy(model_before)
    save_config(cfg)


def _restore_primary_route(model_before: Any, active_provider_before: Any) -> None:
    """Attempt both halves of temporary picker-route restoration."""
    errors: list[BaseException] = []
    try:
        _restore_model_cfg(model_before)
    except BaseException as exc:
        errors.append(exc)
    try:
        _restore_auth_active_provider(active_provider_before)
    except BaseException as exc:
        errors.append(exc)
    if errors:
        details = "; ".join(str(exc) for exc in errors)
        raise RuntimeError(f"Could not fully restore the primary route: {details}") from errors[0]


def _entries(n: int) -> str:
    return f"{n} {'entry' if n == 1 else 'entries'}"


def _print_chain(heading: str, chain: List[Dict[str, Any]]) -> None:
    print(f"  {heading} ({_entries(len(chain))}):")
    print("".join(f"    {i}. {_format_entry(entry)}\n" for i, entry in enumerate(chain, 1)))


def _load_chain(empty_message: str):
    """Load config + chain; print ``empty_message`` block and return ``(config, None)`` when empty."""
    from hermes_cli.config import load_config
    config = load_config()
    chain = _read_chain(config)
    if not chain:
        print(f"\n{empty_message}\n")
    return config, chain or None


def _describe_primary(config: Dict[str, Any]) -> Optional[str]:
    """One-line description of the primary model for display purposes."""
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        provider = (model_cfg.get("provider") or "?").strip() or "?"
        model = (model_cfg.get("default") or model_cfg.get("model") or "?").strip() or "?"
        return f"{model}  (via {provider})"
    return model_cfg.strip() or None if isinstance(model_cfg, str) else None


def _health_row_key(row: Dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("provider") or ""),
        str(row.get("model") or ""),
        str(row.get("base_url") or ""),
    )


def _format_health_row(row: Dict[str, Any], *, include_endpoint: bool = False) -> str:
    """Render one normalized route-health row without changing circuit state."""
    status = str(row.get("status") or "healthy")
    retry_after = int(row.get("retry_after_seconds") or 0)
    labels = {
        "open": "BLOCKED",
        "half_open_busy": "PROBE IN PROGRESS",
        "probe_ready": "PROBE READY",
        "healthy": "HEALTHY",
    }
    text = labels.get(status, status.upper().replace("_", " "))
    if status == "open" and retry_after:
        text += f" — retry eligible in ~{retry_after}s"
    elif status == "half_open_busy" and retry_after:
        text += f" — probe lease expires in ~{retry_after}s"
    elif status == "probe_ready":
        text += " — next real request may test this route"

    details: list[str] = []
    if reason := row.get("reason"):
        details.append(f"reason={reason}")
    failures = int(row.get("consecutive_failures") or 0)
    if failures:
        details.append(f"failures={failures}")
    if include_endpoint and (endpoint := row.get("base_url")):
        details.append(f"endpoint={endpoint}")
    if details:
        text += "  (" + "; ".join(details) + ")"
    return text


def _print_route_health(label: str, entry: Dict[str, Any], *, matched: set[tuple[str, str, str]]) -> None:
    from agent import route_health

    rows = route_health.health_rows(
        str(entry.get("provider") or ""),
        str(entry.get("model") or ""),
        str(entry.get("base_url") or ""),
    )
    print(f"  {label}: {_format_entry(entry)}")
    if not rows:
        print("    ELIGIBLE — no persisted route failures recorded")
        return
    include_endpoint = len(rows) > 1 or not entry.get("base_url")
    for row in rows:
        matched.add(_health_row_key(row))
        print(f"    {_format_health_row(row, include_endpoint=include_endpoint)}")


def cmd_fallback_list(args) -> None:  # noqa: ARG001
    """Print the current fallback chain."""
    config, chain = _load_chain("  No fallback providers configured.")
    if chain is None:
        print("  Add one with:  hermes fallback add\n")
        return
    print()
    if primary := _describe_primary(config):
        print(f"  Primary:   {primary}\n")
    _print_chain("Fallback chain", chain)
    print("  Tried in order when the primary fails (rate-limit, 5xx, connection errors).")
    print("  Route circuit status: hermes fallback health\n")


def cmd_fallback_health(args) -> None:  # noqa: ARG001
    """Show persisted circuit state without claiming a recovery probe or making network calls."""
    from agent import route_health
    from hermes_cli.config import load_config

    config = load_config()
    chain = _read_chain(config)
    state_status, _ = route_health.state_file_status()
    print("\n  Persistent route health")
    print(f"  Enabled: {'yes' if route_health.enabled() else 'no'}")
    print(f"  State:   {route_health.state_path()}")
    if not route_health.enabled():
        print("\n  Persistent circuit tracking is disabled; stored state does not gate routes.\n")
        return

    primary = _extract_fallback_from_model_cfg(config.get("model"))
    if state_status == "corrupt":
        print("\n  CORRUPT — persisted route-health state cannot be trusted; routing is failing open.")
        print("  Configured routes remain eligible until a new valid health state is written.")
        print()
        if primary:
            print(f"  Primary: {_format_entry(primary)}")
        else:
            print("  Primary: not resolvable from model config")
        if chain:
            for index, entry in enumerate(chain, 1):
                print(f"  Fallback {index}: {_format_entry(entry)}")
        else:
            print("  Fallbacks: none configured")
        print("\n  Repair the advisory state with: hermes fallback reset-health\n")
        return

    matched: set[tuple[str, str, str]] = set()
    print()
    if primary:
        _print_route_health("Primary", primary, matched=matched)
    else:
        print("  Primary: not resolvable from model config")

    if chain:
        for index, entry in enumerate(chain, 1):
            _print_route_health(f"Fallback {index}", entry, matched=matched)
    else:
        print("  Fallbacks: none configured")

    remembered = route_health.health_rows()
    extra = [row for row in remembered if _health_row_key(row) not in matched]
    if extra:
        print("\n  Other remembered routes:")
        for row in extra:
            entry = {
                "provider": row.get("provider"),
                "model": row.get("model"),
                "base_url": row.get("base_url"),
            }
            print(f"    {_format_entry(entry)}")
            print(f"      {_format_health_row(row)}")

    print("\n  Inspection is read-only: it never probes a provider or claims a half-open lease.")
    print("  Clear advisory circuit history with: hermes fallback reset-health\n")


def cmd_fallback_reset_health(args) -> None:
    """Clear persisted route-health history without changing fallback configuration."""
    from agent import route_health

    state_status, stored_count = route_health.state_file_status()
    if state_status == "missing" or (state_status == "ok" and stored_count == 0):
        print("\n  No persisted route-health entries to clear.\n")
        return

    if not bool(getattr(args, "yes", False)):
        if state_status == "corrupt":
            print("\n  The persisted route-health state is corrupt or unreadable.")
            print("  This will replace it with a clean empty state for the active profile.")
        else:
            print(f"\n  This will clear {_entries(stored_count)} of route-health history for the active profile.")
        print("  Fallback providers and credentials are not changed.")
        try:
            response = input("  Clear route-health history? [y/N]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled.")
            return
        if response not in {"y", "yes"}:
            print("  Cancelled — no change.")
            return

    try:
        cleared = route_health.reset_state()
    except (OSError, TimeoutError) as exc:
        print(f"\n  Could not clear route-health state: {exc}")
        raise SystemExit(1) from exc
    if state_status == "corrupt":
        print("\n  Reset corrupt route-health state to a clean empty file.")
    else:
        print(f"\n  Cleared {_entries(cleared)} of persisted route-health history.")
    print("  The next real request may probe routes that were previously cooling down.\n")


def cmd_fallback_add(args) -> None:
    """Launch the same picker as `hermes model`, then append the selection to the chain."""
    from hermes_cli.main import _require_tty, select_provider_and_model
    from hermes_cli.config import load_config, save_config
    _require_tty("fallback add")

    # Snapshot BEFORE the picker runs; both route stores must be restored on every exit path.
    model_before = copy.deepcopy(load_config().get("model"))
    active_provider_before = _snapshot_auth_active_provider()
    print("\n  Adding a fallback provider.  The picker below is the same one used by\n"
          "  `hermes model` — select the provider + model you want as a fallback.\n")

    try:
        select_provider_and_model(args=args)
        after_cfg = load_config()
        model_after = after_cfg.get("model")
        new_entry = _extract_fallback_from_model_cfg(model_after)
    except BaseException as picker_error:
        try:
            _restore_primary_route(model_before, active_provider_before)
        except Exception as restore_error:
            picker_error.add_note(
                "Could not fully restore the primary route after fallback "
                f"selection failed: {restore_error}"
            )
        raise

    # From here onward no identity/import/append failure can strand the temporary picker route.
    _restore_primary_route(model_before, active_provider_before)

    if not new_entry:
        print("\n  No fallback added.")
        return

    from agent.backend_identity import same_deployment
    new_ident = _identity(new_entry)
    primary_entry = _extract_fallback_from_model_cfg(model_before)
    if primary_entry and same_deployment(_identity(primary_entry), new_ident):
        print(f"\n  Selected model matches the current primary ({_format_entry(new_entry)}).")
        print("  A provider cannot be a fallback for itself — no change.")
        return

    # Reload after primary restoration; picker-created providers/credentials remain.
    final_cfg = load_config()
    chain = _read_chain(final_cfg)
    if any(same_deployment(_identity(existing), new_ident) for existing in chain):
        print(f"\n  {_format_entry(new_entry)} is already in the fallback chain — skipped.")
        return
    chain.append(new_entry)
    _write_chain(final_cfg, chain)
    save_config(final_cfg)
    print(f"\n  Added fallback: {_format_entry(new_entry)}")
    print(f"  Chain is now {_entries(len(chain))} long.\n")
    print("  Run `hermes fallback list` to view, or `hermes fallback remove` to delete.")


def cmd_fallback_remove(args) -> None:  # noqa: ARG001
    """Pick an entry from the chain and remove it."""
    from hermes_cli.config import save_config
    config, chain = _load_chain("  No fallback providers configured — nothing to remove.")
    if chain is None:
        return

    # The curses menu owns its own non-TTY guard and numbered fallback; -1 means cancelled.
    from hermes_cli.setup import _curses_prompt_choice
    idx = _curses_prompt_choice("Select a fallback to remove:", [_format_entry(e) for e in chain] + ["Cancel"], 0)
    if idx is None or idx < 0 or idx >= len(chain):
        print("\n  Cancelled — no change.")
        return
    removed = chain.pop(idx)
    _write_chain(config, chain)
    save_config(config)
    print(f"\n  Removed fallback: {_format_entry(removed)}")
    print(f"  Chain is now {_entries(len(chain))} long.\n" if chain else "  Fallback chain is now empty.\n")


def cmd_fallback_clear(args) -> None:  # noqa: ARG001
    """Remove all fallback entries (with confirmation)."""
    from hermes_cli.config import save_config
    config, chain = _load_chain("  No fallback providers configured — nothing to clear.")
    if chain is None:
        return
    print()
    _print_chain("Current fallback chain", chain)
    try:
        resp = input("  Clear all entries? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return
    if resp not in {"y", "yes"}:
        print("  Cancelled — no change.")
        return
    _write_chain(config, [])
    save_config(config)
    print("\n  Fallback chain cleared.\n")


def cmd_fallback(args) -> None:
    """Top-level dispatcher for ``hermes fallback [subcommand]``."""
    sub = getattr(args, "fallback_command", None)
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        print(f"Unknown fallback subcommand: {sub}")
        print("Use one of: list, add, remove, clear, health, reset-health")
        raise SystemExit(2)
    handler(args)


_SUBCOMMANDS = {
    **dict.fromkeys((None, "", "list", "ls"), cmd_fallback_list),
    "add": cmd_fallback_add,
    **dict.fromkeys(("remove", "rm"), cmd_fallback_remove),
    "clear": cmd_fallback_clear,
    "health": cmd_fallback_health,
    "reset-health": cmd_fallback_reset_health,
}
