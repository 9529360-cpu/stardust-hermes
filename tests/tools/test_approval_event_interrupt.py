"""Event-driven approval cancellation: latency, isolation, and lifecycle."""
import threading
import time

from tools import approval, approval_context, interrupt


def test_interrupt_wakes_only_target_waiter(monkeypatch):
    monkeypatch.setattr(approval_context, '_get_approval_config', lambda: {
        'mode': 'manual', 'timeout': 10, 'event_driven_interrupt': True})
    ready = [threading.Event() for _ in range(25)]
    done = [threading.Event() for _ in ready]
    results = {}

    def worker(i):
        interrupt.set_interrupt(False)
        try:
            results[i] = approval._await_gateway_decision(
                f'event-test-{i}', lambda _: ready[i].set(),
                {'command': 'approval-only', 'pattern_keys': ['test']})
        finally:
            interrupt.set_interrupt(False)
            done[i].set()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for thread in threads:
        thread.start()
    try:
        assert all(event.wait(5) for event in ready)
        time.sleep(.05)  # Ensure the interrupt arrives during the blocking wait.
        interrupt.set_interrupt(True, threads[0].ident)
        assert done[0].wait(.25), 'opt-in interrupt still waits for the 1-second poll'
        assert results[0]['choice'] == 'deny'
        assert not any(event.is_set() for event in done[1:])
        for i in range(1, 25):
            approval.resolve_gateway_approval(f'event-test-{i}', 'once')
        assert all(event.wait(3) for event in done)
    finally:
        for thread in threads:
            interrupt.set_interrupt(True, thread.ident)
        for thread in threads:
            thread.join(3)
    assert all(not thread.is_alive() for thread in threads)
    assert all(results[i]['choice'] == 'once' for i in range(1, 25))
    assert not interrupt._interrupt_waiters


def test_wakeup_registration_cleanup_and_preexisting_interrupt():
    wake = threading.Event()
    interrupt.set_interrupt(True)
    try:
        with interrupt.interrupt_wakeup(wake.set):
            assert wake.is_set()
        wake.clear()
        interrupt.set_interrupt(True)
        assert not wake.is_set()
        assert not interrupt._interrupt_waiters
    finally:
        interrupt.set_interrupt(False)


def test_coalesced_follower_interrupt_preserves_leader(monkeypatch):
    from tools.approval_gateway_wait import _ApprovalEvent, _poll_event
    monkeypatch.setattr(approval_context, '_get_approval_config', lambda: {
        'timeout': 5, 'event_driven_interrupt': True})
    answer = _ApprovalEvent()
    result = {}
    def wait():
        interrupt.set_interrupt(False)
        try:
            result['state'] = _poll_event(answer, 'shared', interrupt_log='%s')
        finally:
            interrupt.set_interrupt(False)
    thread = threading.Thread(target=wait)
    thread.start()
    time.sleep(.05)
    interrupt.set_interrupt(True, thread.ident)
    thread.join(1)
    assert result['state'] == 'interrupted'
    assert not answer.is_set()
    assert not answer._listeners
    assert not interrupt._interrupt_waiters


def test_recycled_threads_do_not_keep_wakeup_registrations():
    events = []
    def worker():
        interrupt.set_interrupt(False)
        event = threading.Event()
        events.append(event)
        with interrupt.interrupt_wakeup(event.set):
            interrupt.set_interrupt(True)
            assert event.is_set()
        event.clear()
        interrupt.set_interrupt(False)
    for _ in range(100):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
    assert not any(event.is_set() for event in events)
    assert not interrupt._interrupt_waiters
