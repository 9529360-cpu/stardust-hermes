"""Folder triggers: the arrival, debounce and seen-set rules, the queue and the hourly budget.

The debounce rules are tested on ``observe_folder`` directly with synthetic entries. The store path
runs against real files in a temp folder with an injected clock, so no test waits on real time.
A file arrives at the later of its modified time and its change time. Synthetic entries set the
change time equal to the modified time unless a test says otherwise.
"""

import os
import shutil
import time

import pytest

from cron import triggers as T

# 2020-01-01T00:00:00Z: the modified time a copy keeps from an old source file.
OLD_MTIME = 1577836800.0


def _entry(name, size, mtime, folder="/watch", ctime=None):
    return {
        "name": name, "size": size, "mtime": mtime,
        "ctime": mtime if ctime is None else ctime, "path": f"{folder}/{name}",
    }


def _state(floor=0.0):
    return {"floor": floor, "seen": [], "pending": {}}


class TestObserveFolder:
    def test_a_new_file_is_returned_once_after_two_unchanged_polls(self):
        state = _state(floor=100.0)
        entries = [_entry("a.pdf", 10, 150.0)]
        assert T.observe_folder(state, entries, 200.0) == []
        assert T.observe_folder(state, entries, 260.0) == []
        ready = T.observe_folder(state, entries, 320.0)
        assert [item["name"] for item in ready] == ["a.pdf"]
        assert T.observe_folder(state, entries, 380.0) == []
        assert T.observe_folder(state, entries, 440.0) == []

    def test_a_growing_file_waits_until_its_size_holds(self):
        state = _state(floor=100.0)
        assert T.observe_folder(state, [_entry("big.zip", 10, 150.0)], 200.0) == []
        assert T.observe_folder(state, [_entry("big.zip", 20, 230.0)], 260.0) == []
        assert T.observe_folder(state, [_entry("big.zip", 30, 290.0)], 320.0) == []
        assert T.observe_folder(state, [_entry("big.zip", 30, 290.0)], 380.0) == []
        ready = T.observe_folder(state, [_entry("big.zip", 30, 290.0)], 440.0)
        assert [(item["name"], item["size"]) for item in ready] == [("big.zip", 30)]

    def test_files_at_or_before_the_floor_never_trigger(self):
        state = _state(floor=500.0)
        old = [_entry("old.pdf", 5, 400.0), _entry("edge.pdf", 5, 500.0)]
        for now in (510.0, 570.0, 630.0, 690.0):
            assert T.observe_folder(state, old, now) == []
        assert state["pending"] == {}

    def test_the_same_name_with_a_new_size_is_a_new_file(self):
        state = _state(floor=100.0)

        def poll(size, now):
            return T.observe_folder(state, [_entry("r.pdf", size, 150.0)], now)

        poll(10, 200.0)
        poll(10, 260.0)
        assert [item["size"] for item in poll(10, 320.0)] == [10]
        poll(12, 380.0)
        poll(12, 440.0)
        assert [item["size"] for item in poll(12, 500.0)] == [12]

    def test_files_that_vanish_leave_no_pending_state(self):
        state = _state(floor=100.0)
        T.observe_folder(state, [_entry("tmp.crdownload", 4, 150.0)], 200.0)
        assert "tmp.crdownload" in state["pending"]
        T.observe_folder(state, [], 260.0)
        assert state["pending"] == {}

    def test_evicted_entries_never_look_new_again(self, monkeypatch):
        monkeypatch.setattr(T, "SEEN_CAP", 2)
        state = _state(floor=100.0)
        entries = [_entry("a.pdf", 1, 110.0), _entry("b.pdf", 1, 120.0), _entry("c.pdf", 1, 130.0)]
        now = 200.0
        for _ in range(2):
            T.observe_folder(state, entries, now)
            now += 60
        ready = T.observe_folder(state, entries, now)
        now += 60
        assert [item["name"] for item in ready] == ["a.pdf", "b.pdf", "c.pdf"]
        assert [name for name, _, _ in state["seen"]] == ["b.pdf", "c.pdf"]
        assert state["floor"] == 110.0  # the evicted entry's mtime
        for _ in range(4):
            assert T.observe_folder(state, entries, now) == []
            now += 60

    def test_the_floor_never_moves_past_now(self, monkeypatch):
        monkeypatch.setattr(T, "SEEN_CAP", 1)
        state = _state(floor=100.0)
        future = [_entry("f1.pdf", 1, 9_999.0), _entry("f2.pdf", 1, 9_999.0)]
        for now in (200.0, 260.0, 320.0):
            T.observe_folder(state, future, now)
        assert state["floor"] <= 320.0

    def test_a_copy_with_an_old_modified_time_arrives_at_its_change_time(self):
        state = _state(floor=100.0)
        # A copy keeps the source's Date modified (50), but the copy was made after the floor (150).
        entries = [_entry("copy.pdf", 10, 50.0, ctime=150.0)]
        assert T.observe_folder(state, entries, 200.0) == []
        assert T.observe_folder(state, entries, 260.0) == []
        ready = T.observe_folder(state, entries, 320.0)
        assert [item["name"] for item in ready] == ["copy.pdf"]

    def test_a_file_older_than_the_floor_in_both_times_never_triggers(self):
        state = _state(floor=100.0)
        old = [_entry("old.pdf", 5, 50.0, ctime=60.0)]
        for now in (200.0, 260.0, 320.0, 380.0):
            assert T.observe_folder(state, old, now) == []
        assert state["pending"] == {}

    def test_an_evicted_file_with_a_later_change_time_cannot_look_new_again(self, monkeypatch):
        monkeypatch.setattr(T, "SEEN_CAP", 1)
        state = _state(floor=100.0)
        # y.pdf keeps an old modified time (50) but arrived at 110, so it is the first one evicted.
        entries = [_entry("y.pdf", 1, 50.0, ctime=110.0), _entry("x.pdf", 1, 120.0)]
        for now in (200.0, 260.0):
            assert T.observe_folder(state, entries, now) == []
        ready = T.observe_folder(state, entries, 320.0)
        assert [item["name"] for item in ready] == ["y.pdf", "x.pdf"]
        assert state["floor"] == 110.0  # the evicted file's arrival, not its older modified time
        for now in (380.0, 440.0, 500.0, 560.0):
            assert T.observe_folder(state, entries, now) == []

    def test_a_real_file_older_than_the_floor_in_both_times_never_triggers(self, tmp_path):
        path = tmp_path / "old.pdf"
        path.write_bytes(b"x")
        os.utime(path, (OLD_MTIME, OLD_MTIME))
        # A test cannot set the change time back on Linux, so the floor is put just above the real
        # change time. The modified time is old too, so the file is old on both counts.
        floor = os.stat(path).st_ctime + 10
        state = _state(floor=floor)
        for now in (floor + 60, floor + 120, floor + 180, floor + 240):
            assert T.observe_folder(state, T.list_watch_folder(str(tmp_path), "*.pdf"), now) == []
        assert state["pending"] == {}


