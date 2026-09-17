"""Profile-scoped structured contact memory for the personal assistant.

This store is deliberately separate from USER.md/MEMORY.md: contact aliases and channel handles
can grow without consuming the tiny every-turn memory budget, while preferences and general facts
stay in the existing memory system. Nothing is auto-imported from connected services; callers only
persist details the user explicitly supplied or confirmed.
"""
from __future__ import annotations

import contextlib
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

from hermes_cli.sqlite_util import open_db, write_txn
from hermes_constants import get_hermes_home


def contacts_db_path() -> Path:
    return get_hermes_home() / "contacts.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    relationship  TEXT,
    notes         TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contact_aliases (
    contact_id  TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    alias       TEXT NOT NULL,
    alias_key   TEXT NOT NULL UNIQUE,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (contact_id, alias_key)
);

CREATE TABLE IF NOT EXISTS contact_channels (
    contact_id  TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    handle      TEXT NOT NULL,
    label       TEXT,
    created_at  INTEGER NOT NULL,
    UNIQUE (channel, handle),
    UNIQUE (contact_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_contact_aliases_contact
    ON contact_aliases(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_channels_contact
    ON contact_channels(contact_id);
"""


def _now() -> int:
    return int(time.time())


def _clean_text(value: object, *, limit: int = 1000) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def normalize_alias(value: object) -> str:
    """Stable exact-match key for human names/aliases, including non-Latin text."""
    text = unicodedata.normalize("NFKC", _clean_text(value, limit=300))
    return text.casefold()


def normalize_channel(value: object) -> str:
    raw = _clean_text(value, limit=80).lower().replace(" ", "_")
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})
    if not cleaned:
        raise ValueError("channel is required")
    return cleaned


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path if db_path is not None else contacts_db_path()
    return open_db(
        path,
        db_label="contacts.db",
        foreign_keys=True,
        initialize=lambda conn: conn.executescript(SCHEMA_SQL),
    )


@contextlib.contextmanager
def connect_closing(db_path: Optional[Path] = None):
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@dataclass(frozen=True)
class ContactChannel:
    channel: str
    handle: str
    label: Optional[str] = None

    def to_dict(self) -> dict:
        return {"channel": self.channel, "handle": self.handle, "label": self.label}


@dataclass(frozen=True)
class Contact:
    id: str
    display_name: str
    created_at: int
    updated_at: int
    relationship: Optional[str] = None
    notes: Optional[str] = None
    archived: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)
    channels: tuple[ContactChannel, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "relationship": self.relationship,
            "notes": self.notes,
            "archived": bool(self.archived),
            "aliases": list(self.aliases),
            "channels": [channel.to_dict() for channel in self.channels],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _load_contact(conn: sqlite3.Connection, row: sqlite3.Row) -> Contact:
    aliases = conn.execute(
        "SELECT alias FROM contact_aliases WHERE contact_id = ? ORDER BY created_at, alias_key",
        (row["id"],),
    ).fetchall()
    channels = conn.execute(
        "SELECT channel, handle, label FROM contact_channels WHERE contact_id = ? ORDER BY channel",
        (row["id"],),
    ).fetchall()
    return Contact(
        id=row["id"],
        display_name=row["display_name"],
        relationship=row["relationship"],
        notes=row["notes"],
        archived=bool(row["archived"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        aliases=tuple(item["alias"] for item in aliases),
        channels=tuple(ContactChannel(item["channel"], item["handle"], item["label"]) for item in channels),
    )


def get_contact(conn: sqlite3.Connection, contact_id: str, *, include_archived: bool = False) -> Optional[Contact]:
    sql = "SELECT * FROM contacts WHERE id = ?" + ("" if include_archived else " AND archived = 0")
    row = conn.execute(sql, (contact_id,)).fetchone()
    return _load_contact(conn, row) if row else None


def _contact_ids_for_aliases(conn: sqlite3.Connection, alias_keys: Iterable[str]) -> set[str]:
    keys = list(dict.fromkeys(key for key in alias_keys if key))
    if not keys:
        return set()
    placeholders = ",".join("?" for _ in keys)
    return {
        row["contact_id"]
        for row in conn.execute(
            f"SELECT contact_id FROM contact_aliases WHERE alias_key IN ({placeholders})",
            tuple(keys),
        ).fetchall()
    }


def remember_contact(
    conn: sqlite3.Connection,
    *,
    display_name: str,
    aliases: Iterable[str] = (),
    relationship: Optional[str] = None,
    notes: Optional[str] = None,
    channels: Optional[Mapping[str, str]] = None,
) -> Contact:
    """Create or update one contact using exact aliases as identity anchors.

    Aliases accumulate. A supplied channel replaces that contact's previous handle for the same
    channel. Any alias or channel already owned by another contact fails the whole transaction.
    """
    name = _clean_text(display_name, limit=300)
    if not name:
        raise ValueError("display_name is required")
    clean_aliases = list(dict.fromkeys(
        value for value in (_clean_text(alias, limit=300) for alias in (name, *tuple(aliases))) if value
    ))
    alias_pairs = [(alias, normalize_alias(alias)) for alias in clean_aliases]
    relationship_value = _clean_text(relationship, limit=500) if relationship is not None else None
    notes_value = _clean_text(notes, limit=2000) if notes is not None else None
    channel_pairs = [
        (normalize_channel(channel), _clean_text(handle, limit=500))
        for channel, handle in (channels or {}).items()
        if _clean_text(handle, limit=500)
    ]

    existing_ids = _contact_ids_for_aliases(conn, (key for _, key in alias_pairs))
    if len(existing_ids) > 1:
        raise ValueError("aliases resolve to multiple existing contacts; specify one person unambiguously")
    contact_id = next(iter(existing_ids), None)
    now = _now()
    if contact_id is None:
        contact_id = "c_" + secrets.token_hex(6)

    with write_txn(conn):
        row = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO contacts (id, display_name, relationship, notes, created_at, updated_at, archived) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (contact_id, name, relationship_value, notes_value, now, now),
            )
        else:
            sets = ["display_name = ?", "updated_at = ?", "archived = 0"]
            values: list[object] = [name, now]
            if relationship is not None:
                sets.append("relationship = ?")
                values.append(relationship_value)
            if notes is not None:
                sets.append("notes = ?")
                values.append(notes_value)
            values.append(contact_id)
            conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id = ?", tuple(values))

        for alias, alias_key in alias_pairs:
            owner = conn.execute(
                "SELECT contact_id FROM contact_aliases WHERE alias_key = ?", (alias_key,)
            ).fetchone()
            if owner is not None and owner["contact_id"] != contact_id:
                raise ValueError(f"alias {alias!r} already belongs to another contact")
            conn.execute(
                "INSERT OR IGNORE INTO contact_aliases (contact_id, alias, alias_key, created_at) "
                "VALUES (?, ?, ?, ?)",
                (contact_id, alias, alias_key, now),
            )

        for channel, handle in channel_pairs:
            owner = conn.execute(
                "SELECT contact_id FROM contact_channels WHERE channel = ? AND handle = ?",
                (channel, handle),
            ).fetchone()
            if owner is not None and owner["contact_id"] != contact_id:
                raise ValueError(f"{channel} handle {handle!r} already belongs to another contact")
            conn.execute(
                "DELETE FROM contact_channels WHERE contact_id = ? AND channel = ?",
                (contact_id, channel),
            )
            conn.execute(
                "INSERT INTO contact_channels (contact_id, channel, handle, label, created_at) "
                "VALUES (?, ?, ?, NULL, ?)",
                (contact_id, channel, handle, now),
            )

    contact = get_contact(conn, contact_id, include_archived=True)
    if contact is None:  # pragma: no cover - defensive
        raise RuntimeError("contact write committed but contact could not be reloaded")
    return contact


def lookup_contact(
    conn: sqlite3.Connection,
    query: str,
    *,
    channel: Optional[str] = None,
    include_archived: bool = False,
) -> Optional[Contact]:
    """Exact alias/name lookup; channel optionally narrows the returned contact to one with that handle type."""
    key = normalize_alias(query)
    if not key:
        return None
    row = conn.execute(
        "SELECT c.* FROM contact_aliases a JOIN contacts c ON c.id = a.contact_id "
        "WHERE a.alias_key = ?" + ("" if include_archived else " AND c.archived = 0"),
        (key,),
    ).fetchone()
    if row is None:
        return None
    contact = _load_contact(conn, row)
    if channel is not None:
        wanted = normalize_channel(channel)
        if not any(item.channel == wanted for item in contact.channels):
            return None
    return contact


def list_contacts(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    channel: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 50,
) -> list[Contact]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    rows = conn.execute(
        "SELECT * FROM contacts" + ("" if include_archived else " WHERE archived = 0") + " ORDER BY updated_at DESC, id",
    ).fetchall()
    needle = normalize_alias(query)
    wanted_channel = normalize_channel(channel) if channel else None
    result: list[Contact] = []
    for row in rows:
        contact = _load_contact(conn, row)
        if needle and not any(needle in normalize_alias(value) for value in (contact.display_name, *contact.aliases)):
            continue
        if wanted_channel and not any(item.channel == wanted_channel for item in contact.channels):
            continue
        result.append(contact)
        if len(result) >= limit:
            break
    return result


def archive_contact(conn: sqlite3.Connection, contact_id: str) -> bool:
    """Soft-delete one contact so accidental removal is reversible at the DB level."""
    with write_txn(conn):
        cur = conn.execute(
            "UPDATE contacts SET archived = 1, updated_at = ? WHERE id = ? AND archived = 0",
            (_now(), contact_id),
        )
    return cur.rowcount == 1
