"""Ticker liveness markers for ``hermes cron status``: heartbeat and last-success stamps, the
last tick error and the stale-schedule catch-up counter.

Split out of ``cron.jobs``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_jobs`` (import-cycle breaking), so
monkeypatching ``cron.jobs.<name>`` keeps working.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Optional

logger = logging.getLogger("cron.jobs")  # log-record parity with the origin module


# --- Ticker heartbeat (liveness signal for `hermes cron status`) ---

def _write_marker(name: str, text: str, tmp_prefix: str) -> None:
    """Atomic (never torn) best-effort marker write; failures swallowed so markers never break the
    tick."""
    try:
        _jobs.ensure_dirs()
        _jobs.atomic_write_text(_jobs._current_cron_store().cron_dir / name, text, tmp_prefix=tmp_prefix, mode=0o600)
    except Exception:
        pass


def record_ticker_heartbeat(success: bool = False) -> None:
    """Record ticker liveness (+ last-success marker when ``success``) so `cron status` can tell
    "alive but failing" from "firing"; scoped per profile store.

    The ticker calls this once per loop iteration. ``success=True`` additionally bumps the *last successful
    tick* marker. We track two distinct signals so `hermes cron status` can tell a thread that is merely
    *alive and looping* (heartbeat fresh, success stale) from one that is actually *firing jobs* (both
    fresh) — a ticker stuck failing every tick would otherwise keep the plain heartbeat fresh and falsely
    report healthy (#32612, #32895).
    Resolution uses ``_current_cron_store()`` so the heartbeat is correctly scoped to the active profile's
    store — critical under multiplex_profiles where each profile needs its own liveness signal (#69377).
    """
    _write_marker("ticker_heartbeat", str(_jobs.time.time()), ".hb_")
    if success:
        _write_marker("ticker_last_success", str(_jobs.time.time()), ".hb_")


def _epoch_file_age(name: str) -> Optional[float]:
    """Seconds since the epoch stamp stored in ``<cron_dir>/<name>``; None = missing/unreadable."""
    try:
        raw = (_jobs._current_cron_store().cron_dir / name).read_text(encoding="utf-8").strip()
        return max(0.0, _jobs.time.time() - float(raw))
    except Exception:
        return None


def get_ticker_heartbeat_age() -> Optional[float]:
    """Seconds since the ticker loop last iterated; None = missing/unreadable ("cannot determine",
    not "dead").

    Resolution uses ``_current_cron_store()`` so the heartbeat is correctly scoped to the active profile —
    critical under multiplex_profiles where ``hermes cron status`` must report per-profile liveness
    (#69377).
    """
    return _epoch_file_age("ticker_heartbeat")


def get_ticker_success_age() -> Optional[float]:
    """Seconds since the ticker last completed a tick WITHOUT raising, or None.

    Resolution uses ``_current_cron_store()`` so the heartbeat is correctly scoped to the active profile —
    critical under multiplex_profiles where ``hermes cron status`` must report per-profile liveness
    (#69377).
    """
    return _epoch_file_age("ticker_last_success")


def get_catch_up_occurrence_count() -> int:
    """Return the profile-local stale-schedule catch-up count."""
    path = _jobs._current_cron_store().cron_dir / "catch_up_occurrences"
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def record_catch_up_occurrence() -> None:
    """Increment the profile-local stale-schedule catch-up counter, best effort."""
    _write_marker("catch_up_occurrences", str(get_catch_up_occurrence_count() + 1), ".count_")


def record_ticker_error(message: str) -> None:
    """Persist the latest tick failure so `cron status` (another process) can show WHY, not just
    staleness."""
    _write_marker("ticker_last_error", f"{_jobs.time.time()}\n{message.strip()}\n", ".terr_")


def clear_ticker_error() -> None:
    """Remove the last-tick-error marker after a successful tick. Best-effort."""
    with contextlib.suppress(OSError):
        (_jobs._current_cron_store().cron_dir / "ticker_last_error").unlink()


def get_ticker_last_error() -> Optional[str]:
    """Return the most recent recorded tick error message, or None."""
    try:
        raw = (_jobs._current_cron_store().cron_dir / "ticker_last_error").read_text(encoding="utf-8")
    except Exception:
        return None
    lines = raw.splitlines()
    if len(lines) < 2:
        return None
    return "\n".join(lines[1:]).strip() or None


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.jobs`` re-exports from it.
from cron import jobs as _jobs  # noqa: E402
