"""Explicit, opt-in recovery journal. Unknown effects are never dispatched again."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

KEY = '_task_recovery_v1'
VERSION = 1
MAX_STEPS = 256
_LOCK = threading.RLock()


def enabled():
    from hermes_cli.config import load_config_readonly
    try:
        config = load_config_readonly()
        return (config.get('digital_life') or {}).get('recovery_enabled') is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def auto_resume_enabled():
    """DL-P2.5 gate: whitelisted goal auto-resume after restart (default OFF)."""
    from hermes_cli.config import load_config_readonly
    try:
        config = load_config_readonly()
        return (config.get('digital_life') or {}).get('auto_resume') is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _available(agent):
    return enabled() and not getattr(agent, '_persist_disabled', False) and getattr(agent, '_session_db', None) is not None


def _identity(agent):
    identity = getattr(agent, 'identity', None)
    if identity is None:
        from agent.identity_store import initialize_identity
        from hermes_constants import get_hermes_home
        identity = initialize_identity(get_hermes_home())
    if identity.get('status') != 'ready' or not identity.get('individual_id'):
        raise ValueError('identity unavailable')
    return identity['individual_id']


def _owner(agent):
    from agent.task_checkpoint import scope
    return {'scope': scope(), 'identity': _identity(agent), 'session_id': agent.session_id}


def _load(agent):
    return agent._session_db.get_session_model_config_value(agent.session_id, KEY)


def _save(agent, record):
    agent._session_db.patch_session_model_config(agent.session_id, {KEY: record})


def _ref(name, args):
    return hashlib.sha256(json.dumps([name, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _validate_owner_only(record, task_id):
    """Validate a journal record's shape and owner scope without a live agent."""
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != VERSION:
        raise ValueError('corrupt or unsupported checkpoint version')
    from agent.task_checkpoint import scope
    if record.get('scope') != scope():
        raise ValueError('HOME/profile scope mismatch')
    if not isinstance(task_id, str) or not task_id or record.get('task_id') != task_id:
        raise ValueError('task mismatch')
    steps = record.get('steps')
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError('corrupt step list')
    calls = set()
    for index, step in enumerate(steps, 1):
        if (not isinstance(step, dict) or type(step.get('step')) is not int or step['step'] != index
                or step.get('task_id') != task_id or not isinstance(step.get('intent'), str)
                or not isinstance(step.get('call_id'), str) or step['call_id'] in calls
                or not isinstance(step.get('side_effect_ref'), str) or len(step['side_effect_ref']) != 64
                or step.get('status') not in {'started', 'committed'}
                or 'committed_at' not in step
                or (step['status'] == 'committed' and not isinstance(step['committed_at'], str))
                or (step['status'] == 'started' and step['committed_at'] is not None)):
            raise ValueError('corrupt step')
        calls.add(step['call_id'])
    return record


def _validate(agent, record, task_id):
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != VERSION:
        raise ValueError('corrupt or unsupported checkpoint version')
    if any(record.get(k) != v for k, v in _owner(agent).items()):
        raise ValueError('HOME/profile/identity/session mismatch')
    if not isinstance(task_id, str) or not task_id or record.get('task_id') != task_id:
        raise ValueError('task mismatch')
    steps = record.get('steps')
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError('corrupt step list')
    calls = set()
    for index, step in enumerate(steps, 1):
        if (not isinstance(step, dict) or type(step.get('step')) is not int or step['step'] != index
                or step.get('task_id') != task_id or not isinstance(step.get('intent'), str)
                or not isinstance(step.get('call_id'), str) or step['call_id'] in calls
                or not isinstance(step.get('side_effect_ref'), str) or len(step['side_effect_ref']) != 64
                or step.get('status') not in {'started', 'committed'}
                or 'committed_at' not in step
                or (step['status'] == 'committed' and not isinstance(step['committed_at'], str))
                or (step['status'] == 'started' and step['committed_at'] is not None)):
            raise ValueError('corrupt step')
        calls.add(step['call_id'])
    return record


def reconcile(agent):
    """Recover a journal commit only from a durable executor success receipt."""
    if not _available(agent) or not getattr(agent, '_recovery_task_id', None):
        return
    with _LOCK:
        record = _validate(agent, _load(agent), agent._recovery_task_id)
        pending = {s['call_id']: s for s in record['steps'] if s['status'] == 'started'}
        if not pending:
            return
        changed = False
        for message in agent._session_db.get_messages(agent.session_id):
            receipt = (message.get('display_metadata') or {}).get('task_recovery')
            step = pending.get(message.get('tool_call_id'))
            if message.get('role') != 'tool' or not step or receipt != {'version': 1, 'committed': True}:
                continue
            result = message.get('content')
            step.update(status='committed', committed_at=datetime.now(timezone.utc).isoformat(),
                        result=result if isinstance(result, str) and len(result) <= 16384 else None)
            changed = True
        if changed:
            _save(agent, record)


