"""Opt-in bounded reference snapshot of existing session task state; never dispatches work."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
KEY = '_task_checkpoint_v1'
OWNER_KEY = '_task_checkpoint_owner'
MAX_BYTES = 4096
HEADER = '[Task checkpoint — snapshot reference only]'
_CONTINUE = re.compile(r'(?i)^\s*(?:continue\b|resume\b|继续|接着)')


def enabled():
    from hermes_cli.config import load_config_readonly
    try:
        config = load_config_readonly()
        return (config.get('agent') or {}).get('task_checkpoint') is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def scope():
    from hermes_constants import get_hermes_home
    from pathlib import Path
    from hermes_cli.profiles import get_active_profile_name
    values = [str(get_hermes_home().resolve()), str(Path.home().resolve()),
              os.getenv('STARDUST_HOME', ''), get_active_profile_name()]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _read(agent, key):
    return agent._session_db.get_session_model_config_value(agent.session_id, key)


def _task(agent):
    from agent.task_acceptance import load
    contract = load(agent.session_id)
    return contract if contract and contract.get('active') else None


def reason(agent, snapshot):
    """Fail closed on foreign scope, owner, unsupported versions or changed source revision."""
    from tools.todo_tool import load_todo_session_state
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
        return 'unsupported or missing checkpoint'
    if snapshot.get('scope') != scope():
        return 'HOME/profile scope mismatch'
    if snapshot.get('session_id') != agent.session_id:
        return 'session owner mismatch'
    if snapshot.get('suspended'):
        return 'new user goal; checkpoint suspended'
    owner = _read(agent, OWNER_KEY)
    contract = _task(agent)
    if not isinstance(owner, dict) or snapshot.get('task_id') != owner.get('task_id'):
        return 'task owner mismatch'
    if owner.get('suspended'):
        return 'new user goal; checkpoint suspended'
    if owner.get('scope') != scope():
        return 'HOME/profile scope mismatch'
    if snapshot.get('contract_id') and (not contract or contract['created_at'] != snapshot['contract_id']):
        return 'task owner mismatch'
    state = load_todo_session_state(agent._session_db, agent.session_id)
    if state and state['revision'] != snapshot.get('todo_revision'):
        return 'stale todo revision'
    return ''


def _clip(value, limit=120):
    return str(value)[:limit]


def _observations(agent, contract):
    if not contract:
        return []
    from agent.verification_evidence import _db_path
    if not _db_path().is_file():
        return []
    with sqlite3.connect(f'{_db_path().as_uri()}?mode=ro', uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='acceptance_observations'").fetchone():
            return []
        rows = conn.execute('SELECT observation_id FROM acceptance_observations '
                            'WHERE session_id=? AND contract_id=? ORDER BY rowid DESC LIMIT 8',
                            (agent.session_id, contract['created_at'])).fetchall()
    return [row[0] for row in rows]


def _evidence_ids(agent, contract):
    if not contract:
        return []
    from agent.verification_evidence import _db_path
    if not _db_path().is_file():
        return []
    with sqlite3.connect(f'{_db_path().as_uri()}?mode=ro', uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='verification_events'").fetchone():
            return []
        rows = conn.execute('SELECT id FROM verification_events WHERE session_id=? AND created_at>=? '
                            'ORDER BY id DESC LIMIT 8',
                            (agent.session_id, (contract or {}).get('created_at', ''))).fetchall()
    return [f'verification:{row[0]}' for row in rows]


def persist(agent):
    """Reconcile only durable todo state and committed ledger references after transcript flush."""
    if not enabled() or getattr(agent, '_persist_disabled', False) or not getattr(agent, '_session_db', None):
        return
    from tools.todo_tool import load_todo_session_state, TodoStore
    try:
        owner = _read(agent, OWNER_KEY)
        if not isinstance(owner, dict) or owner.get('suspended') or owner.get('scope') != scope():
            return
        state = load_todo_session_state(agent._session_db, agent.session_id)
        if not state or state['revision'] < owner.get('min_todo_revision', 0):
            return
        contract = _task(agent)
        if owner.get('contract_id') and (not contract or contract['created_at'] != owner['contract_id']):
            return
        items = state['todos']
        pending = [v for v in items if v['status'] in {'pending', 'in_progress'}]
        ready = [v for v in pending if not TodoStore.dependency_blockers(v, items)]
        next_item = next((v for v in ready if v['status'] == 'in_progress'), ready[0] if ready else None)
        snapshot = dict(version=1, snapshot_at=datetime.now(timezone.utc).isoformat(),
                        scope=scope(), session_id=agent.session_id, task_id=owner['task_id'],
                        contract_id=owner.get('contract_id'), todo_revision=state['revision'],
                        counts={status: sum(v['status'] == status for v in items)
                                for status in ('completed', 'pending', 'in_progress', 'cancelled')},
                        active=[{'id': _clip(v['id'], 48), 'status': v['status']} for v in pending[:8]],
                        next_step=({'id': _clip(next_item['id'], 48), 'text': _clip(next_item['content'])}
                                   if next_item else None),
                        criteria=[{'id': _clip(v['id'], 48), 'tier': v['tier'], 'text': _clip(v['text'], 80)}
                                  for v in (contract or {}).get('criteria', [])[:8]],
                        observation_ids=_observations(agent, contract), evidence_ids=_evidence_ids(agent, contract),
                        blockers=[_clip(v) for v in (contract or {}).get('blockers', [])[:4]],
                        dependency_blockers=[{'id': _clip(v['id'], 48),
                                              'waiting_for': [_clip(i, 48) for i in TodoStore.dependency_blockers(v, items)[:4]]}
                                             for v in pending[:4] if TodoStore.dependency_blockers(v, items)])
        # IDs and counts remain authoritative references; omit optional tails to enforce a byte budget.
        for key in ('dependency_blockers', 'blockers', 'active', 'criteria', 'observation_ids', 'evidence_ids'):
            while len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_BYTES and snapshot[key]:
                snapshot[key].pop()
        if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_BYTES:
            logger.warning('Task checkpoint omitted: identity exceeds byte budget')
            return
        agent._session_db.patch_session_model_config(agent.session_id, {KEY: snapshot})
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        logger.warning('Task checkpoint unavailable: %s', type(exc).__name__)


def reference(agent):
    if not enabled() or not getattr(agent, '_session_db', None):
        return ''
    try:
        snapshot = _read(agent, KEY)
        if not snapshot:
            return ''
        why = reason(agent, snapshot)
        if why:
            return f'\n{HEADER}\nNot applied: {why}.\n'
        return (f'\n{HEADER}\nSnapshot, not fresh authorization; no autonomous dispatch. '
                'Use current user input to decide whether to continue. Completed counts are todo status, '
                'not proof of acceptance. Resolve IDs in session/acceptance state before relying on them.\n'
                + json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')) + '\n')
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        logger.warning('Task checkpoint recall unavailable: %s', type(exc).__name__)
        return ''


def carry(agent, parent_id, child_id, child_config):
    """Explicit compression lineage only; never search other sessions for a checkpoint."""
    if not enabled() or not getattr(agent, '_session_db', None):
        return
    try:
        owner = agent._session_db.get_session_model_config_value(parent_id, OWNER_KEY)
        snapshot = agent._session_db.get_session_model_config_value(parent_id, KEY)
        if isinstance(owner, dict) and owner.get('scope') == scope():
            child_config[OWNER_KEY] = owner
            if isinstance(snapshot, dict) and not reason(agent, snapshot):
                child_config[KEY] = dict(snapshot, session_id=child_id,
                                        source_session_id=snapshot.get('source_session_id', parent_id))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        logger.warning('Task checkpoint carry unavailable: %s', type(exc).__name__)


def prepare(agent, request):
    """Current user input alone determines continuity; history/tool output never does."""
    if not enabled() or getattr(agent, '_persist_disabled', False) or not getattr(agent, '_session_db', None):
        return ''
    if not isinstance(request, str):
        return ''
    try:
        owner = _read(agent, OWNER_KEY)
        if owner and _CONTINUE.match(request):
            # A suspended goal is not resurrected by an ambiguous continuation.
            return reference(agent)
        contract = _task(agent)
        identity = ('contract:' + contract['created_at']) if contract else hashlib.sha256(request.encode()).hexdigest()
        if owner and owner.get('task_id') == identity and owner.get('scope') == scope():
            return reference(agent)
        if owner:
            old_snapshot = _read(agent, KEY)
            from tools.todo_tool import load_todo_session_state
            state = load_todo_session_state(agent._session_db, agent.session_id) or {'revision': 0}
            patch = {OWNER_KEY: {'task_id': identity, 'scope': scope(),
                                'contract_id': contract['created_at'] if contract else None,
                                'min_todo_revision': state['revision'] + 1,
                                'suspended': False}}
            if isinstance(old_snapshot, dict):
                patch[KEY] = dict(old_snapshot, suspended=True)
            agent._session_db.patch_session_model_config(agent.session_id, patch)
            return reference(agent)
        agent._session_db.patch_session_model_config(agent.session_id, {OWNER_KEY: {
            'task_id': identity, 'scope': scope(), 'contract_id': contract['created_at'] if contract else None,
            'suspended': False}})
        return ''
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        logger.warning('Task checkpoint preparation unavailable: %s', type(exc).__name__)
        return ''
