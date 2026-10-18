"""Ownership rollback when a delegation batch cannot finish construction."""

import threading
from types import SimpleNamespace

import pytest

from tools import delegate_tool as dt
from tools.delegate_tool_child_run import _attach_child


@pytest.mark.parametrize("failure_type", [ValueError, RuntimeError, KeyboardInterrupt])
def test_later_construction_failure_releases_unstarted_siblings(monkeypatch, failure_type):
    existing = object()
    parent = SimpleNamespace(_active_children=[existing], _active_children_lock=threading.Lock())
    built = []

    def build(**kwargs):
        if kwargs["task_index"] == 2:
            raise failure_type("third child construction failed")
        child = SimpleNamespace(closed=False)
        child.close = lambda: setattr(child, "closed", True)
        _attach_child(parent, child)
        built.append(child)
        return child

    monkeypatch.setattr(dt, "_build_child_preserving_parent_tools", build)
    creds = dict(provider="openrouter", base_url="http://127.0.0.1:1/v1", api_key="test",
                 api_mode="chat_completions", model="test-model")

    def construct():
        return dt._build_children(
            [{"goal": f"Inspect independent component {i}"} for i in range(3)],
            [None] * 3, creds, top_role="leaf", max_iterations=2, parent_agent=parent,
            routing_cfg={}, live_deleg_id=None, live_writers=[],
        )

    try:
        if failure_type is ValueError:
            children, error = construct()
            assert children == []
            assert error == "third child construction failed"
        else:
            with pytest.raises(failure_type, match="third child construction failed"):
                construct()
        assert len(built) == 2
        assert all(child.closed for child in built)
        assert parent._active_children == [existing]
    finally:
        # Failed assertions on the broken baseline must not leak test-owned objects.
        for child in built:
            child.close()
        parent._active_children[:] = [existing]


def test_successful_build_transfers_children_without_closing_them(monkeypatch):
    parent = SimpleNamespace(_active_children=[], _active_children_lock=threading.Lock())
    built = []

    def build(**_kwargs):
        child = SimpleNamespace(closed=False)
        child.close = lambda: setattr(child, "closed", True)
        _attach_child(parent, child)
        built.append(child)
        return child

    monkeypatch.setattr(dt, "_build_child_preserving_parent_tools", build)
    creds = dict(provider="openrouter", base_url="http://127.0.0.1:1/v1", api_key="test",
                 api_mode="chat_completions", model="test-model")
    children, error = dt._build_children(
        [{"goal": "Inspect component"}], [None], creds, top_role="leaf", max_iterations=2,
        parent_agent=parent, routing_cfg={}, live_deleg_id=None, live_writers=[],
    )
    assert error is None
    assert [child for _i, _task, child in children] == built
    assert parent._active_children == built
    assert not any(child.closed for child in built)

