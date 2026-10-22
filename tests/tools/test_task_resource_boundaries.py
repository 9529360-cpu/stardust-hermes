import os
from pathlib import Path
import sys

import psutil


def test_task_paths_release_cwd_and_refuse_replacement(tmp_path, monkeypatch):
    from tools.task_resources import cleanup_task, register_path
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path / 'home'))
    path=tmp_path/'中文 空格 disposable'
    path.mkdir()
    register_path('paths', kind='temp_dir', path=str(path))
    monkeypatch.chdir(path)
    receipt=cleanup_task('paths')
    assert not path.exists()
    assert Path.cwd() == tmp_path
    assert len(receipt['cleared']) == 1
    assert not receipt['failed']


def test_pid_mismatch_is_reported_and_never_killed(tmp_path, monkeypatch):
    from tools.process_registry import ProcessRegistry, ProcessSession
    from tools.task_resources import register_process, cleanup_task
    monkeypatch.setenv('STARDUST_HOME',str(tmp_path/'home'))
    registry=ProcessRegistry()
    session=ProcessSession(id='proc-mismatch', command='unrelated', task_id='mismatch',
                           owner_task_id='mismatch', pid=os.getpid(), detached=True,
                           host_start_time=-1)
    registry._running[session.id]=session
    register_process(session,lifetime='task',registry=registry)
    receipt=cleanup_task('mismatch')
    assert receipt['failed']
    assert 'fingerprint mismatch' in receipt['failed'][0]['reason']
    assert psutil.pid_exists(os.getpid())


def test_profile_owner_isolation(tmp_path, monkeypatch):
    from tools.task_resources import register_path,cleanup_task
    path=tmp_path/'owned'
    path.mkdir()
    monkeypatch.setenv('STARDUST_HOME',str(tmp_path/'a'))
    register_path('same-id',kind='temp_dir',path=str(path))
    monkeypatch.setenv('STARDUST_HOME',str(tmp_path/'b'))
    assert not cleanup_task('same-id')['cleared']
    assert path.exists()
    monkeypatch.setenv('STARDUST_HOME',str(tmp_path/'a'))
    assert cleanup_task('same-id')['cleared']


def test_explicit_service_survives_top_level_oneshot_exit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent.client_lifecycle import ClientLifecycleMixin
    from tools.process_registry import process_registry
    from tools.task_resources import resource_scope
    monkeypatch.setenv('STARDUST_HOME',str(tmp_path/'home'))
    with resource_scope('service'):
        session=process_registry.spawn_local(f'"{sys.executable}" -c "import time; time.sleep(30)"',
                                             task_id='service-control',cwd=str(tmp_path))
    try:
        agent=SimpleNamespace(_process_owner_task_ids={'service-control'})
        assert ClientLifecycleMixin.stop_owned_background_processes(agent,source='oneshot_exit') == 0
        assert psutil.pid_exists(session.pid)
    finally:
        process_registry.kill_process(session.id)
