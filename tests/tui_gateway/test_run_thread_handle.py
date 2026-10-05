"""A turn launcher never replaces a newer turn thread with the one it started.

``session["_run_thread"]`` tells readers whether a turn is live: compute_host follows it before
emitting ``turn.end``, and ``session.interrupt`` clears a ``running`` flag it believes is stuck when
that thread is dead. prompt.submit starts a wrapper thread that launches the real turn thread and
publishes it; a submit handler that published its (by then finished) wrapper afterwards left the
handle on a dead thread for the whole turn. Main CI hit the same race with a chained goal
continuation: "compute host emitted turn.end while the chained follow-up was still executing".
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

from tui_gateway.run_thread_handle import start_turn_thread


class _LauncherDescheduledAfterStart(threading.Thread):
    """``start()`` returns only once ``handed_off`` is set: the launcher lost the CPU right
    after starting this thread, long enough for the thread to hand off to the next one."""

    def __init__(self, handed_off: threading.Event, **kwargs):
        super().__init__(**kwargs)
        self._handed_off = handed_off

    def start(self):
        super().start()
        assert self._handed_off.wait(5)


def test_a_launcher_keeps_the_turn_its_wrapper_already_published():
    session: dict = {}
    release_turn = threading.Event()
    handed_off = threading.Event()
    turn = threading.Thread(target=release_turn.wait)

    def wrapper_body():
        start_turn_thread(session, turn)  # the real turn, published from inside the wrapper
        handed_off.set()

    wrapper = _LauncherDescheduledAfterStart(handed_off, target=wrapper_body)
    try:
        start_turn_thread(session, wrapper)
        wrapper.join(5)

        assert session["_run_thread"] is turn
        assert turn.is_alive()
    finally:
        release_turn.set()
        turn.join(5)


class _StartGate(threading.Thread):
    """``start()`` runs the thread, signals ``started``, then holds the launcher until ``go``."""

    def __init__(self, started: threading.Event, go: threading.Event, **kwargs):
        super().__init__(**kwargs)
        self._started_evt, self._go = started, go

    def start(self):
        super().start()
        self._started_evt.set()
        assert self._go.wait(5)


def test_the_newer_turn_wins_when_the_launchers_publish_out_of_order():
    """Both launches start before either publishes; the OUTER one (older turn) publishes first.

    Main CI (66fbde8649, after #273) still failed test_turn_end_waits_for_chained_followup_thread
    this way: a compare-against-what-I-saw publish let the outer launcher record its finished
    wrapper, then the inner launcher saw "changed" and never recorded the live follow-up.
    """
    session: dict = {}
    inner_started, outer_published = threading.Event(), threading.Event()
    wrapper_started, release_turn = threading.Event(), threading.Event()
    turn = _StartGate(inner_started, outer_published, target=release_turn.wait)

    def wrapper_body():
        start_turn_thread(session, turn)  # inner launch: publishes only after the outer one did

    # The outer launch publishes only after the inner launch has started its thread.
    wrapper = _StartGate(wrapper_started, inner_started, target=wrapper_body)
    try:
        start_turn_thread(session, wrapper)
        outer_published.set()
        wrapper.join(5)

        assert session["_run_thread"] is turn
        assert turn.is_alive()
    finally:
        outer_published.set()
        release_turn.set()
        turn.join(5)


def test_a_launcher_publishes_over_a_finished_previous_turn():
    session: dict = {}
    previous = threading.Thread(target=lambda: None)
    start_turn_thread(session, previous)
    previous.join(5)
    release = threading.Event()
    current = threading.Thread(target=release.wait)
    try:
        start_turn_thread(session, current)

        assert session["_run_thread"] is current
    finally:
        release.set()
        current.join(5)


def test_every_turn_thread_is_published_through_start_turn_thread():
    """Single-writer invariant: a direct assignment skips the newer-owner check."""
    package = Path(__file__).resolve().parents[2] / "tui_gateway"
    offenders = []
    for path in sorted(package.glob("*.py")):
        if path.name == "run_thread_handle.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets = node.targets if isinstance(node, ast.Assign) else []
            for target in targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "_run_thread"):
                    offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []
