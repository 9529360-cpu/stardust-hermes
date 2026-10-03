"""Running-process checkpoint persistence and PID-safe recovery."""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from agent.redact import redact_sensitive_text

logger = logging.getLogger("tools.process_registry")


class ProcessCheckpointMixin:
    # ----- Checkpoint (crash recovery) -----

    def _write_checkpoint(self, extra_entries: Optional[List[Dict[str, Any]]] = None):
        """Write running process metadata to the checkpoint file atomically."""
        from tools.process_registry import _checkpoint_path, _CHECKPOINT_FIELDS
        from hermes_constants import hermes_home_key

        owner_home = hermes_home_key()

        try:
            with self._lock:
                entries = []
                for s in self._running.values():
                    if s.exited or s.owner_home != owner_home:
                        continue
                    # Backfill the start time so recovery can detect PID recycling
                    # even for sessions spawned before this field existed.
                    if s.host_start_time is None and s.pid_scope == "host" and s.pid:
                        s.host_start_time = self._safe_host_start_time(s.pid)
                    entry = {"session_id": s.id, **{f: getattr(s, f) for f in _CHECKPOINT_FIELDS}}
                    # Redact inline credentials before persisting (~/.hermes/processes.json).
                    # Recovery uses command only for display (adoption re-validates the
                    # PID, never re-runs it), so masking is lossless.
                    # See #77484.
                    entry["command"] = redact_sensitive_text(s.command, code_file=True)
                    entry["owner_task_id"] = s.owner_task_id or s.task_id
                    entries.append(entry)
                if extra_entries:
                    tracked_ids = {item.get("session_id") for item in entries}
                    entries.extend(
                        item for item in extra_entries
                        if item.get("session_id") not in tracked_ids
                        and (not item.get("owner_home") or item["owner_home"] == owner_home)
                    )
            from utils import atomic_json_write
            atomic_json_write(_checkpoint_path(), entries)
        except Exception as e:
            logger.debug("Failed to write checkpoint file: %s", e, exc_info=True)

    def recover_from_checkpoint(self) -> int:
        """On gateway startup, probe PIDs from the checkpoint file; returns how many
        were recovered as detached sessions."""
        from tools.process_registry import (
            ProcessSession, _CHECKPOINT_FIELDS, _checkpoint_path,
            _CHECKPOINT_DEFAULTS, _WATCHER_ROUTE_KEYS, _stop_systemd_unit,
        )

        from hermes_constants import get_hermes_home, hermes_home_key, profile_name_for_home
        from gateway.session import profile_from_session_key_namespace

        checkpoint_path = _checkpoint_path()
        owner_home = hermes_home_key()
        current_profile = profile_name_for_home(get_hermes_home())
        if current_profile is None:
            # Custom single-profile HERMES_HOME roots do not follow the standard
            # profiles/<name> layout; their legacy namespace is still agent:main.
            current_profile = "default"
        if not checkpoint_path.exists():
            return 0
        try:
            entries = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        recovered = 0
        unresolved_scope_entries: List[Dict[str, Any]] = []
        for entry in entries:
            # Old multiplexed gateways copied the global registry into EVERY
            # profile checkpoint. File location therefore cannot establish owner.
            # Only a stamped owner or the unambiguous agent:<profile>: namespace
            # may authorize adoption; API/raw and missing keys have no safe owner.
            stamped_home = entry.get("owner_home")
            if stamped_home:
                if stamped_home != owner_home:
                    continue
            else:
                parts = str(entry.get("session_key") or "").split(":")
                if len(parts) < 3 or parts[0] != "agent" or not parts[1]:
                    logger.warning("Skipping legacy process %s with ambiguous profile ownership", entry.get("session_id"))
                    continue
                if profile_from_session_key_namespace(parts[1]) != current_profile:
                    continue
            pid, pid_scope = entry.get("pid"), entry.get("pid_scope", "host")
            if not pid:
                continue
            # The registry is process-global; a multiplexer recovers each profile's
            # checkpoint into the shared registry without re-adopting the same ID.
            with self._lock:
                already_tracked = entry.get("session_id") in self._running
            if already_tracked:
                continue
            if pid_scope != "host":  # in-sandbox PIDs mean nothing once the env handle is gone
                logger.info(
                    "Skipping recovery for non-host process: %s (pid=%s, scope=%s)",
                    entry.get("command", "unknown")[:60], pid, pid_scope)
                continue
            # Alive AND the same process: across a restart the kernel may have
            # recycled the PID onto a stranger, and adopting it would let a later
            # kill tree-kill e.g. a browser.
            if not self._host_pid_is_ours(pid, entry.get("host_start_time")):
                if self._is_host_pid_alive(pid):
                    logger.info(
                        "Not recovering session %s: pid %d is alive but its "
                        "start time no longer matches — PID was recycled onto "
                        "an unrelated process; refusing to adopt it.",
                        entry.get("session_id", "?"), pid)
                systemd_unit = entry.get("systemd_unit", "")
                if systemd_unit and not _stop_systemd_unit(systemd_unit):
                    logger.warning(
                        "Could not reap persisted scope %s for dead wrapper pid %s; "
                        "retaining checkpoint entry for the next startup",
                        systemd_unit, pid)
                    unresolved_scope_entries.append(entry)
                continue
            fields = {f: entry.get(f, _CHECKPOINT_DEFAULTS[f]) for f in _CHECKPOINT_FIELDS}
            fields.update(
                owner_home=owner_home,
                command=entry.get("command", "unknown"),
                owner_task_id=entry.get("owner_task_id", "") or entry.get("task_id", ""),
                started_at=entry.get("started_at", time.time()))
            # detached: can't read output, but can report status + kill
            session = ProcessSession(id=entry["session_id"], detached=True, **fields)
            with self._lock:
                self._running[session.id] = session
            recovered += 1
            logger.info("Recovered detached process: %s (pid=%d)", session.command[:60], pid)
            # Re-enqueue watcher so gateway can resume notifications
            if session.watcher_interval > 0:
                with self._lock:
                    self.pending_watchers.append({
                        "session_id": session.id,
                        "owner_home": session.owner_home,
                        "check_interval": session.watcher_interval,
                        "session_key": session.session_key,
                        **{key: getattr(session, f"watcher_{key}") for key in _WATCHER_ROUTE_KEYS},
                        "notify_on_complete": session.notify_on_complete,
                        "parent_session_id": session.parent_session_id,
                    })
        self._write_checkpoint(extra_entries=unresolved_scope_entries)
        return recovered