# --- store path against real files -------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
    from cron import jobs_triggers as jt

    monkeypatch.setattr(jt, "_ticker_started", False)
    monkeypatch.setattr(jt, "_armed_stores", set())
    # A file's change time is the real time it was written, so about now. The clock runs an hour
    # ahead of real time, so that change time is always before the floor and never makes a test file
    # look new: each test decides its files by the modified time it writes. The arrival regression
    # test sets its own clock.
    clock = {"now": time.time() + 3600.0}
    monkeypatch.setattr(T, "now_epoch", lambda: clock["now"])
    return clock


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def _write(folder, name, size, mtime):
    path = folder / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def _folder_job(inbox, **extra):
    from cron.jobs import create_job

    trigger = {"type": "folder", "watch_dir": str(inbox), **extra}
    return create_job(prompt="Sort the new file", schedule=None, name="sorter", trigger=trigger)


def _collect(now):
    from cron.jobs_triggers import collect_trigger_dispatches

    return collect_trigger_dispatches(set(), now=now)


def _names(handed):
    return [item["trigger_event"]["file"]["name"] for item in handed]


def _settle(job_id, success=True):
    from cron.jobs import mark_job_run

    return mark_job_run(job_id, success, None if success else "boom")


def _stored(job_id):
    from cron.jobs import load_jobs

    return next(item for item in load_jobs() if item["id"] == job_id)


