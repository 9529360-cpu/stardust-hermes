"""Regression tests for dashboard sidebar scan coalescing."""

import inspect
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hermes_cli.web_routers import profiles

# Bounds a genuine hang only; no assertion depends on how fast a correct run gets there.
_HANG_GUARD_SECONDS = 30.0


class SidebarCacheTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", 5.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        profiles._sidebar_profile_cache_clear()
        self.addCleanup(profiles._sidebar_profile_cache_clear)

    def _run_burst(self, call, workers, entered, release):
        """Run ``workers`` concurrent ``call()``s against a scan that sets ``entered`` and then
        waits for ``release``; return the results in submission order.

        The scan is released only once it is in flight and every caller has made its call, so
        the whole burst overlaps it however slowly threads start or a cold first call imports
        its lazy dependencies (``tui_gateway.server``, ~250 modules, can take over a second on
        a loaded CI runner). No outcome races a clock: the long TTL serves any late lookup from
        the burst's one scan, and the guard only bounds a hang.
        """
        arrived = threading.Semaphore(0)

        def caller():
            arrived.release()
            return call()

        with mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", 3600.0), \
                ThreadPoolExecutor(max_workers=workers) as pool:
            try:
                futures = [pool.submit(caller) for _ in range(workers)]
                for future in futures:
                    # A call that ends before the scan starts wakes the wait below, so it
                    # fails with its own error instead of as a hang.
                    future.add_done_callback(lambda _: entered.set())
                for _ in range(workers):
                    self.assertTrue(arrived.acquire(timeout=_HANG_GUARD_SECONDS))
                self.assertTrue(entered.wait(timeout=_HANG_GUARD_SECONDS))
                # Nothing is cached before the held scan finishes, so a caller that has already
                # returned answered without it.
                for future in futures:
                    if future.done():
                        future.result()
                        self.fail("a caller returned before the in-flight scan finished")
            finally:
                release.set()  # a failed run must not leave workers parked on the scan
            return [future.result(timeout=_HANG_GUARD_SECONDS) for future in futures]

    def test_profile_cache_uses_db_and_wal_fingerprint_and_defensive_copies(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "state.db"
            wal_path = Path(f"{db_path}-wal")
            db_path.write_bytes(b"db-v1")
            wal_path.write_bytes(b"wal-v1")
            first_fingerprint = profiles._sidebar_db_fingerprint(db_path)
            first_key = (str(db_path), first_fingerprint, False, 0, (), 50, 100, ())
            payload = {"recents": None, "cron": [{"id": "one"}], "messaging": []}

            profiles._sidebar_profile_cache_put(first_key, payload)
            cached = profiles._sidebar_profile_cache_get(first_key)
            cached["cron"][0]["id"] = "mutated"
            self.assertEqual(
                profiles._sidebar_profile_cache_get(first_key)["cron"][0]["id"],
                "one",
            )

            wal_path.write_bytes(b"wal-v2-is-different")
            second_fingerprint = profiles._sidebar_db_fingerprint(db_path)
            second_key = (str(db_path), second_fingerprint, False, 0, (), 50, 100, ())
            self.assertNotEqual(first_fingerprint, second_fingerprint)
            self.assertIsNone(profiles._sidebar_profile_cache_get(second_key))

            profiles._sidebar_profile_cache_put(second_key, payload)
            self.assertIsNone(profiles._sidebar_profile_cache_get(first_key))

    def test_profile_cache_is_lru_bounded(self):
        with mock.patch.object(profiles, "_SIDEBAR_PROFILE_CACHE_MAX_ENTRIES", 2):
            for index in range(3):
                key = (f"/db/{index}", (index, None), False, 0, (), 50, 100, ())
                profiles._sidebar_profile_cache_put(key, {"index": index})
            self.assertEqual(len(profiles._SIDEBAR_PROFILE_CACHE), 2)

    def test_applies_defaults_and_returns_defensive_copies(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan(profile="all", limit=20):
            nonlocal calls
            calls += 1
            return {"profile": profile, "rows": [{"limit": limit}]}

        first = scan()
        first["rows"][0]["limit"] = 999
        second = scan(profile="all", limit=20)

        self.assertEqual(calls, 1)
        self.assertEqual(second, {"profile": "all", "rows": [{"limit": 20}]})

    def test_coalesces_concurrent_identical_scans(self):
        workers = 12
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        @profiles._sidebar_singleflight_cache
        def scan(profile="all"):
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            self.assertTrue(release.wait(timeout=_HANG_GUARD_SECONDS))
            return {"profile": profile, "rows": []}

        results = self._run_burst(lambda: scan("default"), workers, entered, release)

        self.assertEqual(calls, 1)
        self.assertEqual(results, [{"profile": "default", "rows": []}] * workers)

    def test_expires(self):
        clock = iter((100.0, 100.0, 100.0, 106.0, 106.0, 106.0))
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            return {"generation": calls}

        with mock.patch.object(profiles.time, "monotonic", side_effect=clock):
            self.assertEqual(scan(), {"generation": 1})
            self.assertEqual(scan(), {"generation": 2})
        self.assertEqual(calls, 2)

    def test_does_not_cache_failures(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")
            return {"ok": True}

        with self.assertRaisesRegex(RuntimeError, "transient"):
            scan()
        self.assertEqual(scan(), {"ok": True})
        self.assertEqual(scan(), {"ok": True})
        self.assertEqual(calls, 2)

    def test_profile_errors_have_shorter_ttl_and_recover_without_fleet_rescan(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"errors": [{"profile": "default", "error": "disk I/O error"}],
                        "recents": {"sessions": []}}
            return {"errors": [], "recents": {"sessions": [{"id": "recovered"}]}}

        clock = [100.0]
        with mock.patch.object(profiles.time, "monotonic", side_effect=lambda: clock[0]):
            first = scan()
            first["recents"]["sessions"].append({"id": "mutated"})
            for _ in range(8):
                self.assertEqual(scan()["recents"]["sessions"], [])
            self.assertEqual(calls, 1)
            clock[0] = 101.99
            self.assertEqual(scan()["errors"][0]["error"], "disk I/O error")
            clock[0] = 102.0
            self.assertEqual(scan()["recents"]["sessions"], [{"id": "recovered"}])
            self.assertEqual(calls, 2)
            clock[0] = 106.0
            self.assertEqual(scan()["recents"]["sessions"], [{"id": "recovered"}])
            self.assertEqual(calls, 2)

    def test_concurrent_profile_errors_share_one_scan(self):
        workers = 8
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            entered.set()
            self.assertTrue(release.wait(timeout=_HANG_GUARD_SECONDS))
            return {"errors": [{"profile": "default", "error": "locked"}], "rows": []}

        # Keep error TTL long enough that scheduling delays cannot turn a late
        # worker into a second scan; the TTL itself is tested with a fake clock above.
        with mock.patch.object(profiles, "_SIDEBAR_ERROR_CACHE_TTL_SECONDS", 3600.0):
            results = self._run_burst(scan, workers, entered, release)
        self.assertEqual(calls, 1)
        self.assertEqual(results, [results[0]] * workers)
        self.assertEqual(len({id(result) for result in results}), workers)

    def test_can_be_disabled(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            return calls

        with mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", 0.0):
            self.assertEqual((scan(), scan()), (1, 2))

    def test_preserves_fastapi_signature(self):
        def scan(profile: str = "all", limit: int = 20):
            return profile, limit

        wrapped = profiles._sidebar_singleflight_cache(scan)

        self.assertEqual(inspect.signature(wrapped), inspect.signature(scan))

    def test_projects_tree_coalesces_concurrent_scans_and_returns_copies(self):
        # /api/profiles/projects/tree fans out over every profile's state.db; desktop
        # background sync + sidebar refreshes overlap identical requests. One scan must
        # serve the whole burst, and no two callers may share the same payload object.
        workers = 8
        entered = threading.Event()
        release = threading.Event()
        scans = 0
        scans_lock = threading.Lock()

        def fake_read(name, home, errors, fn):
            nonlocal scans
            with scans_lock:
                scans += 1
            entered.set()
            self.assertTrue(release.wait(timeout=_HANG_GUARD_SECONDS))
            return None

        with mock.patch.object(profiles, "_profile_targets", return_value=[("default", Path("/nonexistent"))]), \
                mock.patch.object(profiles, "_read_profile_db", side_effect=fake_read):
            results = self._run_burst(profiles.get_profiles_projects_tree, workers, entered, release)

        self.assertEqual(scans, 1)
        self.assertEqual(len({id(r) for r in results}), workers)
        self.assertEqual(results, [results[0]] * workers)
        self.assertEqual(results[0]["projects"], [])


if __name__ == "__main__":
    unittest.main()
