"""Task ownership receipts exercise real processes; no production home is used."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest


def test_task_receipt_reaps_tree_and_retains_service(tmp_path, monkeypatch):
    from tools.process_registry import ProcessRegistry
    from tools.task_resources import register_process, cleanup_task

    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path / 'home'))
    registry = ProcessRegistry()
    task = registry.spawn_local(f'"{sys.executable}" -c "import time; time.sleep(60)"',
                                task_id='receipt', cwd=str(tmp_path))
    service = registry.spawn_local(f'"{sys.executable}" -c "import time; time.sleep(60)"',
                                   task_id='receipt', cwd=str(tmp_path))
    try:
        register_process(task, lifetime='task', registry=registry)
        register_process(service, lifetime='service', registry=registry)
        with ThreadPoolExecutor(2) as pool:
            receipts = list(pool.map(lambda _: cleanup_task('receipt'), range(2)))
        assert receipts[0] == receipts[1]
        assert any(r['id'] == task.id for r in receipts[0]['cleared'])
        assert any(r['id'] == service.id for r in receipts[0]['retained'])
        assert not receipts[0]['failed']
        assert not psutil.pid_exists(task.pid)
        assert psutil.pid_exists(service.pid)
    finally:
        registry.kill_process(task.id)
        registry.kill_process(service.id)


def test_rust_cleanup_after_helper_exit_is_idempotent(monkeypatch):
    from tools.environments.rust_exec import RustProcess
    monkeypatch.setitem(sys.modules, 'stardust_native', None)
    proc = object.__new__(RustProcess)
    proc._proc = subprocess.Popen([sys.executable, '-c', 'pass'], stdin=subprocess.DEVNULL)
    proc.pid = proc._proc.pid
    proc._proc.wait(timeout=10)
    proc._child_pid = None
    proc.kill()
    proc.kill()


def test_rust_cleanup_does_not_swallow_access_denied(monkeypatch):
    from tools.environments.rust_exec import RustProcess
    monkeypatch.setitem(sys.modules, 'stardust_native', None)
    proc = object.__new__(RustProcess)
    proc.pid = os.getpid()
    proc._proc = type('Handle', (), {'pid': os.getpid()})()
    def denied(*args, **kwargs):
        raise psutil.AccessDenied(os.getpid())
    monkeypatch.setattr(psutil.Process, 'children', denied)
    if os.name == 'nt':
        with pytest.raises(psutil.AccessDenied):
            proc.kill()
