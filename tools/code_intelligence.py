"""Compact language-server navigation, code action previews, and safe refactoring."""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.registry import registry

_ACTIONS = (
    "definition", "implementation", "type_definition", "references", "hover", "code_actions",
    "apply_code_action", "document_symbols", "workspace_symbols", "rename",
)
_POSITION_ACTIONS = {
    "definition", "implementation", "type_definition", "references", "hover", "code_actions",
    "apply_code_action", "rename",
}
_SYMBOL_KINDS = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method",
    7: "property", 8: "field", 9: "constructor", 10: "enum", 11: "interface", 12: "function",
    13: "variable", 14: "constant", 15: "string", 16: "number", 17: "boolean", 18: "array",
    19: "object", 20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
    25: "operator", 26: "type_parameter",
}

SCHEMA = {
    "name": "code_intelligence",
    "description": (
        "Use the project's language server for precise code navigation, code action previews, and safe refactoring before broad text search: "
        "find a symbol's definition, implementation, type definition, or references; inspect hover/type information; list file symbols; "
        "search workspace symbols; preview bounded code action edits without executing server commands; "
        "apply one selected text edit through Stardust's guarded patch path; "
        "or rename a symbol across files. Rename applies through Stardust's existing guarded patch tool by default; "
        "set apply=false to preview. Applying a code action requires the exact title from a current preview. "
        "Locations use 1-based lines and columns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string", "enum": list(_ACTIONS),
                "description": "Semantic query to run; code_actions previews edits, apply_code_action uses action_index and action_title from that preview to apply one edit through the guarded patch path, and neither action executes server commands.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Existing source file in the target workspace; relative paths use "
                    "the agent's current working directory."
                ),
            },
            "line": {
                "type": "integer", "minimum": 1,
                "description": "1-based source line for definition, implementation, type_definition, references, hover, code_actions, or apply_code_action.",
            },
            "character": {
                "type": "integer", "minimum": 1, "default": 1,
                "description": (
                    "1-based character column for definition, implementation, type_definition, references, hover, code_actions, or apply_code_action "
                    "(defaults to the start of the line)."
                ),
            },
            "max_results": {
                "type": "integer", "minimum": 1, "maximum": 50, "default": 20,
                "description": "Maximum locations or symbols to return; code action previews are capped at 8.",
            },
            "query": {"type": "string", "description": "Required non-empty symbol text for workspace_symbols."},
            "include_declaration": {
                "type": "boolean", "default": True,
                "description": "Include the declaration site in references results.",
            },
            "new_name": {
                "type": "string", "description": "Required new symbol name for rename; must be a valid identifier for the language."
            },
            "action_title": {
                "type": "string",
                "description": "Exact title from a recent code_actions preview; required by apply_code_action to guard against a changed action list.",
            },
            "action_index": {
                "type": "integer", "minimum": 0, "maximum": 7,
                "description": "Zero-based index from a recent code_actions preview; required with action_title to select one current action.",
            },
            "apply": {
                "type": "boolean", "default": True,
                "description": "Apply a rename using the existing guarded patch path (default true); false returns a bounded diff preview.",
            },
        },
        "required": ["action", "path"],
    },
}


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _position_of(item: Dict[str, Any]) -> Tuple[int, int]:
    rng = item.get("targetSelectionRange") or item.get("selectionRange") or item.get("range") or {}
    start = rng.get("start") if isinstance(rng, dict) else None
    start = start if isinstance(start, dict) else {}
    return int(start.get("line", 0)) + 1, int(start.get("character", 0)) + 1


