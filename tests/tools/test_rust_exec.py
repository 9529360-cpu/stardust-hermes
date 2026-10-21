"""Real JSONL protocol and optional local execution integration."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / 'target' / 'release' / ('stardust-exec.exe' if os.name == 'nt' else 'stardust-exec')


def test_opt_in_only(monkeypatch):
    from tools.environments.rust_exec import enabled
    monkeypatch.delenv('STARDUST_EXEC_BACKEND', raising=False)
    assert not enabled()
    monkeypatch.setenv('STARDUST_EXEC_BACKEND', 'rust')
    assert enabled()


@pytest.mark.skipif(not BINARY.exists(), reason='cargo build --release required')
@pytest.mark.parametrize('code,timeout,expected', [
    ("print('中文'); raise SystemExit(7)", 5000, 7),
    ("import time; print('partial', flush=True); time.sleep(10)", 100, 124),
])
def test_protocol(code, timeout, expected):
    request = dict(argv=[sys.executable, '-c', code], timeout_ms=timeout)
    proc = subprocess.run([str(BINARY)], input=json.dumps(request) + '\n',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', timeout=10)
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    assert events[-1]['exit_code'] == expected
    assert events[-1]['elapsed_ms'] >= 0
    assert ''.join(e.get('output', '') for e in events).strip() == ('中文' if expected == 7 else 'partial')


@pytest.mark.skipif(not BINARY.exists(), reason='cargo build --release required')
def test_local_real_session(monkeypatch, tmp_path):
    from tools.environments.local import LocalEnvironment
    monkeypatch.setenv('STARDUST_EXEC_BACKEND', 'rust')
    monkeypatch.setenv('STARDUST_HOME', str(tmp_path / 'home'))
    env = LocalEnvironment(cwd=str(tmp_path))
    try:
        assert env.execute('export W5_VALUE=hello')['returncode'] == 0
        result = env.execute('printf "$W5_VALUE"')
        assert result['output'] == 'hello'
        assert result['returncode'] == 0
        result = env.execute('cat', stdin_data='中文\n')
        assert result['output'] == '中文\n'
        result = env.execute('echo partial; sleep 10', timeout=0.2)
        assert result['returncode'] == 124
        assert 'partial' in result['output']
    finally:
        env.cleanup()


@pytest.mark.skipif(not BINARY.exists(), reason='cargo build --release required')
def test_adapter_cancel(tmp_path):
    import time
    import psutil
    from tools.environments.rust_exec import RustProcess
    path = tmp_path / 'cancel.json'
    code = (
        'import json,os,pathlib,subprocess,sys,time; '
        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
        f'pathlib.Path({str(path)!r}).write_text(json.dumps([os.getpid(),p.pid])); '
        'time.sleep(30)'
    )
    proc = RustProcess([sys.executable, '-c', code], cwd=str(tmp_path),
                       env=dict(os.environ), timeout=30, stdin_data=None)
    try:
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert path.exists()
        pids = json.loads(path.read_text(encoding='utf-8'))
        proc.kill()
        proc.wait(timeout=5)
        assert all(not psutil.pid_exists(pid) for pid in pids)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()


@pytest.mark.skipif(not BINARY.exists(), reason='cargo build --release required')
def test_multiple_requests_and_errors():
    requests = [dict(argv=[], timeout_ms=100),
                dict(argv=[sys.executable, '-c', "print('ok')"], timeout_ms=5000)]
    proc = subprocess.run([str(BINARY)], input='\n'.join(map(json.dumps, requests)) + '\n',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', timeout=10)
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    assert 'error' in next(e for e in events if 'error' in e)
    assert events[-1]['exit_code'] == 0
    assert ''.join(e.get('output', '') for e in events).splitlines() == ['ok']


@pytest.mark.skipif(not BINARY.exists(), reason='cargo build --release required')
def test_timeout_contains_descendants(tmp_path):
    import psutil
    path = tmp_path / 'pids.json'
    code = (
        'import json,os,pathlib,subprocess,sys,time; '
        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
        f'pathlib.Path({str(path)!r}).write_text(json.dumps([os.getpid(),p.pid])); '
        'time.sleep(30)'
    )
    request = dict(argv=[sys.executable, '-c', code], timeout_ms=1000)
    proc = subprocess.run([str(BINARY)], input=json.dumps(request) + '\n',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', timeout=10)
    assert json.loads(proc.stdout.splitlines()[-1])['exit_code'] == 124
    assert all(not psutil.pid_exists(pid) for pid in json.loads(path.read_text(encoding='utf-8')))


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != 'nt' or not BINARY.exists(), reason='Windows release helper required')
def test_windows_oem_and_utf8_lines():
    import ctypes
    cp = ctypes.windll.kernel32.GetOEMCP()
    text = '\u00e9' if cp in (437, 850, 858, 1252) else '\u4e2d\u6587' if cp in (936, 950) else 'ascii'
    raw = text.encode(f'cp{cp}') + b'\n' + '\u4e2d\u6587\n'.encode('utf-8')
    code = f'import os; os.write(1, {raw!r})'
    request = dict(argv=[sys.executable, '-c', code], timeout_ms=5000)
    proc = subprocess.run([str(BINARY)], input=json.dumps(request) + '\n',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', timeout=10)
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    assert ''.join(e.get('output', '') for e in events) == text + '\n\u4e2d\u6587\n'
