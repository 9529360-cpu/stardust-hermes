"""Publishing a session's turn thread (``session["_run_thread"]``).

Readers use the handle to tell a live turn from a finished one: compute_host's
``_run_real_turn`` follows it before emitting ``turn.end``, and ``session.interrupt``
clears a ``running`` flag it believes is stuck when the handle's thread is dead.
"""

from __future__ import annotations

import itertools
import threading

# Leaf lock: nothing is acquired while holding it, so writers may publish from inside
# ``_sessions_lock`` without a lock-order risk.
_PUBLISH_LOCK = threading.Lock()
_LAUNCH_TICKETS = itertools.count(1)
_TICKET_ATTR = "_hermes_turn_launch_ticket"


def start_turn_thread(session: dict, run_thread: threading.Thread) -> None:
    """Start ``run_thread`` and publish it unless a later launch already published its own.

    Started before publishing: ``is_alive()`` is False both before ``start()`` and after
    the end, so a reader must never see a thread that has not begun. Launches nest — a
    prompt.submit wrapper launches the real turn, a finishing turn launches its follow-up
    (goal continuation, queued prompt) — and the nested launch can publish before or after
    its launcher does. Each launch takes a ticket BEFORE starting its thread, so a nested
    launch always holds the higher one; publishing only over lower tickets keeps the latest
    turn recorded in every interleaving. A stale handle on a finished thread made the
    compute host emit ``turn.end`` while the follow-up ran and let ``session.interrupt``
    clear ``running`` under a live turn.
    """
    with _PUBLISH_LOCK:
        ticket = next(_LAUNCH_TICKETS)
    setattr(run_thread, _TICKET_ATTR, ticket)
    run_thread.start()
    with _PUBLISH_LOCK:
        if ticket > getattr(session.get("_run_thread"), _TICKET_ATTR, 0):
            session["_run_thread"] = run_thread