def _location(item: Any, fallback_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    loc = item.get("location") if isinstance(item.get("location"), dict) else item
    uri = item.get("targetUri") or loc.get("uri")
    if isinstance(uri, str):
        from agent.lsp.client import uri_to_path
        path = uri_to_path(uri)
    else:
        path = fallback_path
    line, column = _position_of(item if item.get("targetUri") else loc)
    result: Dict[str, Any] = {}
    if path:
        result["path"] = path
    result["line"], result["column"] = line, column
    return result


def _bounded(items: List[Dict[str, Any]], limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    return items[:limit], len(items) > limit


def _locations(raw: Any, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    values = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    locations = [loc for item in values if (loc := _location(item)) is not None]
    return _bounded(locations, limit)


def _symbols(raw: Any, fallback_path: str, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    symbols: List[Dict[str, Any]] = []

    def visit(values: Any, parent: Optional[str] = None) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if not isinstance(value, dict):
                continue
            loc = _location(value, fallback_path)
            if loc is None:
                line, column = _position_of(value)
                loc = {"path": fallback_path, "line": line, "column": column}
            row = {"name": str(value.get("name") or ""), **loc}
            kind = value.get("kind")
            if isinstance(kind, int):
                row["kind"] = _SYMBOL_KINDS.get(kind, str(kind))
            if value.get("detail"):
                row["detail"] = str(value["detail"])[:300]
            if parent:
                row["container"] = parent
            symbols.append(row)
            visit(value.get("children"), str(value.get("name") or parent or ""))

    visit(raw)
    return _bounded(symbols, limit)


def _hover_text(raw: Any) -> str:
    contents = raw.get("contents") if isinstance(raw, dict) else raw
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value") or contents.get("language") or "")
    if isinstance(contents, list):
        parts = [_hover_text(item) for item in contents]
        return "\n\n".join(part for part in parts if part)
    return ""


def _resolve_source(path_value: Any, task_id: str) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(path_value, str) or not path_value.strip():
        return None, "path must be a non-empty file path."
    try:
        from tools.file_tools_paths import _resolve_path_for_task, _uses_container_paths
        if _uses_container_paths(task_id):
            return None, (
                "The current terminal workspace is container-backed; LSP needs a workspace "
                "that is locally accessible to the Agent host."
            )
        absolute = os.path.abspath(str(_resolve_path_for_task(path_value, task_id)))
    except Exception as e:  # noqa: BLE001
        return None, f"Could not resolve the file in the active task workspace: {e}"
    if not os.path.isfile(absolute):
        return None, (
            "The source file is not available on the Agent host; "
            "LSP needs a locally accessible workspace file."
        )
    return absolute, None


def _position_offset(text: str, line: int, character: int) -> int:
    """Convert an LSP UTF-16 position to a Python string offset, rejecting split surrogates."""
    lines = text.split("\n")
    if line < 0 or line >= len(lines):
        raise ValueError("language server returned an edit outside the current file")
    raw_line = lines[line]
    body = raw_line[:-1] if raw_line.endswith("\r") else raw_line
    wanted = character
    units = 0
    if wanted < 0:
        raise ValueError("language server returned a negative edit position")
    if wanted == 0:
        return sum(len(part) + 1 for part in lines[:line])
    for index, char in enumerate(body):
        next_units = units + (2 if ord(char) > 0xFFFF else 1)
        if next_units > wanted:
            raise ValueError("language server edit splits a UTF-16 character")
        units = next_units
        if units == wanted:
            return sum(len(part) + 1 for part in lines[:line]) + index + 1
    if units == wanted:
        return sum(len(part) + 1 for part in lines[:line]) + len(body)
    raise ValueError("language server returned an edit beyond the end of the current line")


def _lsp_position_value(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("language server returned an invalid edit position")
    return value


def _workspace_edits(raw: Any, workspace_root: str) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize an LSP WorkspaceEdit and reject resource operations/out-of-workspace paths."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("language server returned an invalid workspace edit")
    if raw.get("changeAnnotations"):
        raise ValueError("workspace edit includes change annotations that require confirmation")
    changes: Dict[str, List[Dict[str, Any]]] = {}
    change_map = raw.get("changes")
    if change_map is not None:
        if not isinstance(change_map, dict):
            raise ValueError("language server returned an invalid workspace edit map")
        changes = change_map
    document_changes = raw.get("documentChanges")
    if document_changes is not None:
        if not isinstance(document_changes, list):
            raise ValueError("language server returned an invalid document edit list")
        if changes:
            raise ValueError("language server returned both WorkspaceEdit formats; refusing ambiguous changes")
        for item in document_changes:
            if not isinstance(item, dict) or not isinstance(item.get("textDocument"), dict):
                raise ValueError("workspace edit includes a file create/delete/move operation; refusing to preview it")
            uri = item["textDocument"].get("uri")
            edits = item.get("edits")
            if not isinstance(uri, str) or not isinstance(edits, list):
                raise ValueError("language server returned an invalid text document edit")
            changes[uri] = edits
    root = os.path.realpath(workspace_root)
    normalized: Dict[str, List[Dict[str, Any]]] = {}
    edit_count = 0
    from agent.lsp.client import uri_to_path
    for uri, edits in changes.items():
        if not isinstance(uri, str) or not uri.startswith("file://") or not isinstance(edits, list):
            raise ValueError("workspace edit returned a non-file document edit; refusing to preview it")
        path = os.path.realpath(uri_to_path(uri))
        try:
            inside = os.path.normcase(os.path.commonpath([root, path])) == os.path.normcase(root)
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(path):
            raise ValueError(f"workspace edit target is missing or outside the active Git workspace: {path}")
        edit_count += len(edits)
        if edit_count > 5000:
            raise ValueError("workspace edit contains more than 5,000 edits; narrow the operation before previewing")
        normalized[path] = edits
    return normalized


def _apply_workspace_edits(raw: Any, workspace_root: str) -> Tuple[Dict[str, str], Dict[str, str], Optional[str]]:
    """Build exact per-file contents from UTF-16 LSP ranges, without writing anything."""
    try:
        edits_by_path = _workspace_edits(raw, workspace_root)
        if len(edits_by_path) > 200:
            raise ValueError("workspace edit spans more than 200 files; narrow the operation before previewing")
        updated: Dict[str, str] = {}
        originals: Dict[str, str] = {}
        for path, edits in edits_by_path.items():
            if not edits:
                continue
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            original = text
            indexed = []
            for edit in edits:
                if not isinstance(edit, dict) or not isinstance(edit.get("newText"), str):
                    raise ValueError("language server returned an invalid workspace text edit")
                if edit.get("annotationId") is not None:
                    raise ValueError("workspace text edit includes an annotation that requires confirmation")
                rng = edit.get("range")
                if not isinstance(rng, dict) or not isinstance(rng.get("start"), dict) or not isinstance(rng.get("end"), dict):
                    raise ValueError("language server returned a workspace text edit without a range")
                start = _position_offset(
                    text, _lsp_position_value(rng["start"].get("line")),
                    _lsp_position_value(rng["start"].get("character")),
                )
                end = _position_offset(
                    text, _lsp_position_value(rng["end"].get("line")),
                    _lsp_position_value(rng["end"].get("character")),
                )
                if start > end:
                    raise ValueError("language server returned a reversed text edit range")
                indexed.append((start, end, edit["newText"]))
            indexed.sort(key=lambda item: (item[0], item[1]))
            for previous, current in zip(indexed, indexed[1:]):
                if previous[1] > current[0] or previous[0] == current[0]:
                    raise ValueError(f"language server returned overlapping workspace edits for {path}")
            for start, end, replacement in reversed(indexed):
                text = text[:start] + replacement + text[end:]
            if text != original:
                originals[path] = original
                updated[path] = text
        return updated, originals, None
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        return {}, {}, str(e)


def _v4a_update(path: str, old: str, new: str) -> str:
    """Serialize one exact file diff into Stardust's existing multi-file patch format."""
    diff = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile="", tofile="", n=3, lineterm=""))
    hunks = [line for line in diff[2:] if not line.startswith("\\ No newline at end of file")]
    if not hunks:
        return ""
    return "*** Update File: " + path + "\n" + "\n".join(hunks)


def _code_action_previews(raw: Any, source_path: str, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Return bounded LSP CodeAction metadata and guarded previews without applying edits."""
    values = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    limit = min(limit, 8)
    truncated = len(values) > limit
    from agent.lsp.workspace import resolve_workspace_for_file
    workspace_root, gated = resolve_workspace_for_file(source_path)
    preview_budget = 7000
    previews: List[Dict[str, Any]] = []

    for index, item in enumerate(values[:limit]):
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        row: Dict[str, Any] = {
            "index": index,
            "title": str(title)[:500] if title is not None else "Untitled code action",
        }
        kind = item.get("kind")
        if isinstance(kind, str):
            row["kind"] = kind[:120]
        if isinstance(item.get("isPreferred"), bool):
            row["preferred"] = item["isPreferred"]
        disabled = item.get("disabled")
        if isinstance(disabled, dict) and isinstance(disabled.get("reason"), str):
            row["disabled_reason"] = disabled["reason"][:300]

        edit = item.get("edit")
        command = item.get("command")
        row["has_edit"] = isinstance(edit, dict)
        row["has_command"] = bool(command)
        if command:
            row["command_not_run"] = True
            if isinstance(command, dict) and isinstance(command.get("command"), str):
                row["command_name"] = command["command"][:160]
            elif isinstance(command, str):
                row["command_name"] = command[:160]

        if isinstance(edit, dict):
            if not gated or not workspace_root:
                row["preview_error"] = "Code action edit preview requires a Git workspace."
            else:
                updates, originals, error = _apply_workspace_edits(edit, workspace_root)
                if error:
                    row["preview_error"] = error[:500]
                else:
                    patch_parts = [
                        patch for path in sorted(updates)
                        if (patch := _v4a_update(path, originals[path], updates[path]))
                    ]
                    row["files_changed"] = len(patch_parts)
                    if patch_parts:
                        patch_preview = "*** Begin Patch\n" + "\n".join(patch_parts) + "\n*** End Patch"
                        preview_chars = min(len(patch_preview), preview_budget, 1800)
                        row["diff_preview"] = patch_preview[:preview_chars]
                        row["preview_truncated"] = preview_chars < len(patch_preview)
                        preview_budget -= preview_chars
                    else:
                        row["diff_preview"] = ""
                        row["preview_truncated"] = False
        elif not disabled:
            row["preview_note"] = "This action has no workspace edit; its command, if any, was not run."

        previews.append(row)
    return previews, truncated


def _workspace_patch_parts(
    updates: Dict[str, str], originals: Dict[str, str]
) -> Tuple[List[str], List[str], Optional[str]]:
    """Build guarded-patch updates only while every LSP source file is unchanged."""
    paths: List[str] = []
    parts: List[str] = []
    try:
        for path, new_text in updates.items():
            if Path(path).read_text(encoding="utf-8", errors="replace") != originals[path]:
                raise ValueError(f"workspace edit target changed while preparing the edit: {path}")
            patch = _v4a_update(path, originals[path], new_text)
            if patch:
                paths.append(path)
                parts.append(patch)
    except (OSError, RuntimeError, ValueError) as e:
        return [], [], str(e)
    return paths, parts, None


def _apply_guarded_patch(
    patch_parts: List[str], task_id: str, session_id: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Apply a language-server edit only through Stardust's existing guarded patch tool."""
    if not patch_parts:
        return {}, None
    try:
        from tools.file_tools import patch_tool

        result_text = patch_tool(
            mode="patch", patch="*** Begin Patch\n" + "\n".join(patch_parts) + "\n*** End Patch",
            task_id=task_id, session_id=session_id,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"Could not apply the language-server edit through Stardust's patch tool: {e}"
    try:
        result = json.loads(result_text)
    except (TypeError, json.JSONDecodeError):
        return None, "Patch tool returned an unstructured result."
    if not isinstance(result, dict):
        return None, "Patch tool returned an unstructured result."
    return result, None


def _apply_code_action(
    args: Dict[str, Any], source_path: str, raw: Any, task_id: str, session_id: Optional[str]
) -> str:
    """Apply the selected current LSP text edit through the existing write guards."""
    from agent.lsp.workspace import resolve_workspace_for_file

    workspace_root, gated = resolve_workspace_for_file(source_path)
    if not gated or not workspace_root:
        return _json({"available": False, "action": "apply_code_action", "error": "Applying a code action requires a file inside a Git worktree."})

    title = args["action_title"]
    index = args["action_index"]
    values = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    selected = values[index] if index < len(values) and isinstance(values[index], dict) else None
    if selected is None or selected.get("title") != title:
        return _json({"available": False, "action": "apply_code_action", "error": "That code action changed; preview code actions again and choose a current index and title."})
    if selected.get("disabled") is not None:
        reason = selected["disabled"].get("reason") if isinstance(selected["disabled"], dict) else None
        detail = f": {str(reason)[:300]}" if reason else ""
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": f"The selected code action is disabled{detail}."})
    if selected.get("command") is not None:
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": "The selected action requires a language-server command; commands are not executed."})
    edit = selected.get("edit")
    if not isinstance(edit, dict):
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": "The selected action has no workspace text edit to apply."})

    updates, originals, error = _apply_workspace_edits(edit, workspace_root)
    if error:
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": error})
    if not updates:
        return _json({"available": True, "action": "apply_code_action", "title": title, "applied": False,
                      "files_changed": 0, "message": "The selected code action makes no text changes."})
    paths, patch_parts, error = _workspace_patch_parts(updates, originals)
    if error:
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": error})
    if not patch_parts:
        return _json({"available": True, "action": "apply_code_action", "title": title, "applied": False,
                      "files_changed": 0, "message": "The selected code action makes no text changes."})

    result, error = _apply_guarded_patch(patch_parts, task_id, session_id)
    if error:
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": error})
    if result is None:
        return _json({"available": False, "action": "apply_code_action", "title": title, "error": "Patch tool returned an unstructured result."})
    modified = result.get("files_modified") or []
    if result.get("error"):
        return _json({"available": False, "action": "apply_code_action", "title": title,
                      "applied": bool(modified), "files_modified": modified, "error": result["error"]})
    return _json({
        "available": True, "action": "apply_code_action", "title": title, "action_index": index, "applied": True,
        "files_changed": len(modified), "files_modified": modified,
        "lint": result.get("lint"), "lsp_diagnostics": result.get("lsp_diagnostics"),
        "warning": result.get("_warning"),
    })


def _rename(args: Dict[str, Any], source_path: str, raw: Any, task_id: str, session_id: Optional[str]) -> str:
    from agent.lsp.workspace import resolve_workspace_for_file
    workspace_root, gated = resolve_workspace_for_file(source_path)
    if not gated or not workspace_root:
        return _json({"available": False, "action": "rename", "error": "Rename requires a file inside a Git worktree."})
    updates, originals, error = _apply_workspace_edits(raw, workspace_root)
    if error:
        return _json({"available": False, "action": "rename", "error": error})
    if not updates:
        return _json({"available": True, "action": "rename", "applied": False, "files_changed": 0, "edits": 0,
                      "message": "The language server found no changes for this symbol."})
    new_name = args["new_name"]
    patch_paths, patch_parts, error = _workspace_patch_parts(updates, originals)
    if error:
        return _json({"available": False, "action": "rename", "error": error})
    if not patch_parts:
        return _json({"available": True, "action": "rename", "applied": False, "files_changed": 0,
                      "edits": 0, "message": "The proposed rename does not change the current files."})
    if args.get("apply", True) is not True:
        preview = "\n".join(patch_parts)
        return _json({
            "available": True, "action": "rename", "applied": False, "new_name": new_name,
            "files_changed": len(patch_paths), "edits": sum(len(v) for v in _workspace_edits(raw, workspace_root).values()),
            "diff_preview": preview[:12000], "truncated": len(preview) > 12000,
        })
    patch_text = "*** Begin Patch\n" + "\n".join(patch_parts) + "\n*** End Patch"
    try:
        from tools.file_tools import patch_tool
        result_text = patch_tool(
            mode="patch", patch=patch_text, task_id=task_id, session_id=session_id,
        )
    except Exception as e:  # noqa: BLE001
        return _json({"available": False, "action": "rename", "error": f"Could not apply the rename through Stardust's patch tool: {e}"})
    try:
        result = json.loads(result_text)
    except (TypeError, json.JSONDecodeError):
        return _json({"available": False, "action": "rename", "error": "Patch tool returned an unstructured result.", "details": str(result_text)[:4000]})
    if not isinstance(result, dict):
        return _json({"available": False, "action": "rename", "error": "Patch tool returned an unstructured result.", "details": str(result_text)[:4000]})
    modified = result.get("files_modified") or []
    if result.get("error"):
        return _json({
            "available": False, "action": "rename", "applied": bool(modified), "new_name": new_name,
            "files_modified": modified, "error": result["error"],
        })
    return _json({
        "available": True, "action": "rename", "applied": True, "new_name": new_name,
        "files_changed": len(modified), "files_modified": modified,
        "lint": result.get("lint"), "lsp_diagnostics": result.get("lsp_diagnostics"),
        "warning": result.get("_warning"),
    })


def code_intelligence(args: Dict[str, Any], *, task_id: str = "default", session_id: Optional[str] = None) -> str:
    action = args.get("action")
    if action not in _ACTIONS:
        return _json({"available": False, "error": f"action must be one of: {', '.join(_ACTIONS)}"})
    path, error = _resolve_source(args.get("path"), task_id)
    if error:
        return _json({"available": False, "error": error})
    line, character = args.get("line"), args.get("character", 1)
    if action in _POSITION_ACTIONS:
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            return _json({"available": False, "error": f"{action} requires a 1-based line number."})
        if not isinstance(character, int) or isinstance(character, bool) or character < 1:
            return _json({"available": False, "error": "character must be a 1-based positive integer."})
    query = args.get("query", "")
    if action == "workspace_symbols":
        if not isinstance(query, str) or not query.strip():
            return _json({"available": False, "error": "workspace_symbols requires a non-empty query."})
        if len(query) > 200:
            return _json({"available": False, "error": "workspace_symbols query cannot exceed 200 characters."})
        query = query.strip()
    elif not isinstance(query, str):
        query = ""
    new_name = args.get("new_name", "")
    if action == "rename":
        if not isinstance(new_name, str) or not new_name.strip():
            return _json({"available": False, "error": "rename requires a non-empty new_name."})
        if len(new_name) > 200:
            return _json({"available": False, "error": "new_name cannot exceed 200 characters."})
        new_name = new_name.strip()
    if action == "rename" and not isinstance(args.get("apply", True), bool):
        return _json({"available": False, "error": "apply must be a boolean."})
    action_title = args.get("action_title", "")
    action_index = args.get("action_index")
    if action == "apply_code_action":
        if not isinstance(action_title, str) or not action_title or len(action_title) > 500:
            return _json({"available": False, "error": "apply_code_action requires an exact action_title of 1 to 500 characters."})
        if not isinstance(action_index, int) or isinstance(action_index, bool) or not 0 <= action_index < 8:
            return _json({"available": False, "error": "apply_code_action requires an action_index from 0 to 7 in a recent preview."})
    limit = args.get("max_results", 20)
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = 20
    limit = max(1, min(limit, 50))
    try:
        from agent.lsp import get_service
        service = get_service()
    except Exception as e:  # noqa: BLE001
        return _json({"available": False, "error": f"Could not initialize the LSP service: {e}"})
    if service is None or not service.is_active():
        return _json({"available": False, "error": "LSP is disabled or unavailable in the current profile."})
    query_action = "code_actions" if action == "apply_code_action" else action
    response = service.semantic_query_sync(
        path, query_action,
        line=(line - 1 if isinstance(line, int) and not isinstance(line, bool) else None),
        character=(character - 1 if isinstance(character, int) and not isinstance(character, bool) else None),
        query=query,
        include_declaration=(
            args.get("include_declaration")
            if isinstance(args.get("include_declaration"), bool) else True
        ),
        new_name=new_name,
    )
    if not response.get("available"):
        return _json({
            "available": False, "action": action,
            "error": response.get("reason", "No semantic result available."),
        })
    raw = response.get("result")
    if action == "rename":
        return _rename(args, path, raw, task_id, session_id)
    if action == "apply_code_action":
        return _apply_code_action(args, path, raw, task_id, session_id)
    payload: Dict[str, Any] = {"available": True, "action": action, "path": path}
    if action == "code_actions":
        rows, truncated = _code_action_previews(raw, path, limit)
        payload.update({"actions": rows, "truncated": truncated})
    elif action == "hover":
        hover = _hover_text(raw)
        payload.update({"found": bool(hover), "hover": hover[:8000]})
        if len(hover) > 8000:
            payload["truncated"] = True
    elif action in {"document_symbols", "workspace_symbols"}:
        rows, truncated = _symbols(raw, path, limit)
        payload.update({"symbols": rows, "truncated": truncated})
    else:
        rows, truncated = _locations(raw, limit)
        payload.update({"locations": rows, "truncated": truncated})
    return _json(payload)


registry.register(
    name="code_intelligence",
    toolset="code_intelligence",
    schema=SCHEMA,
    handler=lambda args, **kw: code_intelligence(
        args, task_id=kw.get("task_id") or "default", session_id=kw.get("session_id")),
    max_result_size_chars=16000,
    emoji="🔎",
)
