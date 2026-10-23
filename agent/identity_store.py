"""Opt-in, profile-owned individual identity (separate from DB provenance)."""
import hashlib
import logging
import os
from pathlib import Path
import platform
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from hermes_state_schema import ensure_identity_schema

logger = logging.getLogger(__name__)
IDENTITY_VERSION = 1


def _owner_fingerprint(home: Path) -> str:
    # Resolve aliases and Windows case differences; never persist the raw path/host.
    owner = os.path.normcase(str(home.resolve())) + '\0' + platform.node()
    return hashlib.sha256(owner.encode('utf-8')).hexdigest()


def initialize_identity(home: Path, *, identity_version: int = IDENTITY_VERSION) -> dict:
    """Atomically initialize/upgrade an identity; conflicts never expose a usable UUID.

    A moved/copied profile requires explicit future migration resolution. This API
    intentionally cannot rebind ownership or replace an existing identity.
    """
    if type(identity_version) is not int or identity_version < 1:
        raise ValueError('identity_version must be a positive integer')
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    fingerprint = _owner_fingerprint(home)
    with closing(sqlite3.connect(home / 'identity.db', timeout=15)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        ensure_identity_schema(db)
        row = db.execute('SELECT * FROM individual_identity WHERE singleton = 1').fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            db.execute('INSERT INTO individual_identity VALUES (1, ?, ?, ?, ?)',
                       (str(uuid4()), now, identity_version, fingerprint))
            db.execute('INSERT INTO identity_history (identity_version, changed_at) VALUES (?, ?)',
                       (identity_version, now))
        elif row['owner_fingerprint'] != fingerprint or row['identity_version'] > identity_version:
            db.rollback()
            reason = ('profile ownership mismatch (copied/moved HOME or host)'
                      if row['owner_fingerprint'] != fingerprint else 'identity version is newer than this runtime')
            logger.error('Identity conflict: %s; identity disabled. Preserve the original profile; '
                         'explicit migration resolution is required.', reason)
            return {'status': 'conflict', 'individual_id': None, 'reason': reason}
        elif row['identity_version'] < identity_version:
            db.execute('INSERT INTO identity_history (identity_version, changed_at) VALUES (?, ?)',
                       (identity_version, now))
            db.execute('UPDATE individual_identity SET identity_version = ? WHERE singleton = 1',
                       (identity_version,))
        result = dict(db.execute('SELECT individual_id, created_at, identity_version '
                                 'FROM individual_identity WHERE singleton = 1').fetchone())
        db.commit()
    return {'status': 'ready', **result}