class TestFolderStore:
    def test_a_new_file_runs_once_after_it_settles(self, store, inbox):
        job = _folder_job(inbox)
        base = store["now"]
        _write(inbox, "a.pdf", 10, base + 10)
        assert _collect(base + 60) == []  # first sight
        assert _collect(base + 120) == []  # unchanged once
        handed = _collect(base + 180)  # unchanged twice: settled
        assert _names(handed) == ["a.pdf"]
        assert handed[0]["_execution_source"] == "folder"
        assert handed[0]["trigger_event"]["file"]["size_bytes"] == 10
        _settle(job["id"])
        assert _collect(base + 240) == []  # never again
        assert _collect(base + 300) == []

    def test_files_already_present_at_creation_never_trigger(self, store, inbox):
        base = store["now"]
        _write(inbox, "before.pdf", 5, base - 500)
        _folder_job(inbox)
        for step in range(1, 6):
            assert _collect(base + 60 * step) == []

    def test_a_file_copied_in_with_an_old_modified_time_triggers(self, store, inbox, tmp_path):
        # Explorer keeps the source's Date modified on a copy. The copy still arrived after the job
        # was created, and that is what counts. The floor here is real time, so the file's real
        # change time is compared against a real floor.
        floor = time.time() - 60  # the job was created a minute before the copy arrives
        store["now"] = floor
        job = _folder_job(inbox)
        source = tmp_path / "downloads" / "statement.pdf"
        source.parent.mkdir()
        source.write_bytes(b"%PDF-1.4 statement")
        os.utime(source, (OLD_MTIME, OLD_MTIME))
        shutil.copyfile(source, inbox / "statement.pdf")
        os.utime(inbox / "statement.pdf", (OLD_MTIME, OLD_MTIME))
        assert _collect(floor + 60) == []  # first sight
        assert _collect(floor + 120) == []  # unchanged once
        handed = _collect(floor + 180)  # unchanged twice: settled
        assert _names(handed) == ["statement.pdf"]
        _settle(job["id"])
        assert _collect(floor + 240) == []  # handed out once, never again

    def test_a_growing_file_is_picked_up_only_when_it_stops(self, store, inbox):
        base = store["now"]
        job = _folder_job(inbox)
        _write(inbox, "download.zip", 10, base + 10)
        assert _collect(base + 60) == []
        _write(inbox, "download.zip", 20, base + 70)
        assert _collect(base + 120) == []
        _write(inbox, "download.zip", 30, base + 130)
        assert _collect(base + 180) == []
        assert _collect(base + 240) == []
        handed = _collect(base + 300)
        assert [item["trigger_event"]["file"]["size_bytes"] for item in handed] == [30]
        _settle(job["id"])

    def test_the_hourly_budget_skips_the_excess_and_frees_up_after_an_hour(self, store, inbox):
        base = store["now"]
        job = _folder_job(inbox)
        for index in range(20):
            _write(inbox, f"f{index:02d}.pdf", 1 + index, base + 10)
        clock = base + 60
        dispatched = []
        for _ in range(30):  # every poll hands out at most one run; each run finishes at once
            handed = _collect(clock)
            dispatched += _names(handed)
            if handed:
                _settle(job["id"])
            clock += 60
        assert len(dispatched) == 20

        # Three more files arrive inside the same hour: the budget refuses them, and they are recorded.
        for name in ("late1.pdf", "late2.pdf", "late3.pdf"):
            _write(inbox, name, 5, clock)
        for _ in range(6):
            assert _collect(clock) == []
            clock += 60
        stored = _stored(job["id"])["trigger_state"]
        assert stored["skipped_total"] == 3
        assert {entry["reason"] for entry in stored["skipped"]} == {"rate_limit"}
        assert {entry["file"] for entry in stored["skipped"]} == {"late1.pdf", "late2.pdf", "late3.pdf"}

        # Once the window has passed, new files run again, and the skipped ones never re-trigger.
        clock = base + 5000
        _write(inbox, "after.pdf", 5, clock)
        seen_names = []
        for _ in range(3):
            seen_names += _names(_collect(clock))
            clock += 60
        assert seen_names == ["after.pdf"]

    def test_a_burst_beyond_the_queue_is_recorded_as_skipped(self, store, inbox):
        base = store["now"]
        job = _folder_job(inbox)
        for index in range(25):
            _write(inbox, f"burst{index:02d}.pdf", 1, base + 10)
        for step in range(1, 4):
            _collect(base + 60 * step)
        stored = _stored(job["id"])["trigger_state"]
        assert stored["skipped_total"] == 5
        assert {entry["reason"] for entry in stored["skipped"]} == {"queue_full"}
        assert len(stored["queue"]) <= T.QUEUE_CAP


class TestRetryAndReturn:
    def test_a_failed_run_is_retried_once_then_the_error_stays(self, store, monkeypatch):
        from cron import jobs_triggers as jt
        from cron.jobs import create_job

        monkeypatch.setattr(jt, "_ticker_started", True)
        job = create_job(prompt="Summarise", schedule=None, name="brief", trigger={"type": "startup"})
        first = _collect(store["now"])
        assert [item["trigger_event"]["kind"] for item in first] == ["startup"]
        _settle(job["id"], success=False)
        retry = _collect(store["now"] + 60)
        assert [item["trigger_event"]["attempt"] for item in retry] == [2]
        _settle(job["id"], success=False)
        assert _collect(store["now"] + 120) == []
        stored = _stored(job["id"])
        assert stored["last_status"] == "error" and stored["last_error"] == "boom"
        assert stored["enabled"] is True and stored["state"] == "scheduled"

    def test_a_hand_out_that_cannot_start_returns_to_the_queue(self, store, monkeypatch):
        from cron import jobs_triggers as jt
        from cron.jobs import create_job
        from cron.jobs_triggers import return_trigger_dispatch

        monkeypatch.setattr(jt, "_ticker_started", True)
        job = create_job(prompt="Summarise", schedule=None, name="brief", trigger={"type": "startup"})
        handed = _collect(store["now"])
        assert return_trigger_dispatch(job["id"], handed[0]["trigger_event"]) is True
        state = _stored(job["id"])["trigger_state"]
        assert [event["kind"] for event in state["queue"]] == ["startup"]
        assert "dispatched_at" not in state["queue"][0]
        assert "inflight" not in state and state["run_times"] == []
        assert len(_collect(store["now"] + 60)) == 1
