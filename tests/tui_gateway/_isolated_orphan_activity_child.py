"""Lightweight real-child helper for isolated orphan activity integration tests."""

from pathlib import Path
import sys
import threading
import time

from agent.activity_tracking import ActivityTrackingMixin
from agent.session_activity import build_activity_snapshot
from tui_gateway import compute_host
from tui_gateway import server
from tui_gateway.compute_host import run_host


def _session(sid: str) -> dict:
    return {
        "agent": None, "agent_ready": threading.Event(), "session_key": sid,
        "history": [], "history_version": 0, "history_lock": threading.Lock(),
        "running": True, "transport": server._detached_ws_transport,
        "attached_images": [], "cols": 80, "source": "desktop", "inflight_turn": None,
    }


def run_child(mode: str, directory: str) -> None:
    class Agent(ActivityTrackingMixin):
        def __init__(self, sid):
            self.session_id = sid
            self._interrupt = threading.Event()
            if mode == "previous":
                self._touch_activity("previous turn")

        def get_activity_summary(self):
            return build_activity_snapshot(
                last_activity_at=getattr(self, "_last_activity_ts", None),
                last_activity_description="test provider",
            )

        def clear_interrupt(self):
            self._interrupt.clear()

        def interrupt(self, **_kwargs):
            self._interrupt.set()

        def run_conversation(self, *args, **_kwargs):
            Path(directory, "provider-started").touch()
            deadline = time.monotonic() + 20
            while not self._interrupt.wait(0.05) and time.monotonic() < deadline:
                if Path(directory, "release").exists():
                    break
                if mode == "fresh" and args[0] != "next":
                    self._touch_activity("provider wait")
                elif mode == "stale":
                    self._last_activity_ts = time.time() - 3600
            return {"final_response": "done", "interrupted": self._interrupt.is_set()}

    def init(sid, key, agent, history, **_kwargs):
        session = _session(sid)
        session.update(
            agent=agent, running=False, transport=None, image_counter=0,
            slash_worker=None, show_reasoning=False, tool_progress_mode="all",
        )
        server._sessions[sid] = session

    server._make_agent = lambda sid, *_args, **_kwargs: Agent(sid)
    server._init_session = init
    server._wire_callbacks = lambda *_args: None
    server._sync_agent_model_with_config = lambda *_args: None
    server._register_session_cwd = lambda *_args: None
    server._tts_stream_begin = lambda: None
    server._sync_session_key_after_compress = lambda *_args, **_kwargs: None
    server._get_usage = lambda *_args: {}

    original_shutdown = compute_host.ComputeHost.shutdown
    original_handle_shutdown = compute_host.ComputeHost._handle_shutdown

    def record_shutdown(self, *, reason="shutdown", wait=10.0):
        marker = Path(directory, "child-shutdown-reasons")
        prior = marker.read_text(encoding="utf-8") if marker.exists() else ""
        marker.write_text(prior + reason + "\n", encoding="utf-8")
        return original_shutdown(self, reason=reason, wait=wait)

    def record_handle_shutdown(self, frame):
        Path(directory, "child-explicit-shutdown").write_text(str(frame), encoding="utf-8")
        return original_handle_shutdown(self, frame)

    compute_host.ComputeHost.shutdown = record_shutdown
    compute_host.ComputeHost._handle_shutdown = record_handle_shutdown
    run_host(stdout=sys.__stdout__)


if __name__ == "__main__":
    run_child(sys.argv[1], sys.argv[2])