"""Publishing a session's turn thread (``session["_run_thread"]``).

Readers use the handle to tell a live turn from a finished one: compute_host's
``_run_real_turn`` follows it before emitting ``turn.end``, and ``session.interrupt``
clears a ``running`` flag it believes is stuck when the handle's thread is dead.
"""

from __future__ import annotations

import threading

# Leaf lock: nothing is acquired while holding it, so writers may publish from inside
# ``_sessions_lock`` without a lock-order risk.
_PUBLISH_LOCK = threading.Lock()


def start_turn_thread(session: dict, run_thread: threading.Thread) -> None:
    """Start ``run_thread`` and publish it, unless a newer owner took over meanwhile.

    Started before publishing: ``is_alive()`` is False both before ``start()`` and
    after the end, so a reader must never see a thread that has not begun. But a turn
    can finish and chain its follow-up (goal continuation, queued prompt) — which
    publishes itself — before this writer gets to publish. Overwriting that with the
    finished thread made the compute host emit ``turn.end`` while the follow-up ran
    and let ``session.interrupt`` clear ``running`` under a live turn.
    """
    with _PUBLISH_LOCK:
        previous = session.get("_run_thread")
    run_thread.start()
    with _PUBLISH_LOCK:
        if session.get("_run_thread") is previous:
            session["_run_thread"] = run_thread
