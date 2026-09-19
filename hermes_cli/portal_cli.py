"""Legacy ``hermes portal`` compatibility surface.

Stardust does not own or operate the Nous Portal account system.  The parser is
kept temporarily so existing scripts fail with a clear migration message rather
than an argparse "unknown command" error, but no user-reachable portal command
may authenticate to Nous, open Nous-owned pages, or change provider/tool routing.

The read-only helpers below are retained temporarily for internal compatibility
coverage while the wider Nous auth implementation is separated from Stardust.
They are deliberately not wired into ``portal_command``.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser

from hermes_cli.colors import Colors, color
from hermes_cli.config import load_config

DEFAULT_PORTAL_URL = "https://portal.nousresearch.com"
SUBSCRIPTION_URL = "https://portal.nousresearch.com/manage-subscription"
DOCS_URL = "https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway"
# Static legacy catalog retained only for the read-only compatibility helper.
_CATALOG = [
    ("web", "Web search & extract", "Firecrawl"),
    ("image_gen", "Image generation", "FAL"),
    ("tts", "Text-to-speech", "OpenAI TTS"),
    ("browser", "Browser automation", "Browser Use"),
    ("modal", "Cloud terminal", "Modal"),
]


def _feature_state(feat, *, via_nous: str) -> str:
    """Routing column for the legacy read-only status helpers."""
    if feat.managed_by_nous:
        return color(via_nous, Colors.GREEN)
    if feat.active:
        return feat.current_provider or "active"
    return color("not configured", Colors.DIM)


def _heading(title: str) -> None:
    print()
    print(color(f"  {title}", Colors.MAGENTA))
    print(color("  " + "─" * len(title), Colors.MAGENTA))


def _cmd_status(args) -> int:
    """Legacy read-only Portal auth + Tool Gateway summary.

    Not registered by Stardust's ``portal`` parser.  Kept temporarily for
    compatibility tests while the upstream account implementation is retired.
    """
    from hermes_cli.auth import get_nous_auth_status_local
    from hermes_cli.nous_subscription import get_nous_subscription_features

    config = load_config() or {}
    try:
        auth = get_nous_auth_status_local() or {}
    except Exception:
        auth = {}
    logged_in = bool(auth.get("logged_in"))
    free_tier = bool(auth.get("free_tier"))
    _heading("Nous Portal")
    if free_tier:
        from hermes_cli.anon_auth import FREE_TIER_LABEL, GUEST_MODEL, UPGRADE_HINT
        print(f"  Auth:    {color(f'{FREE_TIER_LABEL} · {GUEST_MODEL}', Colors.GREEN)}")
        print(f"           {UPGRADE_HINT}")
        if auth.get("inference_base_url"):
            print(f"  API:     {auth['inference_base_url']}")
    elif logged_in:
        print(f"  Auth:    {color('✓ logged in', Colors.GREEN)}")
        print(f"  Portal:  {auth.get('portal_base_url') or DEFAULT_PORTAL_URL}")
        if auth.get("inference_base_url"):
            print(f"  API:     {auth['inference_base_url']}")
    else:
        print(f"  Auth:    {color('not logged in', Colors.YELLOW)}")
        print(f"  Sign up: {SUBSCRIPTION_URL}")
        print("  Login:   legacy Nous integration only")

    model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
    provider = str(model_cfg.get("provider") or "").strip().lower()
    if provider == "nous":
        print(f"  Model:   {color('✓ using Nous as inference provider', Colors.GREEN)}")
    elif provider:
        print(f"  Model:   currently {provider} (switch with `hermes model`)")

    _heading("Tool Gateway")
    try:
        features = get_nous_subscription_features(config)
    except Exception:
        print("  (could not resolve subscription state)")
        return 0
    rows = [(feat.label, _feature_state(feat, via_nous="via Nous Portal")) for feat in features.items()]
    width = max((len(r[0]) for r in rows), default=0)
    for label, state in rows:
        print(f"  {label:<{width}}   {state}")
    if not logged_in:
        print()
        print(color(f"  Docs: {DOCS_URL}", Colors.DIM))
    return 0


def _cmd_open(args) -> int:
    """Legacy helper; not registered by Stardust."""
    print(f"Opening {SUBSCRIPTION_URL}")
    try:
        opened = webbrowser.open(SUBSCRIPTION_URL)
    except Exception:
        opened = False
    if opened:
        return 0
    print()
    print("Could not launch a browser. Visit the URL above manually.")
    return 1


def _cmd_tools(args) -> int:
    """Legacy helper; not registered by Stardust."""
    from hermes_cli.nous_subscription import get_nous_subscription_features

    config = load_config() or {}
    try:
        features = get_nous_subscription_features(config)
    except Exception:
        print("Could not resolve Tool Gateway state.", file=sys.stderr)
        return 1

    _heading("Tool Gateway catalog")
    if not features.nous_auth_present:
        print(color("  Legacy Nous Portal integration is not signed in.", Colors.YELLOW))
        print()

    label_width = max(len(label) for _, label, _ in _CATALOG)
    for key, label, partner in _CATALOG:
        feat = features.features.get(key)
        state = color("unknown", Colors.DIM) if feat is None else _feature_state(feat, via_nous="✓ via Nous Portal")
        print(f"  {label:<{label_width}}  partner: {partner:<14} {state}")

    print()
    print(color(f"  Legacy subscription URL: {SUBSCRIPTION_URL}", Colors.DIM))
    print(color(f"  Legacy docs: {DOCS_URL}", Colors.DIM))
    return 0


def _cmd_login(args) -> int:
    """Legacy helper; not registered by Stardust."""
    from hermes_cli.setup import _run_portal_one_shot

    config = load_config() or {}
    try:
        _run_portal_one_shot(config)
    except (KeyboardInterrupt, EOFError):
        print()
        print("Portal setup cancelled.")
        return 1
    return 0


_RETIRED_MESSAGE = (
    "Nous Portal is not a built-in Stardust account service. "
    "This legacy command is retained only for compatibility and performs no login, "
    "subscription, browser, provider, or Tool Gateway action. "
    "Configure model providers with `hermes auth` and `hermes model`; "
    "generic remote gateway credentials remain supported."
)


def portal_command(args) -> int:
    """Fail closed for every legacy ``hermes portal`` invocation."""
    print(_RETIRED_MESSAGE, file=sys.stderr)
    return 1


def add_parser(subparsers) -> None:
    """Register a fail-closed compatibility parser for old ``hermes portal`` scripts."""
    portal_parser = subparsers.add_parser(
        "portal",
        help=argparse.SUPPRESS,
        description=(
            "Legacy Nous Portal compatibility command. The built-in Nous account "
            "integration is retired in Stardust and no action is performed."
        ),
    )
    portal_sub = portal_parser.add_subparsers(dest="portal_command")

    # Parse historical forms so old scripts receive the explicit retirement message
    # instead of accidentally falling through to another command. None of these
    # subparsers dispatches to the legacy helper functions above.
    for name in ("login", "info", "status", "open", "tools"):
        legacy = portal_sub.add_parser(name, help=argparse.SUPPRESS)
        legacy.set_defaults(func=portal_command)

    portal_parser.set_defaults(func=portal_command)
