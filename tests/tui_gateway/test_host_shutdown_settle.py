"""Opt-in normal shutdown accounting at the real child-process boundary."""
import io
import json
import sys
import threading
import time

import pytest

from tui_gateway.compute_host import ComputeHost
from tui_gateway.host_supervisor import HostSupervisor


@pytest.fixture(params=[True])
def supervisor(tmp_path, monkeypatch, request):
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "dashboard:\n  compute_host_shutdown_settle: " + str(request.param).lower() + "\n",
        encoding="utf-8")
    child = tmp_path / "host.py"
    child.write_text(
        "import time\n"
        "from tui_gateway.compute_host import ComputeHost, run_host\n"
        "def turn(self, frame):\n"
        " if frame.get('quick'): time.sleep(.05); self._reply('turn.end', frame['sid'], frame['request_id'])\n"
        " else: time.sleep(60)\n"
        "ComputeHost._run_real_turn = turn\n"
        "ComputeHost._handle_control = lambda self, frame: None\n"
        "run_host()\n", encoding="utf-8")
    host = HostSupervisor(argv=[sys.executable, str(child)], registry_path=tmp_path / "reg.json")
    yield host
    host.shutdown()


def test_shutdown_settles_turns_controls_and_restart(supervisor):
    host = supervisor
    callbacks = []
    for i in range(100):
        host.submit_turn({"sid": str(i), "request_id": str(i)}, on_complete=callbacks.append)
    replies = []
    waiter = threading.Thread(target=lambda: replies.append(
        host.control("control", route_name="session.save", timeout=3)))
    waiter.start()
    deadline = time.monotonic() + 2
    while not host._pending_controls and time.monotonic() < deadline:
        time.sleep(.005)
    host.shutdown()
    waiter.join(4)
    assert len(callbacks) == len({f["request_id"] for f in callbacks}) == 100
    assert {f["reason"] for f in callbacks} == {"shutdown"}
    assert host._pending_turns == {}
    assert replies[0]["reason"] == "shutdown"
    with pytest.raises(RuntimeError, match="shutting down"):
        host.submit_turn({"sid": "rejected"})
    host.start()
    complete = threading.Event()
    host.submit_turn({"sid": "fresh", "quick": True}, on_complete=lambda f: complete.set())
    assert complete.wait(3)


@pytest.mark.parametrize("iteration", range(10))
def test_real_success_during_shutdown_wins(supervisor, iteration):
    frames = []
    supervisor.submit_turn({"sid": "success", "quick": True}, on_complete=frames.append)
    supervisor.shutdown()
    assert len(frames) == 1
    assert frames[0]["type"] == "turn.end"
    assert supervisor._pending_turns == {}


@pytest.mark.parametrize("supervisor", [False], indirect=True)
def test_disabled_preserves_pending(supervisor):
    frames = []
    supervisor.submit_turn({"sid": "blocked"}, on_complete=frames.append)
    supervisor.shutdown()
    assert frames == []
    assert len(supervisor._pending_turns) == 1


def test_shutdown_callback_cannot_restart_or_interrupt_settlement(supervisor):
    frames = []

    def reenter(frame):
        frames.append(frame)
        with pytest.raises(RuntimeError, match="shutting down"):
            supervisor.start()
        raise ValueError("callback failure must not strand other turns")

    supervisor.submit_turn({"sid": "a"}, on_complete=reenter)
    supervisor.submit_turn({"sid": "b"}, on_complete=frames.append)
    supervisor.rpc_sink = lambda frame: (_ for _ in ()).throw(ValueError("sink failed"))
    supervisor.shutdown()
    assert len(frames) == 2
    assert supervisor._pending_turns == {}


def test_child_rejects_admission_after_close():
    out = io.StringIO()
    host = ComputeHost(stdout=out, heartbeat_secs=0)
    try:
        host.handle_frame({"type": "shutdown", "settle_pending": True})
        host.handle_frame({"type": "turn.start", "sid": "late", "request_id": "late"})
        frames = [json.loads(line) for line in out.getvalue().splitlines()]
        assert frames[-1]["type"] == "turn.error"
        assert frames[-1]["reason"] == "shutdown"
    finally:
        host.close()
