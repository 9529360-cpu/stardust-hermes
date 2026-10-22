"""Task-owned resource accounting on top of the process registry.

A session process keeps its historical across-turn lifetime. Only explicitly task
scoped resources are reaped on turn completion. One owner lock serializes timeout
and normal finalization; a new registration invalidates the previous receipt.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from hermes_constants import get_hermes_home, hermes_home_key
from agent.redact import redact_sensitive_text

_scope = ContextVar('task_resource_scope', default=('session', ()))
_owners = {}
_owners_lock = threading.Lock()


@dataclass
class TaskOwner:
    task_id: str
    home: str
    resources: dict = field(default_factory=dict)
    lock: object = field(default_factory=threading.RLock)
    receipt: dict | None = None


def _owner(task_id):
    key = (hermes_home_key(), str(task_id))
    with _owners_lock:
        if key not in _owners:
            owner = TaskOwner(str(task_id), key[0])
            name = hashlib.sha256(owner.task_id.encode('utf-8')).hexdigest()[:24]
            path = Path(owner.home) / 'task_resources' / f'{name}.json'
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('owner_home') != owner.home or data.get('task_id') != owner.task_id:
                    raise ValueError('task resource owner mismatch')
                # Paths retain their original inode. Process handles are adopted only
                # through the PID/profile-validated process checkpoint recovery.
                for resource in data.get('resources', ()):
                    if resource.get('kind') in ('temp_dir', 'worktree'):
                        owner.resources[resource['id']] = resource
            _owners[key] = owner
        return _owners[key]


def _save(owner):
    from utils import atomic_json_write
    name = hashlib.sha256(owner.task_id.encode('utf-8')).hexdigest()[:24]
    path = Path(owner.home) / 'task_resources' / f'{name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, dict(task_id=owner.task_id, owner_home=owner.home,
                                resources=[{k: v for k, v in r.items() if not k.startswith('_')}
                                           for r in owner.resources.values()], receipt=owner.receipt))


@contextmanager
def resource_scope(lifetime='session', paths=()):
    if lifetime not in ('task', 'service', 'session'):
        raise ValueError('resource_lifetime must be task, service or session')
    token = _scope.set((lifetime, paths))
    try:
        yield
    finally:
        _scope.reset(token)


def register_process(session, *, lifetime=None, registry=None):
    from tools.process_registry import process_registry
    scoped_lifetime, paths = _scope.get()
    if not (session.owner_task_id or session.task_id):
        return
    lifetime = lifetime or scoped_lifetime
    session.resource_lifetime = lifetime
    owner = _owner(session.owner_task_id or session.task_id)
    import psutil
    descendants = []
    if session.pid_scope == 'host' and session.pid:
        try:
            descendants = [(p.pid, p.create_time()) for p in psutil.Process(session.pid).children(recursive=True)]
        except psutil.NoSuchProcess:
            pass
    with owner.lock:
        owner.resources[session.id] = dict(kind='process', id=session.id, pid=session.pid,
            host_start_time=session.host_start_time, cwd=session.cwd, lifetime=lifetime,
            owner_task_id=session.owner_task_id or session.task_id,
            detached=session.detached, descendants=descendants,
            _session=session, _registry=registry or process_registry)
        for item in paths:
            register_path(owner.task_id, **item)
        owner.receipt = None
        _save(owner)


def register_browser(task_id, cleanup):
    owner = _owner(task_id)
    with owner.lock:
        owner.resources['browser'] = dict(kind='browser', id='browser', lifetime='task', _cleanup=cleanup)
        owner.receipt = None
        _save(owner)


def register_path(task_id, *, kind, path, repository=None):
    """Register only paths the task created and is authorized to remove."""
    if kind not in ('temp_dir', 'worktree'):
        raise ValueError('resource path kind must be temp_dir or worktree')
    target = Path(path).resolve(strict=True)
    # Never accept a root/home/repository checkout or link as disposable storage.
    home = get_hermes_home().resolve()
    if (not target.is_dir() or Path(path).is_symlink() or target.parent == target
            or target == Path.home().resolve() or target == home or target in home.parents):
        raise ValueError('unsafe task resource path')
    repo = str(Path(repository).resolve(strict=True)) if repository else None
    if kind == 'temp_dir' and (target / '.git').exists():
        raise ValueError('Git checkouts must use worktree ownership, never temp_dir')
    if kind == 'worktree' and (not repo or not (target / '.git').is_file()):
        raise ValueError('worktree needs its repository and a linked-worktree .git file')
    stat = target.stat()
    owner = _owner(task_id)
    with owner.lock:
        key = f'{kind}:{target}'
        owner.resources[key] = dict(kind=kind, id=key, path=str(target), repository=repo,
                                    identity=[stat.st_dev, stat.st_ino], lifetime='task')
        owner.receipt = None
        _save(owner)


def _reap_process(resource):
    import psutil
    from tools.environments.windows_job import terminate_contained
    session = resource['_session']
    if (session.owner_task_id or session.task_id) != resource['owner_task_id']:
        raise RuntimeError('ownership transferred; process belongs to another task')
    registry = resource['_registry']
    if session.pid_scope == 'host' and session.pid:
        if registry._is_host_pid_alive(session.pid):
            if session.host_start_time is None:
                raise RuntimeError('missing PID start fingerprint; ownership unproven')
            if not registry._host_pid_is_ours(session.pid, session.host_start_time):
                raise RuntimeError('PID start fingerprint mismatch; refusing unrelated process')
        # Snapshot before signaling; psutil objects defend against recycled child PIDs.
        try:
            parent = psutil.Process(session.pid)
            if registry._host_pid_is_ours(session.pid, session.host_start_time):
                resource['ports'] = sorted({c.laddr.port for c in parent.net_connections(kind='inet') if c.status == psutil.CONN_LISTEN})
            targets = parent.children(recursive=True) if registry._host_pid_is_ours(session.pid, session.host_start_time) else []
        except psutil.NoSuchProcess:
            targets = []
        for pid, started in resource.get('descendants', ()):
            try:
                child = psutil.Process(pid)
                if child.create_time() == started:
                    targets.append(child)
            except psutil.NoSuchProcess:
                pass
    else:
        targets = []
    ports = set(resource.get('ports', ()))
    for child in targets:
        try:
            ports.update(c.laddr.port for c in child.net_connections(kind='inet') if c.status == psutil.CONN_LISTEN)
        except psutil.NoSuchProcess:
            pass
    resource['ports'] = sorted(ports)
    result = registry.kill_process(session.id, source='task.cleanup', consume_output=True)
    if result.get('status') == 'error':
        raise RuntimeError(result.get('error'))
    # A naturally exited wrapper still owns its Windows job and descendants.
    if session.process is not None:
        terminate_contained(session.process)
    for child in targets:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if session.process is not None:
        session.process.wait(timeout=5)
    _, alive = psutil.wait_procs(targets, timeout=5)
    if alive:
        raise RuntimeError(f'process descendants still alive: {[p.pid for p in alive]}')
    if session.pid_scope == 'host' and registry._host_pid_is_ours(session.pid, session.host_start_time):
        raise RuntimeError('process still alive after cleanup')
    return result.get('status', 'released')


def _release_path(resource):
    target = Path(resource['path'])
    if not target.exists():
        return 'already absent'
    stat = target.stat()
    if target.is_symlink() or [stat.st_dev, stat.st_ino] != resource['identity']:
        raise RuntimeError('path identity changed; refusing removal')
    # Release our own cwd only when it is inside this explicitly disposable path.
    cwd = Path.cwd().resolve()
    if cwd == target or target in cwd.parents:
        os.chdir(target.parent)
    if resource['kind'] == 'worktree':
        result = subprocess.run(['git', '-C', resource['repository'], 'worktree', 'remove', str(target)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                stdin=subprocess.DEVNULL, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
    else:
        shutil.rmtree(target)
    return 'removed'


def cleanup_task(task_id, *, reason='task_end'):
    owner = _owner(task_id)
    with owner.lock:
        if owner.receipt is not None:
            return owner.receipt
        receipt = dict(task_id=owner.task_id, owner_home=owner.home, reason=reason,
                       cleared=[], retained=[], failed=[])
        # Programs must release cwd and sockets before removing their directories.
        resources = sorted(owner.resources.values(), key=lambda r: {'process': 0, 'browser': 1}.get(r['kind'], 2))
        for resource in resources:
            item = {k: v for k, v in resource.items() if not k.startswith('_')}
            if resource['kind'] == 'process' and (resource['_session'].owner_task_id or resource['_session'].task_id) != owner.task_id:
                item['reason'] = 'ownership transferred to another task'
                receipt['retained'].append(item)
                continue
            if resource['lifetime'] != 'task':
                item['reason'] = f"{resource['lifetime']} lifetime; observable via process_manage"
                receipt['retained'].append(item)
                continue
            try:
                if resource['kind'] == 'process':
                    item['result'] = _reap_process(resource)
                elif resource['kind'] == 'browser':
                    resource['_cleanup']()
                    item['result'] = 'closed'
                else:
                    item['result'] = _release_path(resource)
                item['ports'] = resource.get('ports', [])
                receipt['cleared'].append(item)
            except Exception as exc:
                item['reason'] = redact_sensitive_text(f'{type(exc).__name__}: {exc}', code_file=True)
                receipt['failed'].append(item)
        owner.receipt = receipt
        _save(owner)
        return receipt


def record_cleanup_outcome(task_id, receipt, status, item):
    owner = _owner(task_id)
    with owner.lock:
        if not any(r['id'] == item['id'] for r in receipt[status]):
            receipt[status].append(item)
        _save(owner)


def receipt_text(receipt):
    if not any(receipt.get(key) for key in ('cleared', 'retained', 'failed')):
        return ''
    lines = [f"资源收尾：释放 {len(receipt['cleared'])}，保留 {len(receipt['retained'])}，失败 {len(receipt['failed'])}。"]
    for status in ('cleared', 'retained', 'failed'):
        for item in receipt[status]:
            lines.append(f"  {status}: {item.get('path') or item['id']} — {item.get('reason') or item.get('result', '')}")
    return '\n'.join(lines)
