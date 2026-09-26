#!/usr/bin/env python3
"""Project tools — the agent's INTENTIONAL handle on first-class Projects (per-profile
``projects.db``, the desktop sidebar's named workspaces). Creating/switching is an explicit
tool call, never a side effect of ``cd``. Durable Project facts are managed through the same
capability so workspace identity and Project-scoped knowledge keep one owner. GUI-only: the `project` toolset stays off
``_HERMES_CORE_TOOLS``; the desktop/TUI gateway folds it in and wires
``set_project_workspace_callback`` so the live session's cwd and sidebar follow."""

import json
import os
from typing import Callable, Optional

from tools.registry import registry

# Set by the GUI gateway: ``(task_id, primary_path, project_name, project_id)`` re-anchors that session's
# workspace. ``None`` in CLI/messaging — the DB write still happens, nothing to move.
_workspace_callback: Optional[Callable[[str, str, str, str], None]] = None
_project_context_callback: Optional[Callable[[str], Optional[str]]] = None


def set_project_workspace_callback(fn: Optional[Callable[[str, str, str, str], None]]) -> None:
    global _workspace_callback
    _workspace_callback = fn


def set_project_context_callback(fn: Optional[Callable[[str], Optional[str]]]) -> None:
    global _project_context_callback
    _project_context_callback = fn


def _primary_path(proj) -> Optional[str]:
    if getattr(proj, "primary_path", None):
        return proj.primary_path
    for folder in proj.folders:
        if folder.is_primary:
            return folder.path
    return proj.folders[0].path if proj.folders else None


def _apply_workspace(task_id: Optional[str], path: Optional[str], name: str, project_id: str) -> None:
    cb = _workspace_callback
    if cb and task_id and project_id:
        try:
            cb(task_id, path or "", name, project_id)
        except Exception:
            pass


def _resolve(conn, token: str):
    from hermes_cli import projects_db as pdb
    token = (token or "").strip()
    if not token:
        return None
    projects = pdb.list_projects(conn, include_archived=True)
    # Exact id / slug / name first, then case-insensitive slug / name.
    for proj in projects:
        if token in (proj.id, proj.slug) or proj.name == token:
            return proj
    low = token.lower()
    for proj in projects:
        if proj.slug.lower() == low or proj.name.lower() == low:
            return proj
    return None


def _current_project(conn, task_id: Optional[str]):
    from hermes_cli import projects_db as pdb
    callback = _project_context_callback
    project_id = callback(str(task_id or "")) if callback and task_id else None
    project = pdb.get_project(conn, project_id) if project_id else None
    return None if project is None or project.archived else project


def _project_fact_action(args: dict, task_id: Optional[str]) -> str:
    from hermes_cli import projects_db as pdb
    with pdb.connect_closing() as conn:
        proj = _current_project(conn, task_id)
        if proj is None:
            return json.dumps({"success": False, "error": "This session is not attached to a Project."})
        action = str(args.get("action") or "")
        if action == "fact_list":
            all_facts = pdb.list_project_facts(conn, proj.id)
            facts = [fact.to_dict() for fact in all_facts if not fact.sensitive]
            hidden_sensitive_count = sum(1 for fact in all_facts if fact.sensitive)
            return json.dumps({
                "success": True,
                "project_id": proj.id,
                "facts": facts,
                "hidden_sensitive_count": hidden_sensitive_count,
            })
        if action == "fact_add":
            source_kind = str(args.get("source_kind") or "").strip().lower()
            if source_kind not in {"user", "repository", "session", "tool", "inference"}:
                return json.dumps({
                    "success": False,
                    "error": "source_kind must be user, repository, session, tool, or inference.",
                })
            try:
                fact_id = pdb.add_project_fact(
                    conn, proj.id, str(args.get("content") or ""), source_kind=source_kind,
                    source_ref=(str(args.get("source_ref") or "").strip() or None),
                    confidence=float(args.get("confidence", 1.0)), sensitive=bool(args.get("sensitive", False)),
                )
            except (TypeError, ValueError) as exc:
                return json.dumps({"success": False, "error": str(exc)})
            return json.dumps({"success": True, "project_id": proj.id, "fact_id": fact_id})
        if action == "fact_supersede":
            fact_id = str(args.get("fact_id") or "").strip()
            if not fact_id or not pdb.supersede_project_fact(conn, fact_id, project_id=proj.id):
                return json.dumps({"success": False, "error": "No active project fact with that id."})
            return json.dumps({"success": True, "project_id": proj.id, "fact_id": fact_id})
    return json.dumps({"success": False, "error": "unknown project fact action"})


