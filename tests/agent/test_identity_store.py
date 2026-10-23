"""Opt-in identity persistence, ownership, and version history contracts."""
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from uuid import UUID

import pytest

from agent import identity_store


def test_same_home_restarts_keep_identity(tmp_path):
    records = [identity_store.initialize_identity(tmp_path) for _ in range(10)]
    assert len({r['individual_id'] for r in records}) == 1
    assert all(r['status'] == 'ready' for r in records)
    assert UUID(records[0]['individual_id']).version == 4
    assert records[0]['created_at'] == records[-1]['created_at']
    assert records[0]['identity_version'] == 1


def test_version_changes_append_history(tmp_path):
    original = identity_store.initialize_identity(tmp_path)
    updated = identity_store.initialize_identity(tmp_path, identity_version=2)
    identity_store.initialize_identity(tmp_path, identity_version=2)
    assert updated['individual_id'] == original['individual_id']
    with closing(sqlite3.connect(tmp_path / 'identity.db')) as db:
        assert db.execute('SELECT identity_version FROM identity_history ORDER BY sequence').fetchall() == [(1,), (2,)]
    # Older code must not silently downgrade a newer identity record.
    assert identity_store.initialize_identity(tmp_path)['status'] == 'conflict'


def test_copy_is_conflict_without_overwrite(tmp_path, caplog):
    source, copied = tmp_path / 'a', tmp_path / 'b'
    original = identity_store.initialize_identity(source)
    shutil.copytree(source, copied)
    before = (copied / 'identity.db').read_bytes()
    conflict = identity_store.initialize_identity(copied)
    assert conflict['status'] == 'conflict'
    assert conflict['individual_id'] is None
    assert 'identity conflict' in caplog.text.lower()
    assert (copied / 'identity.db').read_bytes() == before
    assert identity_store.initialize_identity(source) == original


def test_host_change_is_conflict(tmp_path, monkeypatch):
    identity_store.initialize_identity(tmp_path)
    before = (tmp_path / 'identity.db').read_bytes()
    monkeypatch.setattr(identity_store.platform, 'node', lambda: 'different-host')
    assert identity_store.initialize_identity(tmp_path)['status'] == 'conflict'
    assert (tmp_path / 'identity.db').read_bytes() == before


def test_failed_version_write_rolls_back(tmp_path):
    original = identity_store.initialize_identity(tmp_path)
    with closing(sqlite3.connect(tmp_path / 'identity.db')) as db:
        db.execute("CREATE TRIGGER reject_history BEFORE INSERT ON identity_history "
                   "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match='simulated write failure'):
        identity_store.initialize_identity(tmp_path, identity_version=2)
    assert identity_store.initialize_identity(tmp_path) == original


def test_profiles_are_distinct(tmp_path):
    a = identity_store.initialize_identity(tmp_path / 'profiles' / 'a')
    b = identity_store.initialize_identity(tmp_path / 'profiles' / 'b')
    assert a['individual_id'] != b['individual_id']


def test_concurrent_first_start_is_atomic(tmp_path):
    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(lambda _: identity_store.initialize_identity(tmp_path), range(12)))
    assert len({r['individual_id'] for r in records}) == 1
    with closing(sqlite3.connect(tmp_path / 'identity.db')) as db:
        assert db.execute('SELECT COUNT(*) FROM identity_history').fetchone()[0] == 1


@pytest.mark.parametrize('section', [{}, {'identity_enabled': False}, {'identity_enabled': 'false'}])
def test_disabled_hook_does_not_create_identity(tmp_path, monkeypatch, section):
    from agent.agent_init import _init_identity
    from types import SimpleNamespace
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path))
    before = sorted(p.name for p in tmp_path.iterdir())
    agent = SimpleNamespace()
    _init_identity(agent, {'digital_life': section})
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not hasattr(agent, 'identity')


def test_enabled_hook_uses_profile_home(tmp_path, monkeypatch):
    from agent.agent_init import _init_identity
    from types import SimpleNamespace
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path))
    agent = SimpleNamespace()
    _init_identity(agent, {'digital_life': {'identity_enabled': True}})
    assert agent.identity['status'] == 'ready'
    assert (tmp_path / 'identity.db').exists()
