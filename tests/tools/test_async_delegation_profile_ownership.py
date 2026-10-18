"""Session selectors must not cross the profile boundary in a shared process."""

import contextlib
import threading

import pytest

from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
from tools import async_delegation as ad
from tools.daemon_pool import DaemonThreadPoolExecutor


@contextlib.contextmanager
def profile(home):
    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.fixture(autouse=True)
def reset_registry():
    ad._reset_for_tests()
    yield
    ad._reset_for_tests()


@pytest.mark.parametrize("selector", ["session_key", "origin_ui_session_id", "parent_session_id"])
@pytest.mark.parametrize("state", ["running", "stalling", "finalizing"])
def test_session_selectors_require_matching_profile(tmp_path, selector, state):
    called = []
    for name in ("mine", "foreign"):
        ad._records[name] = {
            "delegation_id": name, "status": state, selector: "shared-id",
            "owner_home": hermes_home_key(tmp_path / name),
            "interrupt_fn": lambda name=name: called.append(name),
        }
    with profile(tmp_path / "mine"):
        assert ad.has_live_for_session(**{selector: "shared-id"})
        assert ad.interrupt_for_session(**{selector: "shared-id"}) == (0 if state == "finalizing" else 1)
        assert called == ([] if state == "finalizing" else ["mine"])
        ad._records.pop("mine")
        assert not ad.has_live_for_session(**{selector: "shared-id"})


@pytest.mark.parametrize("owner", [None, "", "missing"])
def test_legacy_ownerless_records_remain_session_scoped(owner):
    called = []
    ad._records["legacy"] = {
        "delegation_id": "legacy", "status": "running", "owner_home": owner,
        "session_key": "key", "interrupt_fn": lambda: called.append(True),
    }
    if owner == "missing":
        ad._records["legacy"].pop("owner_home")
    assert not ad.has_live_for_session()
    assert ad.interrupt_for_session() == 0
    assert ad.has_live_for_session(session_key="key", origin_ui_session_id="nonmatching")
    assert ad.interrupt_for_session(session_key="key", origin_ui_session_id="nonmatching") == 1
    assert called == [True]


def test_process_shutdown_still_interrupts_all_profiles(tmp_path):
    called = []
    for name in ("one", "two"):
        ad._records[name] = {
            "delegation_id": name, "status": "running", "owner_home": hermes_home_key(tmp_path / name),
            "interrupt_fn": lambda name=name: called.append(name),
        }
    with profile(tmp_path / "one"):
        assert ad.interrupt_all() == 2
    assert sorted(called) == ["one", "two"]


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("entry", ["session_cancel", "backend_finalize", "unbound_finalize"])
def test_real_dispatch_cancellation_and_receipts_stay_in_profile(tmp_path, monkeypatch, queued, entry):
    """Real executor, callbacks, ContextVars, lifecycle entry and per-profile SQLite.

    A one-worker executor forces a queued foreign unit without replacing dispatch logic.
    Events keep the foreign unit alive until assertions finish; no model service is needed.
    """
    homes = [tmp_path / "profile-a", tmp_path / "profile-b"]
    for home in homes:
        home.mkdir()
    started = [threading.Event(), threading.Event()]
    released = [threading.Event(), threading.Event()]
    cancelled = [threading.Event(), threading.Event()]
    executor = DaemonThreadPoolExecutor(max_workers=1 if queued else 2)
    monkeypatch.setattr(ad, "_get_executor", lambda _count: executor)
    handles = []

    def runner(index):
        started[index].set()
        assert released[index].wait(timeout=15), "test did not release worker"
        return {"status": "interrupted" if cancelled[index].is_set() else "completed",
                "summary": f"profile-{index}"}

    def stop(index):
        cancelled[index].set()
        released[index].set()

    try:
        for index, home in enumerate(homes):
            with profile(home):
                handle = ad.dispatch_async_delegation(
                    goal="isolated work", context=None, toolsets=None, role="leaf", model="test",
                    session_key="shared-key", parent_session_id="shared-parent",
                    origin_ui_session_id="shared-tab", runner=lambda i=index: runner(i),
                    interrupt_fn=lambda i=index: stop(i), max_async_children=4,
                )
                assert handle["status"] == "dispatched"
                handles.append(handle["delegation_id"])
        assert started[0].wait(timeout=5)
        if queued:
            assert not started[1].is_set()
        else:
            assert started[1].wait(timeout=5)

        if entry == "session_cancel":
            with profile(homes[0]):
                assert ad.interrupt_for_session(parent_session_id="shared-parent") == 1
        else:
            from tui_gateway import server
            session = {"profile_home": str(homes[0]), "session_key": "shared-key",
                       "_sid": "shared-tab", "history": [], "source": "tui"}
            # Reapers can run unbound, or in a different profile's ambient context.
            if entry == "unbound_finalize":
                import contextvars
                contextvars.Context().run(server._finalize_session, session)
            else:
                with profile(homes[1]):
                    server._finalize_session(session)
                    assert hermes_home_key() == hermes_home_key(homes[1])
        assert cancelled[0].is_set()
        assert not cancelled[1].is_set(), "foreign-profile worker was cancelled"
        assert started[1].wait(timeout=5)
    finally:
        for event in released:
            event.set()
        executor.shutdown(wait=True)

    for index, home in enumerate(homes):
        with profile(home):
            receipt = ad.get_durable_delegation(handles[index])
            assert receipt["state"] == ("interrupted" if index == 0 else "completed")
            assert receipt["event"]["owner_home"] == hermes_home_key(home)
            assert receipt["result"]["summary"] == f"profile-{index}"
            assert ad.get_durable_delegation(handles[1 - index]) is None


