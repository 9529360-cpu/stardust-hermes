"""Agent-facing subcommand handlers: memory, acp, tools, insights, monitoring, skills (+trust).

Split out of ``hermes_cli/main.py``. Names that still live in main (``PROJECT_ROOT``, ...)
are imported lazily inside the functions that use them (avoids an import cycle).
"""

import sys


def _set_memory_master(enabled: bool) -> None:
    """Toggle the master memory privacy switch without erasing provider config."""
    from hermes_cli.config import load_config, save_config
    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}
    config["memory"]["enabled"] = bool(enabled)
    save_config(config)
    state = "enabled" if enabled else "paused"
    print(f"\n  ✓ Memory persistence: {state}")
    if not enabled and config["memory"].get("provider"):
        print(f"  External provider '{config['memory']['provider']}' remains configured.")
    print("  Chat/session history is unchanged.")
    print("  Saved to config.yaml\n")


def _cmd_memory_off():
    _set_memory_master(False)


def _cmd_memory_on():
    _set_memory_master(True)


def _cmd_memory_reset(args):
    from hermes_constants import display_hermes_home
    from tools.memory_tool import MemoryStore

    target = getattr(args, "target", "all")
    targets = []
    if target in {"all", "memory"}:
        targets.append(("memory", "MEMORY.md", "agent notes"))
    if target in {"all", "user"}:
        targets.append(("user", "USER.md", "user profile"))

    existing = []
    for key, fname, desc in targets:
        path = MemoryStore._path_for(key)
        if path.exists() or path.is_symlink():
            existing.append((key, fname, desc, path))

    if existing:
        print("\n  This will permanently erase the following built-in memory files:")
        for _key, fname, desc, path in existing:
            try:
                size = path.stat().st_size
                size_text = f" — {size:,} bytes"
            except OSError:
                size_text = ""
            print(f"    ◆ {fname} ({desc}){size_text}")
    else:
        print(
            "\n  No built-in memory files are currently present. "
            "Reset will still advance the forget generation so stale sessions "
            "and pre-reset staged writes cannot restore old memory."
        )

    if not getattr(args, "yes", False):
        try:
            answer = input("\n  Type 'yes' to confirm: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.\n")
            return
        if answer != "yes":
            print("  Cancelled.\n")
            return

    completed = []
    for key, fname, desc in targets:
        try:
            existed = MemoryStore.reset_target(key)
        except (OSError, RuntimeError) as exc:
            suffix = f" Already reset: {', '.join(completed)}." if completed else ""
            print(f"\n  ✗ Could not reset {fname}: {exc}.{suffix}\n", file=sys.stderr)
            raise SystemExit(1) from exc
        completed.append(fname)
        if existed:
            print(f"  ✓ Deleted {fname} ({desc})")
        else:
            print(f"  ✓ Advanced forget boundary for {fname} ({desc}); no file bytes were present")

    print(
        "\n  Memory reset complete. Active sessions drop the reset built-in memory "
        "on their next turn; pre-reset staged writes cannot restore it."
    )
    print(f"  Files were in: {display_hermes_home()}/memories/\n")


def cmd_memory(args):
    sub = getattr(args, "memory_command", None)
    if sub == "off":
        _cmd_memory_off()
    elif sub == "on":
        _cmd_memory_on()
    elif sub == "reset":
        _cmd_memory_reset(args)
    else:
        from hermes_cli.memory_setup import memory_command
        memory_command(args)


# (args attribute, acp flag) — forwarded in this order.
_ACP_FLAGS = (
    ("acp_version", "--version"),
    ("check", "--check"),
    ("setup", "--setup"),
    ("setup_browser", "--setup-browser"),
    ("assume_yes", "--yes"))