def _activated(proj, task_id: Optional[str]) -> str:
    primary = _primary_path(proj)
    _apply_workspace(task_id, primary, proj.name, proj.id)
    return json.dumps({
        "success": True, "id": proj.id, "slug": proj.slug, "name": proj.name,
        "primary_path": primary})


def project_list(task_id: Optional[str] = None) -> str:
    from hermes_cli import projects_db as pdb
    with pdb.connect_closing() as conn:
        active = pdb.get_active_id(conn)
        projects = pdb.list_projects(conn)
    return json.dumps({
        "active_id": active,
        "projects": [
            {
                "id": p.id, "slug": p.slug, "name": p.name,
                "primary_path": _primary_path(p), "active": p.id == active}
            for p in projects]})


def project_create(name: str, path: Optional[str] = None, task_id: Optional[str] = None) -> str:
    name = (name or "").strip()
    if not name:
        return json.dumps({"success": False, "error": "name is required"})
    from hermes_cli import projects_db as pdb
    folder = (path or "").strip()
    if folder:
        folder = os.path.abspath(os.path.expanduser(folder))
    try:
        with pdb.connect_closing() as conn:
            existing = pdb.find_by_primary_path(conn, folder) if folder else None
            if existing is not None:
                # Idempotent create: duplicates would render N identical sidebar subtrees.
                # Idempotent create: the folder already belongs to a project. Re-activating it beats minting
                # a duplicate — duplicated projects render N identical sidebar subtrees (#75820).
                pdb.set_active(conn, existing.id)
                proj = existing
            else:
                pid = pdb.create_project(conn, name=name, folders=[folder] if folder else [], primary_path=folder or None)
                pdb.set_active(conn, pid)
                proj = pdb.get_project(conn, pid)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    if proj is None:
        return json.dumps({"success": False, "error": "project vanished after create"})
    return _activated(proj, task_id)


def project_switch(project: str, task_id: Optional[str] = None) -> str:
    from hermes_cli import projects_db as pdb
    with pdb.connect_closing() as conn:
        proj = _resolve(conn, project)
        if proj is None:
            return json.dumps({"success": False, "error": f"no project matching '{project}'"})
        pdb.set_active(conn, proj.id)
    return _activated(proj, task_id)


_ACTIONS = {
    "list": lambda args, tid: project_list(task_id=tid),
    "create": lambda args, tid: project_create(
        name=args.get("name", ""), path=args.get("path"), task_id=tid),
    "switch": lambda args, tid: project_switch(project=args.get("name", ""), task_id=tid),
    "fact_list": _project_fact_action,
    "fact_add": _project_fact_action,
    "fact_supersede": _project_fact_action,
}


def _handle_project(args, **kw):
    action = _ACTIONS.get((args.get("action") or "").strip())
    if action is None:
        return json.dumps({"success": False, "error": "action must be one of: create, switch, list, fact_list, fact_add, fact_supersede."})
    return action(args, kw.get("task_id"))


# One action enum instead of three tools: each re-taught "desktop Projects" (244 -> ~145 tok).
# Consolidated (#95681, maintainer-directed): project_list/create/switch each re-taught "desktop Projects
# (named workspaces)"; one action enum says it once (244 -> ~145 tok).
registry.register(
    name="desktop_project",
    toolset="project",
    schema={
        "name": "desktop_project",
        "description": (
            "Create/switch desktop Projects and manage durable facts for the Project attached to this session. create: one and switch "
            "this chat into it — pass path to anchor it to a repo/folder (the "
            "chat's workspace moves there, the sidebar follows). switch: move "
            "this chat into an existing project by name/slug/id — the "
            "intentional way to move the session, not `cd`. list: all projects + which is active. fact_list/fact_add/fact_supersede operate only on this session\'s explicit Project. The agent cannot verify its own inference."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "switch", "list", "fact_list", "fact_add", "fact_supersede"]},
                "name": {"type": "string", "description": "create: human name. switch: name, slug, or id."},
                "path": {"type": "string", "description": "create: repo/folder to anchor to."},
                "content": {"type": "string", "description": "fact_add: durable project-scoped fact."},
                "source_kind": {"type": "string", "enum": ["user", "repository", "session", "tool", "inference"], "description": "fact_add provenance: use user only for direct user statements; repository/tool only for observed evidence; inference for model conclusions."},
                "source_ref": {"type": "string", "description": "fact_add optional source locator."},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "fact_add confidence; unverified inference must be below 1."},
                "sensitive": {"type": "boolean", "description": "fact_add: persist locally but never auto-inject into model context."},
                "fact_id": {"type": "string", "description": "fact_supersede: active fact id to retire."},
            },
            "required": ["action"],
        },
    },
    handler=_handle_project,
)
