"""``hermes project`` CLI — manage first-class, multi-folder Projects."""

from __future__ import annotations

import argparse
import functools
import sys

from hermes_cli import projects_db as pdb


def build_parser(parent_subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Attach the ``project`` subcommand tree. Returns the top parser."""
    parser = parent_subparsers.add_parser(
        "project",
        help="Manage projects (named, multi-folder workspaces)",
        description=(
            "Projects are human-named workspaces that can span multiple "
            "folders / repos. They anchor desktop session grouping and, when "
            "bound to a kanban board, give tasks a deterministic worktree + "
            "branch convention. State is per-profile."
        ),
    )
    sub = parser.add_subparsers(dest="project_action")
    p_create = sub.add_parser("create", help="Create a new project")
    p_create.add_argument("name", help="Human name, e.g. 'Hermes Agent'")
    p_create.add_argument("folders", nargs="*", help="Folder paths to include (first = primary)")
    p_create.add_argument("--slug", default=None, help="Explicit slug override")
    p_create.add_argument("--primary", default=None, metavar="PATH", help="Primary repo path")
    for opt in ("--description", "--icon", "--color"):
        p_create.add_argument(opt, default=None)
    p_create.add_argument("--board", default=None, metavar="SLUG", help="Bind a kanban board")
    p_create.add_argument("--use", action="store_true", help="Set as the active project")
    p_list = sub.add_parser("list", aliases=["ls"], help="List projects")
    p_list.add_argument("--all", action="store_true", dest="include_archived", help="Include archived projects")

    def project_sub(name: str, help: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help)
        sp.add_argument("project", help="Project id or slug")
        return sp

    project_sub("show", "Show a project's details")
    p_add = project_sub("add-folder", "Add a folder to a project")
    p_add.add_argument("path", help="Folder path")
    p_add.add_argument("--label", default=None)
    p_add.add_argument("--primary", action="store_true", help="Mark as primary repo")
    project_sub("remove-folder", "Remove a folder from a project").add_argument("path", help="Folder path")
    project_sub("rename", "Rename a project").add_argument("name", help="New name")
    project_sub("set-primary", "Set the primary folder").add_argument("path", help="Folder path (must already be in project)")
    p_use = sub.add_parser("use", help="Set the active project")
    p_use.add_argument("project", nargs="?", default=None, help="Project id or slug (omit to clear)")
    project_sub("archive", "Archive a project")
    project_sub("restore", "Restore an archived project")
    project_sub("bind-board", "Bind a kanban board to a project").add_argument(
        "board", nargs="?", default="", help="Board slug (omit to unbind)"
    )

    p_facts = project_sub("facts", "Manage durable facts for a project")
    fact_sub = p_facts.add_subparsers(dest="fact_action")
    fact_list = fact_sub.add_parser("list", help="List active project facts")
    fact_list.add_argument("--all", action="store_true", dest="include_superseded", help="Include superseded facts")
    fact_add = fact_sub.add_parser("add", help="Add a project fact with provenance")
    fact_add.add_argument("content", help="Durable project-scoped fact")
    fact_add.add_argument(
        "--source", dest="source_kind", required=True,
        choices=sorted(pdb.PROJECT_FACT_SOURCE_KINDS),
        help="Where this fact came from",
    )
    fact_add.add_argument("--source-ref", default=None, help="Optional source locator or identifier")
    fact_add.add_argument("--confidence", type=float, default=1.0)
    fact_add.add_argument("--sensitive", action="store_true", help="Store but never auto-inject into model context")
    fact_verify = fact_sub.add_parser("verify", help="Mark one fact verified and certain")
    fact_verify.add_argument("fact_id")
    fact_supersede = fact_sub.add_parser("supersede", help="Retire an obsolete fact without deleting history")
    fact_supersede.add_argument("fact_id")
    parser.set_defaults(_project_parser=parser)
    return parser


def projects_command(args: argparse.Namespace) -> int:
    """Entry point from ``hermes project …`` argparse dispatch."""
    action = getattr(args, "project_action", None)
    if not action:
        parser = getattr(args, "_project_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print("usage: hermes project <action> [options]\nRun 'hermes project --help' for the full list.", file=sys.stderr)
        return 0
    handler = _HANDLERS.get(action)
    if handler is None:
        print(f"Unknown project action: {action}", file=sys.stderr)
        return 1
    return handler(args)


def _err(message: str) -> int:
    print(f"project: {message}", file=sys.stderr)
    return 1


def _resolve(conn, ident: str):
    proj = pdb.get_project(conn, ident)
    if proj is None:
        _err(f"no such project: {ident}")
    return proj


def _db_command(fn):
    """Open the DB and run ``fn(args, conn)``; a ``str`` result is printed (rc 0), an ``int`` is the rc;
    a ``ValueError`` prints ``project: …`` and exits 2."""

    @functools.wraps(fn)
    def wrapper(args: argparse.Namespace) -> int:
        try:
            with pdb.connect_closing() as conn:
                out = fn(args, conn)
        except ValueError as exc:
            print(f"project: {exc}", file=sys.stderr)
            return 2
        if isinstance(out, str):
            print(out)
            return 0
        return out

    return wrapper


def _with_project(fn):
    """Like ``_db_command`` but also resolves ``args.project`` into ``fn(args, conn, proj)``."""

    @functools.wraps(fn)
    def wrapper(args: argparse.Namespace, conn):
        proj = _resolve(conn, args.project)
        return 1 if proj is None else fn(args, conn, proj)

    return _db_command(wrapper)


def _print_project(proj) -> None:
    print(f"{proj.slug}  [{proj.id}]{' (archived)' if proj.archived else ''}")
    print(f"  name:    {proj.name}")
    for label, value in (("about", proj.description), ("board", proj.board_slug), ("primary", proj.primary_path)):
        if value:
            print(f"  {label}:{' ' * (8 - len(label))}{value}")
    if proj.folders:
        print("  folders:")
        for f in proj.folders:
            print(f"   {' *' if f.is_primary else '  '} {f.path}{f' ({f.label})' if f.label else ''}")


@_db_command
def _cmd_create(args, conn) -> int:
    pid = pdb.create_project(
        conn, name=args.name, slug=args.slug, folders=args.folders, primary_path=args.primary,
        description=args.description, icon=args.icon, color=args.color, board_slug=args.board,
    )
    if args.use:
        pdb.set_active(conn, pid)
    proj = pdb.get_project(conn, pid)
    if proj is None:
        print("project: vanished after create", file=sys.stderr)
        return 2
    print(f"Created project {proj.slug} ({pid})")
    _print_project(proj)
    return 0


@_db_command
def _cmd_list(args, conn):
    active = pdb.get_active_id(conn)
    projs = pdb.list_projects(conn, include_archived=getattr(args, "include_archived", False))
    if not projs:
        return "No projects yet. Create one with `hermes project create <name>`."
    for p in projs:
        flags = " (archived)" if p.archived else ""
        print(f"{'*' if p.id == active else ' '} {p.slug:<24} {p.name}{flags}  [{len(p.folders)} folder(s)]")
    return 0


@_with_project
def _cmd_show(args, conn, proj) -> int:
    _print_project(proj)
    return 0


@_with_project
def _cmd_add_folder(args, conn, proj) -> str:
    path = pdb.add_folder(conn, proj.id, args.path, label=args.label, is_primary=args.primary)
    return f"Added {path} to {proj.slug}"


@_with_project
def _cmd_remove_folder(args, conn, proj):
    if not pdb.remove_folder(conn, proj.id, args.path):
        return _err(f"folder not in project: {args.path}")
    return f"Removed {args.path} from {proj.slug}"


@_with_project
def _cmd_rename(args, conn, proj) -> str:
    pdb.update_project(conn, proj.id, name=args.name)
    return f"Renamed {proj.slug} -> {args.name}"


@_with_project
def _cmd_set_primary(args, conn, proj):
    if not pdb.set_primary(conn, proj.id, args.path):
        return _err(f"'{args.path}' is not a folder of {proj.slug}; add it first with `hermes project add-folder`.")
    return f"Set primary of {proj.slug} -> {args.path}"


@_db_command
def _cmd_use(args, conn):
    if not args.project:
        pdb.set_active(conn, None)
        return "Cleared active project"
    proj = _resolve(conn, args.project)
    if proj is None:
        return 1
    pdb.set_active(conn, proj.id)
    return f"Active project: {proj.slug}"


@_with_project
def _cmd_facts(args, conn, proj):
    action = getattr(args, "fact_action", None)
    if not action:
        return _err("choose a facts action: list, add, verify, supersede")
    if action == "list":
        facts = pdb.list_project_facts(
            conn, proj.id, include_superseded=bool(getattr(args, "include_superseded", False))
        )
        if not facts:
            return "No project facts."
        for fact in facts:
            state = "superseded" if fact.superseded_at is not None else "active"
            verified = " verified" if fact.verified_at is not None else ""
            sensitive = " sensitive" if fact.sensitive else ""
            print(
                f"{fact.id}  [{fact.source_kind} {fact.confidence:.2f}; {state}{verified}{sensitive}]  "
                f"{fact.content}"
            )
        return 0
    if action == "add":
        fact_id = pdb.add_project_fact(
            conn, proj.id, args.content, source_kind=args.source_kind,
            source_ref=args.source_ref, confidence=args.confidence, sensitive=args.sensitive,
        )
        return f"Added project fact {fact_id} to {proj.slug}"
    if action == "verify":
        if not pdb.verify_project_fact(conn, args.fact_id):
            return _err(f"no active project fact: {args.fact_id}")
        return f"Verified project fact {args.fact_id}"
    if action == "supersede":
        if not pdb.supersede_project_fact(conn, args.fact_id):
            return _err(f"no active project fact: {args.fact_id}")
        return f"Superseded project fact {args.fact_id}"
    return _err(f"unknown facts action: {action}")


def _flag_command(op: str, verb: str):
    """Handler for ``pdb.<op>(conn, proj.id)`` followed by ``"<verb> <slug>"``."""
    return _with_project(lambda args, conn, proj: (getattr(pdb, op)(conn, proj.id), f"{verb} {proj.slug}")[1])


@_with_project
def _cmd_bind_board(args, conn, proj) -> str:
    pdb.update_project(conn, proj.id, board_slug=args.board)
    if not args.board.strip():
        return f"Unbound board from {proj.slug}"
    if proj.primary_path:  # best-effort: point the bound board's default_workdir at the primary repo
        try:
            from hermes_cli import kanban_db as kb

            slug = kb._normalize_board_slug(args.board)
            if slug and (slug == kb.DEFAULT_BOARD or kb.board_exists(slug)):
                kb.write_board_metadata(slug, default_workdir=proj.primary_path)
        except Exception:
            pass
    return f"Bound {proj.slug} -> board {args.board}"


_HANDLERS = {
    "create": _cmd_create,
    "list": _cmd_list,
    "ls": _cmd_list,
    "show": _cmd_show,
    "add-folder": _cmd_add_folder,
    "remove-folder": _cmd_remove_folder,
    "rename": _cmd_rename,
    "set-primary": _cmd_set_primary,
    "use": _cmd_use,
    "archive": _flag_command("archive_project", "Archived"),
    "restore": _flag_command("restore_project", "Restored"),
    "bind-board": _cmd_bind_board,
    "facts": _cmd_facts,
}