def cmd_acp(args):
    """Launch Hermes Agent as an ACP server."""
    try:
        from acp_adapter.entry import main as acp_main
        acp_main([flag for attr, flag in _ACP_FLAGS if getattr(args, attr, False)])
    except ImportError as e:
        from hermes_cli.main_dep_hints import missing_optional_deps_message

        print(missing_optional_deps_message("ACP server", "its protocol packages", "acp"), file=sys.stderr)
        print(f"Details: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tools(args):
    from hermes_cli.main import _require_tty
    action = getattr(args, "tools_action", None)
    if action in {"list", "disable", "enable"}:
        from hermes_cli.tools_config import tools_disable_enable_command
        tools_disable_enable_command(args)
    elif action == "post-setup":
        from hermes_cli.tools_config import run_post_setup_command
        sys.exit(run_post_setup_command(args))
    else:
        _require_tty("tools")
        from hermes_cli.tools_config import tools_command
        tools_command(args)


def cmd_insights(args):
    db = None
    try:
        from hermes_state import SessionDB, _default_db_path
        from agent.insights import InsightsEngine
        if not _default_db_path().exists():
            print("No session data yet.")
            return
        db = SessionDB(read_only=True)
        engine = InsightsEngine(db)
        report = engine.generate(days=args.days, source=args.source)
        print(engine.format_terminal(report))
    except Exception as e:
        print(f"Error generating insights: {e}")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _dict_or_empty(value) -> dict:
    return value if isinstance(value, dict) else {}


def cmd_monitoring(args):
    """Gateway monitoring status: health & diagnostics export posture."""
    from hermes_cli.config import load_config
    action = getattr(args, "monitoring_action", None) or "status"
    mon = _dict_or_empty(load_config().get("monitoring"))

    if action == "status":
        from agent.monitoring import otlp_exporter
        gh = _dict_or_empty(mon.get("gateway_health_export"))
        otlp = _dict_or_empty(_dict_or_empty(mon.get("export")).get("otlp"))

        print("Gateway monitoring")
        print(f"  Health export:  {'enabled' if gh.get('enabled') else 'disabled'} "
              f"(monitoring.gateway_health_export.enabled)")
        if gh.get("enabled"):
            print(f"    Metrics:            {'on' if gh.get('metrics_enabled', True) else 'off'} "
                  f"(interval {gh.get('export_interval_seconds', 60)}s)")
            print(f"    Diagnostic events:  {'on' if gh.get('diagnostic_events_enabled', True) else 'off'}")
            print(f"    Warning/error logs: {'on' if gh.get('warning_error_events_enabled', True) else 'off'} "
                  f"(interval {gh.get('logs_export_interval_seconds', 5)}s)")
            print("    Content safety:     always on "
                  "(rendered messages are never exported; not configurable)")
        endpoint = otlp.get("endpoint") or ""
        if otlp.get("enabled") and endpoint:
            print(f"  OTLP endpoint:  {endpoint}")
        else:
            print("  OTLP endpoint:  not configured (monitoring.export.otlp)")
        print(f"  OTel SDK:       {'installed' if otlp_exporter.is_available() else 'not installed'} "
              f"(optional extra: hermes-agent[otlp])")
        print("\n  Scope: gateway service health + redacted diagnostics only.")
        print("  No prompts, messages, tool args/results, usage analytics, or traces.")
        return

    print(f"Unknown monitoring action: {action}", file=sys.stderr)
    sys.exit(2)


def cmd_skills(args):
    from hermes_cli.main import _require_tty
    action = getattr(args, "skills_action", None)
    if action == "config":
        _require_tty("skills config")
        from hermes_cli.skills_config import skills_command as skills_config_command
        skills_config_command(args)
    elif action in ("trust", "untrust"):
        _cmd_skills_trust(args)
    else:
        from hermes_cli.skills_hub import skills_command
        skills_command(args)


def _cmd_skills_trust(args):
    """``hermes skills trust|untrust [path]`` — manage ``skills.trusted_project_dirs``.

    With no path, operates on the project root enclosing the current directory
    (nearest ancestor with ``.git``).
    """
    from pathlib import Path
    from agent.skill_utils import (
        PROJECT_SKILLS_SUBDIRS,
        _candidate_project_skills_dirs,
        find_project_root,
        iter_skill_index_files)
    from hermes_cli.config import load_config, save_config
    action = args.skills_action
    raw_path = getattr(args, "path", None)
    if raw_path:
        root = Path(raw_path).expanduser().resolve()
        if not root.is_dir():
            print(f"Not a directory: {root}")
            return
    else:
        root = find_project_root()
        if root is None:
            print(
                "Not inside a git checkout. Run from a project directory or "
                "pass the project root path explicitly.")
            return

    config = load_config()
    skills_cfg = config.setdefault("skills", {})
    trusted = skills_cfg.get("trusted_project_dirs") or []
    if not isinstance(trusted, list):
        trusted = [trusted]
    trusted = [str(t) for t in trusted]
    root_str = str(root)

    def _same(t: str) -> bool:
        return str(Path(t).expanduser().resolve()) == root_str

    if action == "untrust":
        kept = [t for t in trusted if not _same(t)]
        if len(kept) == len(trusted):
            print(f"{root} was not trusted.")
            return
        skills_cfg["trusted_project_dirs"] = kept
        save_config(config)
        print(f"Untrusted: {root}")
        print("Project skills from this repo will no longer load.")
        return

    if any(_same(t) for t in trusted):
        print(f"Already trusted: {root}")
    else:
        trusted.append(root_str)
        skills_cfg["trusted_project_dirs"] = trusted
        save_config(config)
        print(f"Trusted: {root}")

    # Show what this unlocks
    count = sum(
        sum(1 for _ in iter_skill_index_files(d, "SKILL.md"))
        for d in _candidate_project_skills_dirs(root))
    if count:
        print(
            f"{count} project skill(s) will load in sessions started inside "
            "this repo (they take precedence over same-named profile skills).")
    else:
        subdirs = " or ".join(PROJECT_SKILLS_SUBDIRS)
        print(f"No project skills found yet — add them under {subdirs}.")
