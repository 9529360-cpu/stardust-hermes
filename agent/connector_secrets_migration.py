#!/usr/bin/env python3
"""One-time migration of legacy plaintext connector credentials into the
OS-encrypted connector secret store (CX2b).

Runs on the first MCP config load after the store became enabled by default.
For every ``mcp_servers`` entry whose credential is a legacy ``${ENV_VAR}``
reference, the plaintext value is read from the profile ``.env``, copied into
the store, the config reference rewritten to ``${connector:<server>/api_key}``,
and the ``.env`` key removed — in an order that is safe against interruption:

1. plaintext backup of every file about to change (``backup-<ts>/`` under
   ``<profile>/connector-secrets/``, never auto-deleted);
2. store entries written (fail-closed — a backend failure skips that server,
   leaving its plaintext value and config reference untouched);
3. ``config.yaml`` references rewritten (atomic save);
4. the migrated ``.env`` keys removed;
5. the migration marker written.

Interrupted anywhere between 2 and 5: the next run sees the still-plaintext
config reference, the store entry already exists (rewritten idempotently), and
the run resumes — at worst a second backup directory. A full completion leaves
zero plaintext occurrences outside the backup directory.

Explicitly out of scope: unrelated ``.env`` keys (model providers, messaging
platforms...) are never read, moved or removed — only env vars referenced by
``mcp_servers`` entries with a CX3-shaped credential slot (a bare ``${VAR}``
env value or an ``Authorization: Bearer ${VAR}`` header). MCP OAuth token
files are migrated lazily by their own adapter (CX4,
``connectors.oauth_secure_store``) whose contract keeps its files byte-identical
while that flag is off; this module only offers the shared legacy-lookup seam
used as decrypt-failure fallback.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("agent.connector_secrets")

# The one CX3-owned credential field per server (hermes_cli/mcp_config.py).
MIGRATION_FIELD = "api_key"

_MARKER_VERSION = 1

# Per-process attempt cache keyed by profile home (hermes_constants.
# hermes_home_key): MCP config loads repeat per discovery pass, so the real
# check (store availability probe) must run at most once per profile per
# process; afterwards the hook is a set-membership test.
_ATTEMPTED_HOMES: Set[str] = set()


def _marker_path(home: Path) -> Path:
    return home / "connector-secrets" / "migration.json"


def _store_base(home: Path) -> Path:
    return home / "connector-secrets"


def _load_marker(home: Path) -> Optional[dict]:
    try:
        data = json.loads(_marker_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_marker(home: Path, migrated: List[dict], backup_dir: str) -> None:
    marker = {
        "version": _MARKER_VERSION,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "backup_dir": backup_dir,
        # (server, field) -> env var the secret was migrated from; powers the
        # decrypt-failure fallback. Values themselves never appear here.
        "migrated": {f"{m['server']}/{m['field']}": m["env_key"] for m in migrated},
    }
    path = _marker_path(home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(".migration.json.tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _bare_env_ref(value: Any) -> Optional[str]:
    """``VAR`` when *value* is exactly ``${VAR}`` (CX3's own write shapes)."""
    if not (isinstance(value, str) and value.startswith("${") and value.endswith("}")):
        return None
    name = value[2:-1]
    if name.startswith("connector:"):
        return None
    return name if name.isidentifier() else None


def _strip_auth_scheme(auth: str) -> str:
    body = auth.strip()
    for prefix in ("bearer ", "token ", "basic "):
        if body.lower().startswith(prefix):
            return body[len(prefix):].strip()
    return body


def _credential_env_refs(server_config: Dict[str, Any]) -> List[str]:
    """Env var names referenced by credential slots a CX3 write would own.

    A credential slot is (a) an ``env`` block value that is a bare ``${VAR}``
    reference, or (b) an ``Authorization: <scheme> ${VAR}`` header. Bare env
    references elsewhere (command, args, urls) are the server's own plumbing
    (PATH etc.) and are deliberately NOT migrated.
    """
    refs: List[str] = []
    env_block = server_config.get("env")
    if isinstance(env_block, dict):
        for value in env_block.values():
            name = _bare_env_ref(value)
            if name is not None:
                refs.append(name)
    headers = server_config.get("headers")
    if isinstance(headers, dict):
        auth = headers.get("Authorization")
        if isinstance(auth, str):
            name = _bare_env_ref(_strip_auth_scheme(auth))
            if name is not None:
                refs.append(name)
    return refs


def _read_env_values_at(env_file: Path) -> Dict[str, str]:
    """Parse one ``.env`` file into a flat dict (quotes stripped; comments and
    non-assignments skipped)."""
    values: Dict[str, str] = {}
    try:
        text = env_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _secure_backup_dir(path: Path) -> None:
    """Same protection story as the store root (managed/NixOS group-share mode
    via the canonical securer, plain 0700 as the fallback)."""
    try:
        from hermes_cli.config import _secure_dir
        _secure_dir(path)
    except Exception:
        pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _backup_files(home: Path, backup_dir: Path) -> bool:
    """Copy the plaintext files migration may change into *backup_dir*.

    Returns True when at least one file was backed up. ``copy2`` keeps the
    original file's protection; the directory itself is secured above.
    """
    copied = False
    for name in (".env", "config.yaml"):
        src = home / name
        if src.is_file():
            shutil.copy2(src, backup_dir / name)
            copied = True
    return copied


def maybe_migrate_connector_secrets() -> None:
    """MCP-config-load hook: run the one-time migration when due (CX2b).

    Never raises; at most one real attempt per profile home per process.
    """
    try:
        from hermes_constants import get_hermes_home, hermes_home_key
        home = Path(get_hermes_home())
        key = hermes_home_key(home)
        if key in _ATTEMPTED_HOMES:
            return
        _ATTEMPTED_HOMES.add(key)
        if _marker_path(home).exists():
            return
        migrate_connector_secrets_once(home)
    except Exception:
        logger.debug("Connector secret migration skipped", exc_info=True)


def migrate_connector_secrets_once(home: Path) -> bool:
    """Run the one-time migration under *home* (already resolved by the hook).

    No-op when the marker exists. Never raises: every failure logs a
    value-free warning and skips that item, leaving the legacy plaintext path
    fully working. Returns True when a migration pass ran to completion.
    """
    if _load_marker(home) is not None or _marker_path(home).exists():
        return False

    try:
        from agent.connector_secrets import get_connector_secret_store
        store = get_connector_secret_store()
    except Exception:
        return False
    if store is None:
        # Store disabled or unusable: legacy behaviour, nothing to migrate.
        return False

    try:
        from hermes_cli.config import read_raw_config
        raw = read_raw_config()
        raw_servers = raw.get("mcp_servers")
        if not isinstance(raw_servers, dict) or not raw_servers:
            _write_marker(home, [], "")
            return True
    except Exception as exc:
        logger.warning("Connector secret migration skipped: could not read config (%s)",
                       exc.__class__.__name__)
        return False

    env_values = _read_env_values_at(home / ".env")
    migrated: List[Dict[str, str]] = []
    changed_servers: Dict[str, Dict[str, Any]] = {}
    touched_env_keys: Set[str] = set()

    for server, server_config in raw_servers.items():
        if not isinstance(server_config, dict):
            continue
        # Collect first so one server with two slots referencing the same env
        # var is migrated as one store entry per referenced server name.
        seen_vars: Set[str] = set()
        for env_name in _credential_env_refs(server_config):
            if env_name in seen_vars:
                continue
            seen_vars.add(env_name)
            value = env_values.get(env_name)
            if not value:
                continue  # not present in .env (process-env-only or already gone)
            from agent.connector_secrets import ConnectorSecretError
            try:
                store.store_secret(server, MIGRATION_FIELD, value)
            except ConnectorSecretError as exc:
                logger.warning("Connector secret migration skipped '%s' (%s); keeping the "
                               "plaintext credential", server, exc.__class__.__name__)
                continue
            entry = changed_servers.get(server)
            if entry is None:
                entry = json.loads(json.dumps(server_config))  # deep copy of the raw entry
                changed_servers[server] = entry
            _rewrite_refs(entry, env_name, server)
            migrated.append({"server": server, "field": MIGRATION_FIELD, "env_key": env_name})
            touched_env_keys.add(env_name)

    if not migrated:
        _write_marker(home, [], "")
        return True

    # Backup BEFORE any on-disk rewrite, containing exactly the pre-migration
    # plaintext bytes of every file this pass changes.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = _store_base(home) / f"backup-{stamp}"
    try:
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _secure_backup_dir(backup_dir)
        backed_up = _backup_files(home, backup_dir)
    except OSError as exc:
        logger.warning("Connector secret migration aborted: backup failed (%s) — plaintext "
                       "files left unchanged", exc.__class__.__name__)
        _rollback_store_entries(store, migrated)
        return False
    if not backed_up:
        logger.warning("Connector secret migration aborted: no plaintext files found to back "
                       "up — plaintext files left unchanged")
        _rollback_store_entries(store, migrated)
        return False

    # 3. rewrite config.yaml references (the canonical save path restores
    # every other ${VAR} template from the raw config and strips defaults)
    try:
        from hermes_cli.config import load_config, save_config
        config = load_config()
        new_servers = config.get("mcp_servers")
        if not isinstance(new_servers, dict):
            new_servers = {}
            config["mcp_servers"] = new_servers
        for server, entry in changed_servers.items():
            new_servers[server] = entry
        save_config(config)
    except Exception as exc:
        logger.warning("Connector secret migration: config rewrite failed (%s) — .env keys "
                       "kept; retry on next load", exc.__class__.__name__)
        _rollback_store_entries(store, migrated)
        return False

    # 4. remove migrated .env keys (the backup retains the original bytes)
    for key in sorted(touched_env_keys):
        try:
            _remove_env_key(home / ".env", key)
        except OSError as exc:
            logger.warning("Connector secret migration: .env cleanup failed for one key (%s)"
                           " — backup retains the original bytes", exc.__class__.__name__)

    # 5. marker last
    _write_marker(home, migrated, backup_dir.name)
    logger.info("Connector secret migration complete: %d credential(s) moved into the "
                "encrypted store; plaintext backup in %s", len(migrated), backup_dir.name)
    return True


def _rewrite_refs(entry: Dict[str, Any], env_name: str, server: str) -> None:
    """Replace ``${env_name}`` credential slots in one server entry with the
    connector reference (both the env block and the Authorization header)."""
    from agent.connector_secrets import connector_secret_reference
    ref = connector_secret_reference(server, MIGRATION_FIELD)
    env_block = entry.get("env")
    if isinstance(env_block, dict):
        for key, value in list(env_block.items()):
            if value == f"${{{env_name}}}":
                env_block[key] = ref
    headers = entry.get("headers")
    if isinstance(headers, dict):
        auth = headers.get("Authorization")
        if isinstance(auth, str) and _strip_auth_scheme(auth) == f"${{{env_name}}}":
            scheme = auth.strip()[:len(auth.strip()) - len(_strip_auth_scheme(auth))]
            headers["Authorization"] = f"{scheme.strip()} {ref}" if scheme.strip() else ref


def _remove_env_key(env_path: Path, key: str) -> None:
    """Remove ``key`` from the .env file, preserving every other line's bytes."""
    if not env_path.is_file():
        return
    lines = env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(keepends=True)
    kept = [ln for ln in lines if not _line_defines(ln, key)]
    if len(kept) == len(lines):
        return
    tmp = env_path.with_name(".env.cxmig.tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    os.replace(tmp, env_path)


def _line_defines(line: str, key: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return False
    name = stripped[7:].strip() if stripped.startswith("export ") else stripped
    return name.split("=", 1)[0].strip() == key


def _rollback_store_entries(store: Any, migrated: List[Dict[str, str]]) -> None:
    for item in migrated:
        try:
            store.delete_secret(item["server"], item["field"])
        except Exception:
            logger.debug("Migration rollback could not remove store entry %s/%s",
                         item["server"], item["field"])


def legacy_value_for_reference(server: str, field: str) -> Optional[str]:
    """Fallback lookup when a store entry cannot be decrypted (CX2b).

    Order: the env var recorded in the migration marker (looked up in the
    newest backup's ``.env`` — the exact pre-migration bytes — then the live
    ``.env``), else the CX3 env-key convention ``MCP_<SERVER>_API_KEY`` in the
    same files. Values are only returned, never logged.
    """
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:
        return None

    env_key: Optional[str] = None
    marker = _load_marker(home)
    if marker is not None:
        recorded = (marker.get("migrated") or {}).get(f"{server}/{field}")
        if isinstance(recorded, str) and recorded:
            env_key = recorded

    candidates: List[Path] = []
    store = _store_base(home)
    if store.is_dir():
        for backup in sorted(store.glob("backup-*"), reverse=True):
            env_file = backup / ".env"
            if env_file.is_file():
                candidates.append(env_file)
    live = home / ".env"
    if live.is_file():
        candidates.append(live)

    for env_file in candidates:
        values = _read_env_values_at(env_file)
        lookup = env_key or _legacy_key_guess(values, server)
        if lookup is not None:
            val = values.get(lookup)
            if val:
                return val
    return None


def _legacy_key_guess(values: Dict[str, str], server: str) -> Optional[str]:
    """The CX3 env-key convention ``MCP_<UPPER_SERVER>_API_KEY`` when present."""
    suffix = re.sub(r"[^A-Za-z0-9_]", "_", server.upper()).strip("_")
    guess = f"MCP_{suffix}_API_KEY"
    return guess if guess in values else None
