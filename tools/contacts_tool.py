"""Structured contact memory tool for personal-assistant identity resolution."""
from __future__ import annotations

import json
from typing import Any, Mapping

from hermes_cli import contacts_db
from tools.registry import registry, tool_error


_ACTIONS = frozenset({"remember", "lookup", "list", "archive"})


def _channels(values: Any) -> dict[str, str]:
    if values is None:
        return {}
    if not isinstance(values, list):
        raise ValueError("channels must be a list of {channel, handle} objects")
    result: dict[str, str] = {}
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            raise ValueError(f"channels[{index}] must be an object")
        channel = str(item.get("channel") or "").strip()
        handle = str(item.get("handle") or "").strip()
        if not channel or not handle:
            raise ValueError(f"channels[{index}] requires channel and handle")
        normalized = contacts_db.normalize_channel(channel)
        if normalized in result:
            raise ValueError(f"channel {normalized!r} appears more than once")
        result[normalized] = handle
    return result


def _public_contact(contact: contacts_db.Contact) -> dict[str, Any]:
    return contact.to_dict()


def _contact_resolution(
    contact: contacts_db.Contact | None,
    *,
    requested_channel: str | None,
) -> dict[str, Any]:
    """Project one lookup into an explicit send-routing state.

    The contacts DB already guarantees exact aliases are globally unique. The remaining ambiguity
    is channel choice: no channel cannot be sent, exactly one is safe to use, and multiple require
    the user/model to select a channel explicitly instead of silently picking one.
    """
    if contact is None:
        return {
            "status": "not_found",
            "requires_user_input": True,
            "selected": None,
            "available_channels": [],
        }

    available = [item.to_dict() for item in contact.channels]
    if requested_channel:
        wanted = contacts_db.normalize_channel(requested_channel)
        selected = next((item for item in available if item["channel"] == wanted), None)
        if selected is not None:
            return {
                "status": "resolved",
                "requires_user_input": False,
                "selected": selected,
                "available_channels": available,
            }
        return {
            "status": "channel_unavailable",
            "requires_user_input": True,
            "requested_channel": wanted,
            "selected": None,
            "available_channels": available,
        }

    if len(available) == 1:
        return {
            "status": "resolved",
            "requires_user_input": False,
            "selected": available[0],
            "available_channels": available,
        }
    if not available:
        return {
            "status": "missing_channel",
            "requires_user_input": True,
            "selected": None,
            "available_channels": [],
        }
    return {
        "status": "needs_channel_selection",
        "requires_user_input": True,
        "selected": None,
        "available_channels": available,
    }


def contacts_tool(args: dict[str, Any], **_kwargs: Any) -> str:
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return tool_error(f"contacts action must be one of: {', '.join(sorted(_ACTIONS))}")

    try:
        with contacts_db.connect_closing() as conn:
            if action == "remember":
                display_name = str(args.get("display_name") or "").strip()
                if not display_name:
                    return tool_error("contacts remember requires display_name")
                aliases = args.get("aliases") or []
                if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                    return tool_error("contacts aliases must be a list of strings")
                contact = contacts_db.remember_contact(
                    conn,
                    display_name=display_name,
                    aliases=aliases,
                    relationship=args.get("relationship"),
                    notes=args.get("notes"),
                    channels=_channels(args.get("channels")),
                )
                return json.dumps({
                    "ok": True,
                    "kind": "contact",
                    "contact": _public_contact(contact),
                }, ensure_ascii=False)

            if action == "lookup":
                query = str(args.get("query") or "").strip()
                if not query:
                    return tool_error("contacts lookup requires query")
                requested_channel = str(args.get("channel") or "").strip() or None
                # Resolve the human identity first, then project channel availability separately.
                # This distinguishes "person not found" from "person found but no such channel".
                contact = contacts_db.lookup_contact(conn, query)
                resolution = _contact_resolution(contact, requested_channel=requested_channel)
                return json.dumps({
                    "ok": True,
                    "kind": "contact_lookup",
                    "query": query,
                    "found": contact is not None,
                    "contact": _public_contact(contact) if contact else None,
                    "resolution": resolution,
                }, ensure_ascii=False)

            if action == "list":
                contacts = contacts_db.list_contacts(
                    conn,
                    query=str(args.get("query") or "").strip(),
                    channel=str(args.get("channel") or "").strip() or None,
                    limit=int(args.get("limit") or 50),
                )
                return json.dumps({
                    "ok": True,
                    "kind": "contact_list",
                    "count": len(contacts),
                    "contacts": [_public_contact(contact) for contact in contacts],
                }, ensure_ascii=False)

            contact_id = str(args.get("contact_id") or "").strip()
            if not contact_id:
                return tool_error("contacts archive requires contact_id")
            archived = contacts_db.archive_contact(conn, contact_id)
            if not archived:
                return tool_error(f"contact {contact_id} was not found or is already archived")
            return json.dumps({
                "ok": True,
                "kind": "contact",
                "contact_id": contact_id,
                "archived": True,
            }, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"contacts: {exc}")


CONTACTS_SCHEMA = {
    "name": "contacts",
    "description": (
        "Structured personal contact memory. Use it to resolve durable person aliases such as '老王' to one "
        "confirmed identity and communication route. Only remember identity/relationship/channel details the user "
        "explicitly supplied or confirmed; never infer private contact details or silently import an address book from "
        "connected services. Use lookup before an external communication action when the user names a person by alias. "
        "The lookup result includes resolution.status: resolved means resolution.selected is safe to use; "
        "missing_channel/channel_unavailable/needs_channel_selection require user input instead of guessing a route. "
        "General user preferences belong in memory(target='user'); project paths/identity belong in Projects, not here."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remember", "lookup", "list", "archive"],
                "description": "remember/update, exact alias lookup, filtered list, or soft-delete a contact.",
            },
            "display_name": {"type": "string", "description": "Canonical human-readable name for remember."},
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Durable aliases/nicknames the user uses for this person, e.g. 老王.",
            },
            "relationship": {
                "type": "string",
                "description": "Optional stable relationship supplied by the user, e.g. colleague, accountant.",
            },
            "notes": {
                "type": "string",
                "description": "Optional compact identity/disambiguation note; do not put task history here.",
            },
            "channels": {
                "type": "array",
                "description": "Optional confirmed communication handles. One handle per channel in v1.",
                "items": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "wechat, email, slack, phone, etc."},
                        "handle": {"type": "string", "description": "Confirmed address/id/handle for that channel."},
                    },
                    "required": ["channel", "handle"],
                },
            },
            "query": {"type": "string", "description": "Exact alias/name for lookup; substring filter for list."},
            "channel": {
                "type": "string",
                "description": "Optional required channel for lookup/list. For alias-based sending, pass it when the user already specified the medium.",
            },
            "contact_id": {"type": "string", "description": "Stable contact id for archive."},
            "limit": {"type": "integer", "description": "List limit, 1-200 (default 50)."},
        },
        "required": ["action"],
    },
}


registry.register(
    name="contacts",
    toolset="memory",
    schema=CONTACTS_SCHEMA,
    handler=lambda args, **kw: contacts_tool(args, **kw),
    description="Structured personal contact aliases and confirmed communication handles",
    emoji="👤",
)
