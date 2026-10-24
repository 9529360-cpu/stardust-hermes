"""Opt-in bounded lifecycle ledger. No prompts, tool arguments or exception text.

SQLite WAL + FULL commits survive abrupt process death. Records are immutable;
retention removes oldest records beyond 4096 (including their idempotency keys).
A 16 MiB database ceiling and per-commit WAL truncation bound disk use. Busy or
failed storage drops the event with WARNING rather than failing the caller.
"""
import atexit
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

logger = logging.getLogger(__name__)
MAX_EVENTS = 4096
MAX_PAGES = 4096
MAX_PAYLOAD_BYTES = 2048
MAX_WAL_BYTES = 1024 * 1024
_ALLOWED_FIELDS = {'session_id', 'task_id', 'process_id', 'outcome', 'identity_version'}
_REGISTRY = {}
_LOCK = threading.Lock()


class LifeEventLog:
    def __init__(self, home, identity_uuid=None):
        self.path = Path(home) / 'life_events.db'
        self.identity_uuid = identity_uuid

    def emit(self, event_type, payload=None, *, event_id=None):
        """Commit once per retained event_id; reject oversize/unapproved payloads."""
        try:
            data = {k: v for k, v in (payload or {}).items() if k in _ALLOWED_FIELDS}
            if any(not isinstance(v, (str, int, bool, type(None))) for v in data.values()):
                raise ValueError('unsupported lifecycle value')
            if len(json.dumps(data).encode('utf-8')) > MAX_PAYLOAD_BYTES:
                raise ValueError('lifecycle payload too large')
            if event_type not in {
                'process_started', 'process_stopped', 'identity_loaded',
                'session_started', 'session_ended', 'task_started',
                'task_finished', 'task_failed',
            }:
                raise ValueError('unsupported lifecycle type')
            # A read-only file must fail even on platforms where a live WAL exists.
            if self.path.exists() and not self.path.stat().st_mode & 0o222:
                raise PermissionError('read-only lifecycle database')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=0)) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA synchronous=FULL')
                db.execute(f'PRAGMA max_page_count={MAX_PAGES}')
                db.execute('PRAGMA journal_size_limit=0')
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                db.execute('BEGIN IMMEDIATE')
                wal = self.path.with_name(self.path.name + '-wal')
                if wal.exists() and wal.stat().st_size >= MAX_WAL_BYTES:
                    raise OSError('lifecycle WAL capacity reached')
                db.execute('CREATE TABLE IF NOT EXISTS life_events ('
                           'event_id TEXT PRIMARY KEY, timestamp_ns INTEGER UNIQUE NOT NULL, '
                           'record TEXT NOT NULL)')
                event_id = event_id or str(uuid4())
                if db.execute('SELECT 1 FROM life_events WHERE event_id=?', (event_id,)).fetchone():
                    db.rollback()
                    return True
                previous = db.execute('SELECT MAX(timestamp_ns) FROM life_events').fetchone()[0] or 0
                stamp = max(time.time_ns(), previous + 1)
                record = {'event_id': event_id, 'timestamp_ns': stamp,
                          'timestamp': datetime.fromtimestamp(stamp / 1e9, timezone.utc).isoformat(),
                          'identity_uuid': self.identity_uuid, 'event_type': event_type, 'payload': data}
                db.execute('INSERT INTO life_events VALUES (?, ?, ?)',
                           (event_id, stamp, json.dumps(record, separators=(',', ':'))))
                db.execute('DELETE FROM life_events WHERE timestamp_ns IN ('
                           'SELECT timestamp_ns FROM life_events ORDER BY timestamp_ns DESC LIMIT -1 OFFSET ?)',
                           (MAX_EVENTS,))
                db.commit()
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return True
        except Exception as exc:
            # Error messages can contain profile paths or user values; log only class.
            logger.warning('Life event write failed (%s); continuing', type(exc).__name__)
            return False


def initialize_agent_log(agent, config):
    section = config.get('digital_life', {})
    if not isinstance(section, dict) or section.get('event_log_enabled', False) is not True:
        return
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home().resolve()
        identity = getattr(agent, 'identity', None) or {}
        identity_uuid = identity.get('individual_id') if identity.get('status') == 'ready' else None
        key = (os.getpid(), str(home))
        with _LOCK:
            ledger = _REGISTRY.get(key)
            if ledger is None:
                ledger = LifeEventLog(home, identity_uuid)
                _REGISTRY[key] = ledger
                ledger.emit('process_started', {'process_id': os.getpid()})
                atexit.register(ledger.emit, 'process_stopped', {'process_id': os.getpid()})
        agent._life_event_log = ledger
        agent._life_event_session_closed = False
        if identity_uuid:
            ledger.emit('identity_loaded', {'session_id': agent.session_id,
                                          'identity_version': identity.get('identity_version')})
        emit_agent_event(agent, 'session_started')
    except Exception as exc:
        logger.warning('Life event initialization failed (%s); continuing', type(exc).__name__)


def emit_agent_event(agent, event_type, **payload):
    ledger = getattr(agent, '_life_event_log', None)
    if ledger is not None:
        ledger.emit(event_type, {'session_id': getattr(agent, 'session_id', None), **payload})


def close_agent_log(agent):
    if getattr(agent, '_life_event_log', None) is not None and not getattr(agent, '_life_event_session_closed', False):
        agent._life_event_session_closed = True
        emit_agent_event(agent, 'session_ended')
