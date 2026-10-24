"""Lifecycle durability, bounded retention and opt-in contracts."""
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from agent.life_event_log import LifeEventLog, close_agent_log, initialize_agent_log


def records(home):
    with closing(sqlite3.connect(home / 'life_events.db')) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        return [json.loads(row[0]) for row in db.execute('SELECT record FROM life_events ORDER BY timestamp_ns')]


def test_disabled_creates_no_files_or_attributes(tmp_path):
    home = tmp_path / 'absent'
    agent = SimpleNamespace()
    initialize_agent_log(agent, {'digital_life': {'event_log_enabled': False}})
    assert not vars(agent)
    assert not home.exists()


def test_durable_idempotent_records_and_privacy(tmp_path):
    ledger = LifeEventLog(tmp_path, 'individual')
    for _ in range(100):
        assert ledger.emit('task_started', {'task_id': 'task', 'session_id': 'session',
                                           'api_key': 'secret', 'prompt': 'private'}, event_id='same')
    row, = records(tmp_path)
    assert row['identity_uuid'] == 'individual'
    assert row['payload'] == {'task_id': 'task', 'session_id': 'session'}
    assert 'secret' not in json.dumps(row)
    assert LifeEventLog(tmp_path, 'individual').emit('task_finished')
    assert records(tmp_path)[1]['timestamp_ns'] > row['timestamp_ns']


def test_clock_rollback_remains_strictly_ordered(tmp_path, monkeypatch):
    ledger = LifeEventLog(tmp_path)
    monkeypatch.setattr('agent.life_event_log.time.time_ns', lambda: 1000000000)
    assert ledger.emit('process_started')
    monkeypatch.setattr('agent.life_event_log.time.time_ns', lambda: 1)
    assert ledger.emit('process_stopped')
    assert [r['timestamp_ns'] for r in records(tmp_path)] == [1000000000, 1000000001]


def test_retention_bounds_old_records(tmp_path, monkeypatch):
    monkeypatch.setattr('agent.life_event_log.MAX_EVENTS', 4)
    ledger = LifeEventLog(tmp_path)
    for i in range(30):
        assert ledger.emit('task_started', {'task_id': str(i)})
    assert [r['payload']['task_id'] for r in records(tmp_path)] == ['26', '27', '28', '29']
    assert (tmp_path / 'life_events.db').stat().st_size <= 16 * 1024 * 1024
    assert not (tmp_path / 'life_events.db-wal').exists()


def test_write_failure_warns_and_returns(tmp_path, caplog):
    ledger = LifeEventLog(tmp_path)
    assert ledger.emit('process_started')
    ledger.path.chmod(0o444)
    try:
        assert ledger.emit('task_started') is False
    finally:
        ledger.path.chmod(0o666)
    assert 'Life event write failed' in caplog.text
    assert len(records(tmp_path)) == 1


def test_busy_database_does_not_wait(tmp_path, caplog):
    ledger = LifeEventLog(tmp_path)
    assert ledger.emit('process_started')
    with sqlite3.connect(ledger.path) as db:
        db.execute('BEGIN IMMEDIATE')
        assert ledger.emit('task_started') is False
    assert 'OperationalError' in caplog.text


def test_payload_bound_and_invalid_type_fail_soft(tmp_path, caplog):
    ledger = LifeEventLog(tmp_path)
    assert not ledger.emit('task_started', {'task_id': 'x' * 3000})
    assert not ledger.emit('unknown')
    assert not ledger.path.exists()
    assert 'ValueError' in caplog.text


def test_pinned_reader_bounds_wal_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr('agent.life_event_log.MAX_WAL_BYTES', 32768)
    ledger = LifeEventLog(tmp_path)
    assert ledger.emit('process_started')
    with closing(sqlite3.connect(ledger.path)) as reader:
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM life_events').fetchall()
        results = [ledger.emit('task_started') for _ in range(30)]
        assert False in results
        assert ledger.path.with_name('life_events.db-wal').stat().st_size < 131072
    assert ledger.emit('task_finished')
    assert records(tmp_path)[-1]['event_type'] == 'task_finished'


def test_session_close_idempotent(tmp_path):
    agent = SimpleNamespace(session_id='session', _life_event_log=LifeEventLog(tmp_path))
    close_agent_log(agent)
    close_agent_log(agent)
    row, = records(tmp_path)
    assert row['event_type'] == 'session_ended'
    assert row['payload']['session_id'] == 'session'