@pytest.mark.parametrize("unbound", [False, True])
def test_unbound_backend_liveness_uses_stored_session_profile(tmp_path, unbound):
    from tui_gateway import server

    homes = [tmp_path / "named", tmp_path / "ambient"]
    for home in homes:
        home.mkdir()
    session = {"profile_home": str(homes[0]), "session_key": "shared-key", "_sid": "shared-tab"}
    for name, home in zip(("own", "foreign"), homes):
        ad._records[name] = {"delegation_id": name, "status": "running", "session_key": "shared-key",
                             "origin_ui_session_id": "shared-tab", "owner_home": hermes_home_key(home)}
    def check():
        assert server._session_has_active_delegations("shared-tab", session)
        ad._records.pop("own")
        assert not server._session_has_active_delegations("shared-tab", session)

    if unbound:
        import contextvars
        contextvars.Context().run(check)
    else:
        with profile(homes[1]):
            check()
            assert hermes_home_key() == hermes_home_key(homes[1])

@pytest.mark.parametrize("operation", ["finalize", "liveness"])
def test_launch_profile_session_ignores_foreign_ambient_home(tmp_path, monkeypatch, operation):
    from hermes_state_registry import release_or_close
    from tui_gateway import server

    launch, foreign = tmp_path / "launch", tmp_path / "foreign"
    launch.mkdir()
    foreign.mkdir()
    monkeypatch.setenv("STARDUST_HOME", str(launch))
    monkeypatch.setattr(server, "_hermes_home", str(launch))
    monkeypatch.setattr(server, "_db", None)
    monkeypatch.setattr(server, "_db_error", None)
    called = []
    session = {"profile_home": None, "session_key": "shared-key", "_sid": "shared-tab", "history": []}
    for name, home in (("own", launch), ("foreign", foreign)):
        ad._records[name] = {
            "delegation_id": name, "status": "running", "owner_home": hermes_home_key(home),
            "session_key": "shared-key", "origin_ui_session_id": "shared-tab",
            "interrupt_fn": lambda name=name: called.append(name),
        }
    try:
        with profile(foreign):
            if operation == "finalize":
                server._finalize_session(session)
                assert called == ["own"]
            else:
                assert server._session_has_active_delegations("shared-tab", session)
                ad._records.pop("own")
                assert not server._session_has_active_delegations("shared-tab", session)
            assert hermes_home_key() == hermes_home_key(foreign)
    finally:
        if server._db is not None:
            release_or_close(server._db)
            server._db = None