def find_resume_point(agent, *, task_id=None):
    """Read a checkpoint from this exact session only; this API never executes work."""
    if not _available(agent):
        return {'status': 'disabled'}
    try:
        record = _load(agent)
        task_id = task_id or getattr(agent, '_recovery_task_id', None)
        _validate(agent, record, task_id)
        if getattr(agent, '_recovery_task_id', None) == task_id:
            reconcile(agent)
            record = _load(agent)
        from agent.tool_result_classification import tool_may_have_side_effect
        pending = [s for s in record['steps'] if s['status'] == 'started' and tool_may_have_side_effect(s['intent'])]
        return {'status': 'needs_review' if pending else 'ready', 'task_id': task_id,
                'last_committed_step': max((s['step'] for s in record['steps'] if s['status'] == 'committed'), default=0),
                'pending': pending}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {'status': 'rejected', 'reason': str(exc)}


def journal_status(session_db, session_id):
    """Startup auto-resume gate: read a session's journal WITHOUT a live agent.

    ``ready`` = every step either committed or side-effect free; ``needs_review`` =
    an uncommitted side-effecting step (unknown whether the effect landed);
    ``rejected`` = foreign/corrupt journal; ``disabled`` = flag off or no journal.
    Read-only: never mutates the journal.
    """
    if not enabled() or session_db is None:
        return {'status': 'disabled'}
    try:
        record = session_db.get_session_model_config_value(session_id, KEY)
        if not isinstance(record, dict):
            return {'status': 'disabled'}
        _validate_owner_only(record, record.get('task_id'))
        from agent.tool_result_classification import tool_may_have_side_effect
        pending = [s for s in record['steps'] if s['status'] == 'started' and tool_may_have_side_effect(s['intent'])]
        return {'status': 'needs_review' if pending else 'ready', 'task_id': record['task_id'],
                'last_committed_step': max((s['step'] for s in record['steps'] if s['status'] == 'committed'), default=0),
                'pending': pending}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {'status': 'rejected', 'reason': str(exc)}


def prepare(agent, request, *, task_id=None):
    """Only literal current user continuation or an explicit API call enables recovery."""
    if not _available(agent) or not isinstance(request, str):
        return ''
    from agent.task_checkpoint import _CONTINUE
    with _LOCK:
        record = _load(agent)
        continuing = bool(_CONTINUE.fullmatch(request.strip()))
        if continuing:
            agent._recovery_task_id = task_id or (record.get('task_id') if isinstance(record, dict) else None)
            agent._recovery_resuming = True
            agent._recovery_replayed = set()
            point = find_resume_point(agent, task_id=agent._recovery_task_id)
            return '\n[Task recovery]\n' + json.dumps(point, ensure_ascii=False) + '\n'
        agent._recovery_resuming = False
        agent._recovery_task_id = task_id or hashlib.sha256(request.encode()).hexdigest()
        # New user goals may supersede valid journals, but cannot erase corrupt/foreign state.
        if record is not None:
            _validate(agent, record, record.get('task_id') if isinstance(record, dict) else None)
        _save(agent, dict(version=VERSION, **_owner(agent), task_id=agent._recovery_task_id, steps=[]))
    return ''


def start_step(agent, call_id, name, args):
    if not _available(agent):
        return
    with _LOCK:
        record = _validate(agent, _load(agent), getattr(agent, '_recovery_task_id', None))
        if len(record['steps']) >= MAX_STEPS:
            raise ValueError('recovery journal full; needs_review')
        record['steps'].append(dict(task_id=record['task_id'], step=len(record['steps']) + 1,
                                    call_id=call_id, intent=name, side_effect_ref=_ref(name, args),
                                    committed_at=None, status='started'))
        _save(agent, record)


def result_committed(name, result):
    """A returned background handle or unknown disposition is not completion."""
    from agent.tool_result_classification import file_mutation_result_landed, tool_may_have_side_effect, tool_result_outcome
    if not tool_may_have_side_effect(name):
        return True
    if file_mutation_result_landed(name, result):
        return True
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return False
    return tool_result_outcome(name, data) == 'success'


def commit_step(agent, call_id, result):
    if not _available(agent):
        return
    with _LOCK:
        record = _validate(agent, _load(agent), getattr(agent, '_recovery_task_id', None))
        for step in record['steps']:
            if step['call_id'] == call_id:
                step.update(status='committed', committed_at=datetime.now(timezone.utc).isoformat(),
                            result=result if isinstance(result, str) and len(result) <= 16384 else None)
                _save(agent, record)
                return


def before_dispatch(agent, call_id, name, args):
    """Enforce the journal at the dispatch boundary, independent of model compliance."""
    if not _available(agent):
        return None
    with _LOCK:
        point = find_resume_point(agent)
        if point['status'] == 'rejected':
            return point
        if point['status'] == 'needs_review' and getattr(agent, '_recovery_resuming', False):
            from agent.tool_result_classification import tool_may_have_side_effect
            if tool_may_have_side_effect(name):
                return point
        if getattr(agent, '_recovery_resuming', False):
            from agent.tool_result_classification import tool_may_have_side_effect
            if not tool_may_have_side_effect(name):
                return None
            fingerprint = _ref(name, args)
            replayed = getattr(agent, '_recovery_replayed', set())
            record = _load(agent)
            for step in record['steps']:
                if step['side_effect_ref'] != fingerprint or step['status'] != 'committed':
                    continue
                if not isinstance(step.get('result'), str) or step['call_id'] in replayed:
                    return {'status': 'needs_review', 'reason': 'completed effect must not be repeated'}
                replayed.add(step['call_id'])
                agent._recovery_replayed = replayed
                return {'status': 'replay', 'result': step['result']}
    return None
