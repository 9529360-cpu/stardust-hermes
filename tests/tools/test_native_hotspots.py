"""Native kernels must preserve the existing Python algorithms exactly."""
import random
import string
from difflib import SequenceMatcher

import pytest


def native():
    return pytest.importorskip('stardust_native')


def test_sequence_ratios_unicode_autojunk_and_ties():
    module = native()
    rng = random.Random(9201)
    alphabet = string.ascii_letters + ' ab_中文😀\u0130\u200b'
    pairs = [('tide', 'diet'), ('a' * 210, 'b' + 'a' * 210), ('', ''), ('中文😀', '中文😁')]
    pairs += [(a, b) for a in ('ab' * 100, 'x' * 199, 'x' * 200, '😀' * 201)
              for b in ('ba' * 100, 'x' * 199, 'x' * 200, '😀' * 201)]
    pairs += [(''.join(rng.choices(alphabet, k=rng.randrange(0, 260))),
               ''.join(rng.choices(alphabet, k=rng.randrange(0, 260)))) for _ in range(150)]
    for a, b in pairs:
        assert module.sequence_ratios(a, [b, b]) == [SequenceMatcher(None, a, b).ratio()] * 2


def test_native_child_output_exit_and_kill(tmp_path):
    import sys
    module = native()
    proc = module.NativeChild([sys.executable, '-c', "print('中文'); raise SystemExit(7)"],
                              str(tmp_path), None, None)
    output = b''
    while True:
        chunk = proc.read()
        if chunk is None:
            continue
        if not chunk:
            break
        output += chunk
    assert output.decode('utf-8').strip() == '中文'
    assert proc.wait() == 7
    proc = module.NativeChild([sys.executable, '-c', 'import time; time.sleep(20)'],
                              str(tmp_path), None, None)
    proc.kill()
    assert proc.wait() != 0


def test_full_output_equivalence(monkeypatch):
    import stardust_native_support as support
    from tools.fuzzy_match import find_closest_lines
    from agent.redact import redact_sensitive_text

    module = native()
    cases = [('  alpha(x)', '\talpha(x)\n\nalpha(y)\n中文😀\nalpha(x)'),
             ('\n \n中文😀', '中文😁\r\n中文😀\u2028tail'),
             ('byte\udcff', 'byte\udcff\nbytes'),
             ('a' * 210, 'b' + 'a' * 210 + '\n' + 'a' * 220)]
    for anchor, content in cases:
        monkeypatch.setattr(support, 'sequence_ratios', module.sequence_ratios)
        fast = find_closest_lines(anchor, content)
        monkeypatch.setattr(support, 'sequence_ratios', None)
        assert fast == find_closest_lines(anchor, content)
    text = ('normal=value\nAPI_KEY=example-fixture-123456789012345\n'
            'openai_key=example-fixture-123456789012345\npassword=x\n'
            'https://example.test/?token=roundtrip\n中文\n')
    for file_read in (False, True):
        for secret_file in (False, True):
            for code_file in (False, True):
                opts = dict(force=True, file_read=file_read, secret_file=secret_file, code_file=code_file)
                monkeypatch.setattr(support, 'assignment_candidate', module.assignment_candidate)
                fast = redact_sensitive_text(text, **opts)
                monkeypatch.setattr(support, 'assignment_candidate', None)
                assert fast == redact_sensitive_text(text, **opts)


def test_adapter_streaming_and_detached_reader(tmp_path):
    import os
    import sys
    import time
    import threading
    from tools.environments.native_process import NativeProcess
    native()
    before = set(threading.enumerate())
    code = ("import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(1.5)']); "
            "print('parent done',flush=True)")
    proc = NativeProcess([sys.executable, '-c', code], cwd=str(tmp_path),
                         env=dict(os.environ), timeout=5, stdin_data=None)
    start = time.monotonic()
    assert 'parent done' in proc.stdout.read()
    assert proc.wait(2) == 0
    assert time.monotonic() - start < 1.4
    proc.stdout.close()
    assert not (set(threading.enumerate()) - before)
    # Let this test's detached child finish naturally before fixture cleanup.
    time.sleep(1.6)


def test_fallback_import_without_extension(monkeypatch):
    import importlib
    import sys
    import stardust_native_support as support
    with monkeypatch.context() as ctx:
        ctx.setitem(sys.modules, 'stardust_native', None)
        importlib.reload(support)
        assert support.sequence_ratios is None
        assert support.assignment_candidate is None
    importlib.reload(support)


def test_assignment_gate_has_no_false_negatives():
    from agent import redact
    module = native()
    cases = ['nothing here = value', 'PASSWORD=opaque-value', 'openai_key=opaque-value',
             'spring.datasource.password=x', 'export token=x', 'AUTH=x', 'PW=x',
             'aPi_KeY=x', 'app.api.key=x', 'apiİkey=x', 'ſecret=x', 'KEY=x']
    rng = random.Random(984)
    cases += [''.join(rng.choices(string.printable + 'İıſK', k=120)) for _ in range(500)]
    patterns = [redact._ENV_ASSIGN_RE, redact._ENV_ASSIGN_LOWER_RE, redact._CFG_SECRET_WORD_RE]
    for text in cases:
        if any(p.search(text) for p in patterns):
            assert module.assignment_candidate(text), repr(text)
    assert not module.assignment_candidate('value_123 = process_item("example", enabled=True)')
