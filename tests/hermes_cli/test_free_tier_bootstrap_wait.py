"""F7.2 — ``setup.status`` must not pay the full ``SETUP_READY_WAIT_SECONDS`` budget for a
bootstrap that can no longer produce a free-tier identity.

Contracts under test (through the real handler and the real ``run_bootstrap``):
- gate closed (no ``STARDUST_GUEST_ONBOARDING``) -> the bootstrap finishes right after its
  inventory; ``wait_for_record`` returns the record promptly (well under the 8 s ceiling).
- a bootstrap that DOES mint within the budget -> the record still wins (unchanged payload shape).
- a slow-but-successful bootstrap -> the wait still ends when the record is published.
- a bootstrap whose unguarded reads crash (``current_nous_state`` raising) -> an error record is
  published anyway, so ``wait_for_record`` answers promptly on THIS call and every later one.
- a concurrent second ``run_bootstrap`` while the first is mid-mint -> returns the minted record
  (the wait must not hold the publish lock and deadlock against the finisher).
"""

from __future__ import annotations

import threading
import time

import pytest

from hermes_cli import anon_auth
from hermes_cli import free_tier_bootstrap as fb


@pytest.fixture(autouse=True)
def _reset():
    fb.reset_for_tests()
    yield
    fb.reset_for_tests()


def _wait_for_record_ms(timeout: float = 8.0) -> tuple[object, float]:
    t0 = time.perf_counter()
    record = fb.wait_for_record(timeout)
    return record, (time.perf_counter() - t0) * 1000.0


def test_gate_closed_bootstrap_finishes_promptly_and_setup_status_answers_fast(monkeypatch):
    """Fresh machine, gate off: the bootstrap has nothing to mint, so the record lands right after
    the inventory and the first ``setup.status`` poll never sits out the budget."""
    monkeypatch.delenv("STARDUST_GUEST_ONBOARDING", raising=False)
    monkeypatch.setattr(anon_auth, "current_nous_state", lambda: None)
    monkeypatch.setattr(fb, "_inventory_other_providers", lambda: False)
    monkeypatch.setattr(fb, "_resolve_inference", lambda: "")

    thread = fb.start_background_bootstrap()
    record, waited_ms = _wait_for_record_ms()

    thread.join(timeout=5)
    assert record is not None, "gate closed must still publish a record"
    assert record.free_tier is False and record.has_identity is False
    assert waited_ms < 1000.0, f"waited {waited_ms:.0f} ms for a bootstrap with nothing to mint"


def test_bootstrap_that_mints_within_budget_still_wins(monkeypatch, tmp_path):
    """Case (a): a stubbed bootstrap that produces a free-tier identity inside the budget —
    ``wait_for_record`` returns it (same payload the desktop expects), even when slow."""
    released = threading.Event()

    def slow_mint(*, explicit, carries_inference):
        released.wait(10)
        return {"provider": "nous", "anon_token": "anon_0001", "model": anon_auth.GUEST_MODEL}

    monkeypatch.setenv("STARDUST_GUEST_ONBOARDING", "1")
    monkeypatch.setenv("STARDUST_SHARED_AUTH_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(anon_auth, "current_nous_state", lambda: None)
    monkeypatch.setattr(anon_auth, "guest_enabled", lambda: True)
    monkeypatch.setattr(anon_auth, "is_guest_state", lambda state: bool(state))
    monkeypatch.setattr(anon_auth, "ensure_portal_identity", slow_mint)
    monkeypatch.setattr(fb, "_inventory_other_providers", lambda: False)
    monkeypatch.setattr(fb, "_resolve_inference", lambda: "nous")

    thread = fb.start_background_bootstrap()
    threading.Timer(0.3, released.set).start()
    record, waited_ms = _wait_for_record_ms()

    thread.join(timeout=5)
    assert record is not None and record.free_tier is True and record.has_identity is True
    assert record.provider_configured is True and record.inference_provider == "nous"
    assert waited_ms >= 250.0, "the wait must not return BEFORE the mint publishes"


def test_bootstrap_crash_publishes_an_error_record_and_never_stalls_setup_status(monkeypatch):
    """Case (c): ``current_nous_state`` raises (an unguarded filesystem read on main). The record
    must still be published — promptly — or every ``setup.status`` call after the crash waits out
    the full 8 s budget with no record at the end of it."""
    monkeypatch.delenv("STARDUST_GUEST_ONBOARDING", raising=False)

    def boom():
        raise RuntimeError("state store unreadable")

    monkeypatch.setattr(anon_auth, "current_nous_state", boom)
    monkeypatch.setattr(fb, "_inventory_other_providers", lambda: False)

    thread = fb.start_background_bootstrap()
    record, waited_ms = _wait_for_record_ms()
    thread.join(timeout=5)

    assert record is not None, "a crashed bootstrap must still publish an error record"
    assert "bootstrap failed" in record.error
    assert record.free_tier is False and record.provider_configured is False
    assert waited_ms < 1000.0

    again, again_ms = _wait_for_record_ms()
    assert again is record and again_ms < 100.0, "later calls read the record, no re-wait"


def test_concurrent_run_bootstrap_waits_for_the_mint_without_deadlocking(monkeypatch, tmp_path):
    """A second ``run_bootstrap`` while the first is mid-mint: it must wait for the record (not
    mint twice, not return an error record) — and the wait may not hold the lock the publisher
    needs (the pre-F7.2 wait ran under ``_lock`` and stalled both callers)."""
    minted = threading.Event()

    def slow_mint(*, explicit, carries_inference):
        minted.wait(10)
        return {"provider": "nous", "anon_token": "anon_0002", "model": anon_auth.GUEST_MODEL}

    monkeypatch.setenv("STARDUST_GUEST_ONBOARDING", "1")
    monkeypatch.setenv("STARDUST_SHARED_AUTH_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(anon_auth, "current_nous_state", lambda: None)
    monkeypatch.setattr(anon_auth, "guest_enabled", lambda: True)
    monkeypatch.setattr(anon_auth, "is_guest_state", lambda state: bool(state))
    monkeypatch.setattr(anon_auth, "ensure_portal_identity", slow_mint)
    monkeypatch.setattr(fb, "_inventory_other_providers", lambda: False)
    monkeypatch.setattr(fb, "_resolve_inference", lambda: "nous")

    first = fb.start_background_bootstrap()
    second = threading.Thread(target=fb.run_bootstrap, daemon=True)
    second.start()
    time.sleep(0.2)  # the second caller is now parked in the in-flight wait
    minted.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive(), "the in-flight wait must not deadlock"
    record = fb.current_record()
    assert record is not None and record.free_tier is True


def test_no_bootstrap_started_returns_none_immediately(monkeypatch):
    """A bare gateway with no boot hook: no wait at all — the caller falls back to its probe."""
    record, waited_ms = _wait_for_record_ms()
    assert record is None
    assert waited_ms < 100.0
