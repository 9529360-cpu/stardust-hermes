"""Bounded recovery references for the existing Skill review; no new store.

Only the literal-file-search recovery has a deterministic equivalence rule.
All other apparent recoveries remain ordinary review correlations.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

TTL = 86400


def _scope():
    from hermes_constants import get_hermes_home
    return hashlib.sha256(str(get_hermes_home().resolve()).encode()).hexdigest()[:16]
_START = '<!-- stardust-recovery '
_END = '<!-- /stardust-recovery -->'
_BLOCK = re.compile(re.escape(_START) + r'([^\n]+) -->\n.*?' + re.escape(_END), re.S)


def hint_key(owner):
    return hashlib.sha256(('literal-file-search:' + owner).encode()).hexdigest()[:16]


def hint_block(owner, session, ids, observed):
    metadata = json.dumps(dict(key=hint_key(owner), scope=_scope(), active=True, expires=int(observed + TTL),
                               session=session, observations=ids, time=int(observed)), separators=(',', ':'))
    return (_START + metadata + ' -->\n'
            'For a simple literal file search, if rg is currently unavailable, try grep with the same '
            'pattern and file; verify the returned match against a committed file read. '
            'Re-probe availability; this is not a lasting claim about the environment.\n' + _END)


def render_hints(content, *, enabled, now=None):
    """Disable expired/retracted bounded hints at load, without rewriting Skill bytes."""
    if not enabled or _START not in content:
        return content
    now = time.time() if now is None else now
    def render(match):
        try:
            meta = json.loads(match[1])
            if (meta.get('active') is True and meta.get('scope') == _scope()
                    and now <= meta['expires'] <= now + TTL):
                return match[0]
        except (ValueError, TypeError, KeyError):
            pass
        return ''
    return _BLOCK.sub(render, content)


def enabled_config():
    from hermes_cli.config import load_config_readonly
    try:
        return load_config_readonly().get('auxiliary', {}).get('background_review', {}).get(
            'promote_verified_recoveries') is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and len(value) <= 16384:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _brief(agent):
    from agent import acceptance_observations, task_acceptance
    from hermes_constants import get_hermes_home
    from tools.terminal_env_recall import _invocation, _literal_search
    from agent.tool_result_classification import tool_result_outcome
    db = getattr(agent, '_session_db', None)
    session = getattr(agent, 'session_id', '')
    # Do not echo arbitrary session text in a future Skill.
    if not db or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session):
        return ''
    if Path(db.db_path).resolve().parent != get_hermes_home().resolve():
        return ''
    from hermes_cli.config import load_config_readonly
    config = load_config_readonly()
    if config.get('terminal', {}).get('backend', 'local') != 'local':
        return ''
    contract = task_acceptance.load(session)
    if not contract or not contract.get('active'):
        return ''
    with sqlite3.connect(Path(db.db_path).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = list(reversed(conn.execute(
            'SELECT id,role,content,tool_calls,tool_call_id,tool_name,timestamp FROM messages '
            'WHERE session_id=? AND active=1 ORDER BY id DESC LIMIT 240', (session,)).fetchall()))
    last_user = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]['role'] == 'user'), -1)
    rows = rows[last_user + 1:]
    calls, owner, failure, recovery = {}, None, None, None
    for row in rows:
        if row['timestamp'] < time.time() - TTL or row['timestamp'] > time.time() + 1:
            continue
        if row['role'] == 'user':
            owner = failure = recovery = None
        if row['role'] == 'assistant':
            for call in (json.loads(row['tool_calls'] or '[]'))[:32]:
                calls[call['id']] = call['function']
            continue
        if row['role'] != 'tool':
            continue
        fn = calls.pop(row['tool_call_id'], {})
        if fn.get('name') != row['tool_name']:
            continue
        args, result = _object(fn.get('arguments')), _object(row['content'])
        if row['tool_name'] == 'skill_view' and result.get('success') is True:
            name = args.get('name', '')
            owner = name if re.fullmatch(r'[A-Za-z0-9_-]{1,60}', name) else None
        elif owner and row['tool_name'] == 'terminal':
            if args.get('background') or result.get('session_id') or result.get('status') in {'running', 'background'}:
                continue
            words = _invocation(args.get('command'))
            if not words or not _literal_search(words[1:]):
                continue
            outcome = tool_result_outcome('terminal', result)
            cwd = args.get('workdir')
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                continue
            if words[0] == 'rg' and outcome == 'failed' and result.get('exit_code') == 127:
                if re.fullmatch(r'(?:[^\r\n]*: )?rg: command not found\s*', result.get('output', '')):
                    failure = (row, words[1:], cwd)
                    recovery = None
            elif words[0] == 'grep' and outcome == 'success' and failure:
                if (words[1:] == failure[1] and cwd == failure[2]
                        and row['timestamp'] - failure[0]['timestamp'] <= 300
                        and isinstance(result.get('output'), str)
                        and failure[1][0] in result['output']):
                    recovery = row
        elif owner and failure and recovery and row['tool_name'] == 'read_file':
            artifact = Path(args.get('path', '')).resolve()
            expected = (Path(failure[2]) / failure[1][1]).resolve()
            if artifact != expected or row['id'] <= recovery['id']:
                continue
            # A committed read must actually contain the literal match, not merely
            # identify an artifact. Re-resolve provenance rather than trusting labels.
            text = result.get('content')
            if not isinstance(text, str) or failure[1][0] not in text:
                continue
            for evidence in contract.get('evidence', [])[-20:]:
                if evidence.get('status') != 'passed' or evidence.get('source') not in {
                        row['tool_call_id'], f"message:{row['id']}"}:
                    continue
                provenance = acceptance_observations.resolve(contract, f"message:{row['id']}", artifact, 'runtime')
                if provenance.get('verification') != 'verified':
                    continue
                ids = [failure[0]['id'], recovery['id'], row['id']]
                block = hint_block(owner, session, ids, failure[0]['timestamp'])
                refs = ', '.join(f'message:{i}' for i in ids)
                return (f'[Verified recovery references; read-only telemetry] owner={owner}; session={session}; '
                        f'{refs}. Supported only: missing rg -> grep with identical literal/file operands '
                        '-> committed file read matching the literal. No raw arguments/output are included.\n'
                        'Use the existing Skill owner and normal write guards only. Read it first. '
                        'If its rule or this recovery key already exists, do not append or renew it. '
                        'Otherwise consider this single bounded block; do not create a Skill or memory. '
                        'Remove/disable this block on contradictory evidence; active=false retracts it.\n' + block)
    return ''


def validate_hint_write(owner, original, content):
    """One canonical block per owning Skill; arbitrary lesson text is never accepted."""
    matches = list(_BLOCK.finditer(content))
    if content.count(_START) != len(matches) or len(matches) > 1:
        return 'Recovery promotion permits at most one well-formed hint per Skill.'
    for match in matches:
        try:
            meta = json.loads(match[1])
            if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', meta.get('session', '')) is None:
                return 'Invalid recovery session identity.'
            ids = meta.get('observations')
            if (not isinstance(ids, list) or len(ids) != 3
                    or any(type(i) is not int or i <= 0 for i in ids)):
                return 'Invalid recovery observation identities.'
            canonical = hint_block(owner, meta['session'], ids, meta['time'])
            if meta.get('active') is False:
                canonical = canonical.replace('"active":true', '"active":false')
            if match[0] != canonical or len(match[0]) > 600:
                return 'Recovery hint must retain its bounded canonical text and provenance.'
        except (ValueError, TypeError, KeyError):
            return 'Invalid recovery hint metadata.'
    return None


def validate_committed_hint(owner, original, content):
    error = validate_hint_write(owner, original, content)
    if error:
        return error
    from hermes_constants import get_hermes_home
    from types import SimpleNamespace
    for match in _BLOCK.finditer(content):
        if match[0] in original or match[0].replace('"active":false', '"active":true') in original:
            continue
        if _START in original:
            return 'Existing recovery hints may be retracted or removed, never renewed by a repeat.'
        meta = json.loads(match[1])
        agent = SimpleNamespace(session_id=meta['session'],
                                _session_db=SimpleNamespace(db_path=get_hermes_home() / 'state.db'))
        if match[0] not in build_brief(agent, {'promote_verified_recoveries': True}, []):
            return 'Recovery hint has no current committed recovery chain.'
    return None


def build_brief(agent, task_cfg, messages):
    """Opt-in only. Reads committed local records; never triggers an extra review."""
    if task_cfg.get('promote_verified_recoveries') is not True:
        return ''
    if messages and not any(isinstance(m, dict) and m.get('role') == 'tool' for m in messages[-240:]):
        return ''
    try:
        return _brief(agent)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return ''
