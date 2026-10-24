"""F2.3: STARDUST_DEFER_CONTEXT_PROBE — construction must not run the network
context probe; the probe runs in a background thread and the resolved value is
identical (same resolver, same inputs).

Regression for DISCOVERY-2 U1 (the 2 s blackhole connect in AIAgent.__init__).
"""

import threading
import time
from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor


def _make_compressor(**kw):
    return ContextCompressor(
        model="f23-stub",
        threshold_percent=0.85,
        protect_first_n=1,
        protect_last_n=1,
        quiet_mode=True,
        **kw,
    )


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("STARDUST_DEFER_CONTEXT_PROBE", raising=False)


class TestDeferContextProbeFlag:
    def test_flag_default_off(self, monkeypatch):
        from agent.model_metadata import defer_context_probe_enabled
        assert defer_context_probe_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
    def test_flag_on_values(self, monkeypatch, val):
        monkeypatch.setenv("STARDUST_DEFER_CONTEXT_PROBE", val)
        from agent.model_metadata import defer_context_probe_enabled
        assert defer_context_probe_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "off", "", "garbage"])
    def test_flag_off_values(self, monkeypatch, val):
        monkeypatch.setenv("STARDUST_DEFER_CONTEXT_PROBE", val)
        from agent.model_metadata import defer_context_probe_enabled
        assert defer_context_probe_enabled() is False


class TestBackgroundProbeSingleFlight:
    def test_resolve_runs_probe_once_under_concurrency(self):
        calls = []

        def slow_resolver(model, **kw):
            calls.append(threading.get_ident())
            time.sleep(0.05)
            return 123456

        with patch("agent.context_compressor.get_model_context_length", side_effect=slow_resolver):
            c = _make_compressor()
            results = []

            def reader():
                results.append(c.context_length)

            threads = [threading.Thread(target=reader) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert c.context_length == 123456
        assert len(calls) == 1, f"probe ran {len(calls)} times, expected single flight"

    def test_start_context_probe_resolves_offthread(self):
        started = threading.Event()

        def resolver(model, **kw):
            started.set()  # runs on the probe thread, not the caller's
            return 777777

        with patch("agent.context_compressor.get_model_context_length", side_effect=resolver):
            c = _make_compressor()
            c.start_context_probe()
            assert started.wait(timeout=5), "background probe never ran"
            deadline = time.monotonic() + 5
            while c._resolved_context_length is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert c._resolved_context_length == 777777

    def test_start_context_probe_idempotent(self):
        spawns = []
        real_thread = threading.Thread

        def counting_thread(*a, **kw):
            spawns.append(1)
            return real_thread(*a, **kw)

        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = _make_compressor()
            with patch("threading.Thread", side_effect=counting_thread):
                c.start_context_probe()
                c.start_context_probe()
        assert len(spawns) == 1

    def test_resolved_context_length_within_never_probes(self):
        def must_not_run(model, **kw):
            raise AssertionError("resolved_context_length_within must not trigger a probe")

        with patch("agent.context_compressor.get_model_context_length", side_effect=must_not_run):
            c = _make_compressor()
            # No probe started: returns None immediately, no network, no resolver call.
            assert c.resolved_context_length_within(0.05) is None

    def test_resolved_context_length_within_waits_for_background_value(self):
        release = threading.Event()

        def resolver(model, **kw):
            release.wait(timeout=5)
            return 321321

        with patch("agent.context_compressor.get_model_context_length", side_effect=resolver):
            c = _make_compressor()
            c.start_context_probe()
            assert c.resolved_context_length_within(0.05) is None  # still blocked
            release.set()
            assert c.resolved_context_length_within(5) == 321321


class TestFloorCheckDeferred:
    def test_enforce_minimum_context_defers_when_flag_on(self, monkeypatch):
        monkeypatch.setenv("STARDUST_DEFER_CONTEXT_PROBE", "1")
        from agent.agent_init import _enforce_minimum_context

        def must_not_run(model, **kw):
            raise AssertionError("floor check must not run the probe inline under the flag")

        with patch("agent.context_compressor.get_model_context_length", side_effect=must_not_run):
            c = _make_compressor()
            agent = type("A", (), {})()
            agent.context_compressor = c
            agent.provider = "custom"
            agent._config_context_length = None
            # Unresolved window: no ValueError, no probe — the check is deferred.
            _enforce_minimum_context(agent, budget_s=0.0)
            assert c._resolved_context_length is None

    def test_enforce_minimum_context_still_raises_on_small_window(self, monkeypatch):
        monkeypatch.setenv("STARDUST_DEFER_CONTEXT_PROBE", "1")
        from agent.agent_init import _enforce_minimum_context

        with patch("agent.context_compressor.get_model_context_length", return_value=32000):
            c = _make_compressor()
            c.start_context_probe()
            deadline = time.monotonic() + 5
            while c._resolved_context_length is None and time.monotonic() < deadline:
                time.sleep(0.01)
            agent = type("A", (), {})()
            agent.context_compressor = c
            agent.provider = "custom"
            agent.model = "f23-stub"
            agent._config_context_length = None
            with pytest.raises(ValueError, match="below the minimum"):
                _enforce_minimum_context(agent, budget_s=0.0)

    def test_enforce_minimum_context_sync_path_unchanged_flag_off(self):
        from agent.agent_init import _enforce_minimum_context

        with patch("agent.context_compressor.get_model_context_length", return_value=32000):
            c = _make_compressor()
            agent = type("A", (), {})()
            agent.context_compressor = c
            agent.provider = "custom"
            agent.model = "f23-stub"
            agent._config_context_length = None
            # Flag off: the property resolves synchronously (today's behaviour) and raises.
            with pytest.raises(ValueError, match="below the minimum"):
                _enforce_minimum_context(agent, budget_s=0.0)