@pytest.mark.parametrize("failure_type", [ValueError, RuntimeError])
def test_public_delegate_failure_releases_real_child_database(tmp_path, monkeypatch, failure_type, local_model):
    import json

    from hermes_state import SessionDB
    from hermes_state_registry import stats
    from run_agent import AIAgent
    import tools.delegate_tool_config as dtc

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    url, requests = local_model
    config = {"worktree_isolation": False, "max_concurrent_children": 3, "max_iterations": 2}
    monkeypatch.setattr(dt, "_load_config", lambda: config)
    monkeypatch.setattr(dtc, "_load_config", lambda: config)
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("partial-build-parent", source="test")
    parent = AIAgent(
        session_id="partial-build-parent", session_db=db, api_key="test-key",
        base_url=url, provider="openrouter",
        api_mode="chat_completions", model="test-model", quiet_mode=True,
        skip_context_files=True, skip_memory=True, save_trajectories=False,
        enabled_toolsets=["file"],
    )
    baseline = stats()
    constructed = []
    original_init = AIAgent.__init__

    def fail_second_child(self, *args, **kwargs):
        if kwargs.get("parent_session_id") == parent.session_id:
            if constructed:
                raise failure_type("controlled second constructor failure")
            original_init(self, *args, **kwargs)
            constructed.append(self)
        else:
            original_init(self, *args, **kwargs)

    monkeypatch.setattr(AIAgent, "__init__", fail_second_child)

    def launch():
        return dt.delegate_task(
            tasks=[{"goal": "Inspect first component"}, {"goal": "Inspect second component"}],
            parent_agent=parent, background=False,
        )

    try:
        if failure_type is ValueError:
            result = json.loads(launch())
            assert "controlled second constructor failure" in result["error"]
        else:
            with pytest.raises(failure_type, match="controlled second constructor failure"):
                launch()
        assert len(constructed) == 1
        print("rollback census", {"db_before": baseline, "db_after": stats(),
                                  "active_children": len(parent._active_children),
                                  "child_owns_db": constructed[0]._owns_session_db})
        assert parent._active_children == []
        assert constructed[0]._owns_session_db is False
        assert stats() == baseline
        assert db.get_session(parent.session_id) is not None
        assert requests == [], "Rejected construction must never invoke the model"

        def record_retry(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            if kwargs.get("parent_session_id") == parent.session_id:
                constructed.append(self)

        monkeypatch.setattr(AIAgent, "__init__", record_retry)
        retry = json.loads(launch())
        assert [entry["status"] for entry in retry["results"]] == ["completed", "completed"]
        assert [entry["summary"] for entry in retry["results"]] == ["ISOLATED_CHILD_DONE"] * 2
        assert len(requests) == 2
        assert parent._active_children == []
        assert stats() == baseline
        for child in constructed[1:]:
            messages = db.get_messages(child.session_id)
            assert any(message.get("content") == "ISOLATED_CHILD_DONE" for message in messages)
            assert db.get_session(child.session_id)["parent_session_id"] == parent.session_id
    finally:
        # Ensure baseline reproduction itself cannot leave live handles behind.
        for child in constructed:
            if child._owns_session_db:
                child.close()
        parent.close()
        db.close()

@pytest.fixture
def local_model():
    """Only inference is synthetic; both real child loops must reach HTTP together."""
    import http.server
    import json

    requests = []
    barrier = threading.Barrier(2)

    class Provider(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(404)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "messages" not in request:
                self.send_error(404)
                return
            requests.append(request)
            barrier.wait(timeout=15)
            choice = {"index": 0, "message": {"role": "assistant", "content": "ISOLATED_CHILD_DONE"},
                      "finish_reason": "stop"}
            response = {"id": "local-completion", "object": "chat.completion", "created": 1,
                        "model": "test-model", "choices": [choice],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}
            content_type = "application/json"
            if request.get("stream"):
                response["object"] = "chat.completion.chunk"
                choice["delta"] = choice.pop("message")
                raw = ("data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()

@pytest.mark.parametrize("close_raises", [False, True])
def test_postconfiguration_failure_reclaims_current_child(monkeypatch, close_raises):
    parent = SimpleNamespace(_active_children=[], _active_children_lock=threading.Lock())
    built = []

    class Child:
        def __init__(self, index):
            self.index = index
            self.close_calls = 0

        @property
        def _delegate_worktree_required(self):
            return False

        @_delegate_worktree_required.setter
        def _delegate_worktree_required(self, value):
            if self.index == 1:
                raise ValueError("postconfiguration failed")

        def close(self):
            self.close_calls += 1
            if self.index == 0 and close_raises:
                raise RuntimeError("first close failed")

    def build(**kwargs):
        child = Child(kwargs["task_index"])
        _attach_child(parent, child)
        built.append(child)
        return child

    monkeypatch.setattr(dt, "_build_child_preserving_parent_tools", build)
    creds = dict(provider="openrouter", base_url="http://127.0.0.1:1/v1", api_key="test",
                 api_mode="chat_completions", model="test-model")
    with pytest.raises(ValueError, match="postconfiguration failed"):
        dt._build_children(
            [{"goal": "Inspect first component"}, {"goal": "Inspect second component"}],
            [None] * 2, creds, top_role="leaf", max_iterations=2, parent_agent=parent,
            routing_cfg={}, live_deleg_id=None, live_writers=[],
        )
    assert [child.close_calls for child in built] == [1, 1]
    assert parent._active_children == []
