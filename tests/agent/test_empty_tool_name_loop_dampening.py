"""Regression for #47967 — empty-name phantom tool calls.

Weak open models (mimo, nemotron-class) that see tool-call XML/JSON sitting in
file contents or tool output get *primed* and emit their own structured tool
calls that mimic the payload — usually with an empty/whitespace ``name``. Those
calls can't be fuzzy-repaired toward a real tool, so the dispatch loop returns an
error and the model retries. Before this fix, every empty-name error dumped the
full tool catalog back to the model, which fed the priming loop more names to
mimic and inflated context 3-4x across the retry budget.

The fix: a blank/whitespace-only tool name gets a terse anti-priming error that
tells the model in-context tool-call syntax is DATA, with NO catalog dump. A
genuinely-wrong-but-nonempty name (an actual typo) still gets the full catalog
so the model can self-correct.

These assert the *behavior contract* of the dispatch branch (what content goes
back to the model for each name shape), exercised end-to-end through
``AIAgent.run_conversation`` against an in-process mock provider — not a snapshot
of the message string.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

# Repo root = three levels up from tests/agent/<file>.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class _MockHandler(BaseHTTPRequestHandler):
    # Set by the fixture before each request cycle.
    captured_requests: list = []
    response_queue: list = []

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length).decode())
        type(self).captured_requests.append(req)
        is_stream = req.get("stream") is True
        if type(self).response_queue:
            resp = type(self).response_queue.pop(0)
        else:
            resp = _text_resp("DONE")
        msg = resp["choices"][0]["message"]
        if is_stream:
            content = msg.get("content") or ""
            tcs = msg.get("tool_calls")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [{"id": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}]
            if content:
                chunks.append({"id": "m", "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]})
            if tcs:
                for ti, tc in enumerate(tcs):
                    chunks.append({"id": "m", "choices": [{"index": 0, "delta": {"tool_calls": [{
                        "index": ti, "id": tc["id"], "type": "function",
                        "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}]}, "finish_reason": None}]})
            chunks.append({"id": "m", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tcs else "stop"}]})
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            body = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a, **kw):  # silence the default stderr logging
        pass


def _tc_resp(name: str, args: str = "{}") -> dict:
    return {
        "id": "m",
        "choices": [{"index": 0, "message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": name, "arguments": args}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


def _batch_tc_resp(calls: list[tuple[str, str]]) -> dict:
    """Multi-call batch response: calls = [(name, arguments), ...]."""
    return {
        "id": "m",
        "choices": [{"index": 0, "message": {
            "role": "assistant", "content": "",
            "tool_calls": [
                {"id": f"call_{i}", "type": "function",
                 "function": {"name": name, "arguments": args}}
                for i, (name, args) in enumerate(calls)
            ]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


def _text_resp(text: str) -> dict:
    return {
        "id": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


@pytest.fixture()
def agent_env():
    """Spin up the mock provider + an isolated STARDUST_HOME, yield (agent, helpers)."""
    _MockHandler.captured_requests = []
    _MockHandler.response_queue = []
    srv = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    test_home = tempfile.mkdtemp(prefix="hermes_e2e_47967_")
    os.makedirs(os.path.join(test_home, ".stardust"))
    prev_home = os.environ.get("STARDUST_HOME")
    os.environ["STARDUST_HOME"] = os.path.join(test_home, ".stardust")

    # Import fresh so the patched conversation_loop is exercised even when the
    # module was imported earlier in the same worker.
    saved_modules = dict(sys.modules)  # restored in finally — see below
    for mod in list(sys.modules):
        if mod == "run_agent" or mod.startswith("agent.") or mod.startswith("tools.") or mod.startswith("hermes_"):
            del sys.modules[mod]
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="test-key", base_url=f"http://127.0.0.1:{port}/v1",
        provider="openai-compat", model="test-model",
        max_iterations=10, enabled_toolsets=[],
        quiet_mode=True, skip_context_files=True, skip_memory=True,
        save_trajectories=False, platform="cli",
    )
    agent.valid_tool_names = {"terminal", "read_file", "write_file", "execute_code", "session_search"}

    try:
        yield agent, _MockHandler
    finally:
        srv.shutdown()
        shutil.rmtree(test_home, ignore_errors=True)
        # Put the original module objects back: sibling test files hold module-level
        # references into hermes_cli.*/tools.* and their monkeypatches would otherwise
        # land on modules the app no longer imports.
        sys.modules.clear()
        sys.modules.update(saved_modules)
        for _name, _mod in saved_modules.items():
            _parent, _, _child = _name.rpartition(".")
            if _parent and _parent in saved_modules:
                try:
                    setattr(saved_modules[_parent], _child, _mod)
                except Exception:
                    pass
        if prev_home is None:
            os.environ.pop("STARDUST_HOME", None)
        else:
            os.environ["STARDUST_HOME"] = prev_home


def _tool_results(handler) -> list[str]:
    out = []
    for req in handler.captured_requests:
        for m in req.get("messages", []):
            if m.get("role") == "tool":
                out.append(m.get("content", ""))
    return out


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t "])
def test_empty_tool_name_gets_terse_error_no_catalog(agent_env, blank):
    """A blank/whitespace tool name must NOT trigger a full tool-catalog dump."""
    agent, handler = agent_env
    handler.response_queue.append(_tc_resp(blank, "{}"))
    handler.response_queue.append(_text_resp("Recovered in plain text."))

    agent.run_conversation("read ./payload and report", conversation_history=[], task_id="t")

    joined = " ".join(_tool_results(handler))
    assert "tool name was empty" in joined
    # The whole point: do not feed the priming loop the catalog of names.
    assert "Available tools:" not in joined




# ── Mixed batches: valid calls execute, invalid calls get error results ──
#
# Degrading models (observed with gpt-5.6 past ~350K input; jonny's July 2026
# report) emit batches like 6 named calls + 1 blank-name call. Before the fix,
# the whole turn was voided ("Skipped: another tool call in this turn used an
# invalid name") and three such batches halted the session as partial even
# though most of the model's work was coherent.




def test_mixed_batch_preserves_tool_call_result_pairing(agent_env):
    """Every emitted tool_call keeps a matching tool result (provider invariant)."""
    agent, handler = agent_env
    agent.valid_tool_names = agent.valid_tool_names | {"todo"}
    handler.response_queue.append(_batch_tc_resp([("todo", "{}"), ("", "{}")]))
    handler.response_queue.append(_text_resp("done"))

    result = agent.run_conversation("track work", conversation_history=[], task_id="t")

    msgs = result["messages"]
    tc_ids = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
            tc_ids.extend(tc["id"] for tc in m["tool_calls"])
    result_ids = [
        m.get("tool_call_id") or "" for m in msgs
        if isinstance(m, dict) and m.get("role") == "tool"
    ]
    # Both the valid and blank call must appear in the assistant message,
    # and each must have exactly one matching tool result.
    assert set(tc_ids) == {"call_0", "call_1"}
    assert sorted(result_ids) == sorted(tc_ids)
    assert all(
        isinstance(message.get("timestamp"), float)
        for message in msgs
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant", "tool"}
    )






def test_invalid_tool_exhaustion_closes_tool_tail(agent_env):
    """Invalid-tool 3-strike partial must not leave a durable tool→user tail (#48879 class).

    Retries <3 append assistant+error tool rows, so the transcript already ends
    on ``tool`` before the exhaustion early-return. That return must close the
    sequence (same contract as interrupt aborts) so the next user turn is not
    ``tool → user`` for strict providers.
    """
    agent, handler = agent_env
    for _ in range(3):
        handler.response_queue.append(_tc_resp("frobnicate_xyz", "{}"))

    result = agent.run_conversation("degenerate", conversation_history=[], task_id="t")

    assert result.get("partial", False)
    msgs = result.get("messages") or []
    assert msgs, "expected persisted conversation messages"
    assert msgs[-1].get("role") == "assistant"
    assert "invalid tool call" in (msgs[-1].get("content") or "").lower()


def test_repeated_invalid_name_in_mixed_batches_reaches_three_strikes(agent_env):
    """A valid sibling must run without resetting the same invalid-name streak."""
    agent, handler = agent_env
    handler.response_queue.extend([
        _batch_tc_resp([("frobnicate_xyz", "{}"), ("todo", "{}")]),
        _batch_tc_resp([("frobnicate_xyz", "{}"), ("todo", "{}")]),
        _batch_tc_resp([("frobnicate_xyz", "{}"), ("todo", "{}")]),
    ])

    result = agent.run_conversation("degenerate mixed batches", conversation_history=[], task_id="t")

    assert result.get("partial", False)
    assert len([request for request in handler.captured_requests if "messages" in request]) == 3
    assert result["messages"][-1].get("role") == "assistant"
    assert "invalid tool call" in (result["messages"][-1].get("content") or "").lower()


def test_different_invalid_names_do_not_share_strikes(agent_env):
    """Only repetitions of the same hallucinated name consume its 3-strike limit."""
    agent, handler = agent_env
    handler.response_queue.extend([
        _tc_resp("frobnicate_one", "{}"),
        _tc_resp("frobnicate_two", "{}"),
        _tc_resp("frobnicate_three", "{}"),
        _text_resp("recovered"),
    ])

    result = agent.run_conversation("vary invalid names", conversation_history=[], task_id="t")

    assert result["completed"] is True
    assert result["final_response"] == "recovered"
    assert len([request for request in handler.captured_requests if "messages" in request]) == 4


def test_invalid_json_first_retry_includes_parse_feedback(agent_env):
    """The validation phase returns a tool error on its first malformed-JSON strike."""
    from agent.turn_tool_validation import validate_tool_calls

    agent = SimpleNamespace(
        valid_tool_names={"todo"}, _invalid_tool_retries=0, _invalid_json_retries=0,
        log_prefix="", _uniquify_tool_call_ids=lambda calls: None,
        _repair_tool_call=lambda name: None, _buffer_vprint=lambda *args: None,
        _vprint=lambda *args, **kwargs: None, _flush_status_buffer=lambda: None,
        _cleanup_task_resources=lambda task_id: None, _persist_session=lambda *args: None,
        _build_assistant_message=lambda message, reason: {
            "role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "todo", "arguments": "{broken"}}]
        },
    )
    assistant = SimpleNamespace(
        content="", tool_calls=[SimpleNamespace(
            id="call_1", function=SimpleNamespace(name="todo", arguments="{\"query\":,}")
        )]
    )
    messages = []

    verdict = validate_tool_calls(
        agent, assistant, "tool_calls", messages=messages, conversation_history=[],
        api_call_count=1, effective_task_id="task-1",
    )

    assert verdict.action == "continue"
    assert agent._json_error_results_this_round["call_1"].startswith("Error: Invalid JSON arguments.")
    assert agent._executable_tool_calls_this_round == []


def test_invalid_json_recovery_is_bounded_through_conversation_loop(agent_env):
    """Malformed arguments get actionable tool feedback, then stop after three rounds."""
    agent, handler = agent_env
    agent._disable_streaming = True
    agent.valid_tool_names = agent.valid_tool_names | {"todo"}
    handler.response_queue.extend([
        _tc_resp("todo", "{\"x\":,}"),
        _tc_resp("todo", "{\"x\":,}"),
        _tc_resp("todo", "{\"x\":,}"),
    ])

    result = agent.run_conversation("recover bad args", conversation_history=[], task_id="t")

    assert result.get("partial") is True
    requests = [req for req in handler.captured_requests if "messages" in req]
    assert len(requests) == 3
    assert len([m for m in result["messages"] if m.get("role") == "assistant" and m.get("tool_calls")]) == 3
    assert sum(m.get("role") == "tool" and "Invalid JSON arguments" in m.get("content", "")
               for m in result["messages"]) == 3
    assert "three times" in result["final_response"]


def test_mixed_json_recovery_executes_valid_sibling_and_pairs_each_id(agent_env):
    """Valid siblings run and each malformed/valid call gets one result under its ID."""
    agent, handler = agent_env
    agent._disable_streaming = True
    agent.valid_tool_names = agent.valid_tool_names | {"todo"}
    calls = [
        {"id": "terminal-id", "type": "function", "function": {"name": "terminal", "arguments": "{\"command\":\"pwd\"}"}},
        {"id": "todo-id", "type": "function", "function": {"name": "todo", "arguments": "{}"}},
        {"id": "invalid-id", "type": "function", "function": {"name": "frobnicate_xyz", "arguments": "{}"}},
    ]
    response = _batch_tc_resp([("todo", "{}")])
    response["choices"][0]["message"]["tool_calls"] = calls
    handler.response_queue.extend([response, _text_resp("recovered")])

    result = agent.run_conversation("mixed malformed batch", conversation_history=[], task_id="t")

    assert result["completed"] is True
    transcript = result["messages"]
    assistant = next(m for m in transcript if m.get("role") == "assistant" and m.get("tool_calls"))
    ids = [tc["id"] for tc in assistant["tool_calls"]]
    tools = [m for m in transcript if m.get("role") == "tool" and m.get("tool_call_id")]
    assert len(ids) == len(set(ids)) == 3
    assert {"terminal-id", "todo-id"}.issubset(set(ids))
    assert set(m["tool_call_id"] for m in tools) == set(ids)
    assert sum("Invalid JSON arguments" in m["content"] for m in tools) == 0
    assert {m.get("name") for m in tools if m.get("tool_call_id") in {"terminal-id", "todo-id"}} == {"terminal", "todo_list"}


def test_true_mixed_json_batch_stages_once_and_pairs_error_with_bad_call(agent_env):
    """A malformed JSON call beside valid names is never dispatched or duplicated."""
    agent, handler = agent_env
    agent._disable_streaming = True
    handler.response_queue.extend([
        _batch_tc_resp([("terminal", "{\"command\":,}"), ("todo", "{}")]),
        _text_resp("recovered"),
    ])
    result = agent.run_conversation("mixed JSON", conversation_history=[], task_id="t")
    transcript = result["messages"]
    batches = [m for m in transcript if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(batches) == 1
    ids = [tc["id"] for tc in batches[0]["tool_calls"]]
    tool_rows = [m for m in transcript if m.get("role") == "tool"]
    assert sorted(m.get("tool_call_id") for m in tool_rows) == sorted(ids)
    bad = next(m for m in tool_rows if m.get("tool_call_id") == "call_0")
    assert "Invalid JSON arguments" in bad["content"]
    assert "todo" in {m.get("name") for m in tool_rows if m.get("tool_call_id") == "call_1"}
    assert result["completed"] is True


def test_true_mixed_json_batches_hit_three_strike_limit_and_close_ledger(agent_env):
    """A valid sibling still executes on strike three, then no fourth request occurs."""
    agent, handler = agent_env
    agent._disable_streaming = True
    handler.response_queue.extend([
        _batch_tc_resp([("terminal", "{\"command\":,}"), ("read_file", "{\"path\":\".\"}")])
        for _ in range(3)
    ])
    result = agent.run_conversation("repeated mixed JSON", conversation_history=[], task_id="t")
    assert result.get("partial") is True
    assert len([req for req in handler.captured_requests if "messages" in req]) == 3
    transcript = result["messages"]
    batches = [m for m in transcript if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(batches) == 3
    for batch_index, batch in enumerate(batches):
        ids = [tc["id"] for tc in batch["tool_calls"]]
        following_index = transcript.index(batch) + 1
        next_batch = transcript.index(batches[batch_index + 1]) if batch_index + 1 < len(batches) else len(transcript)
        rows = []
        rows = [row for row in transcript[following_index:next_batch] if row.get("role") == "tool"]
        assert sorted(row.get("tool_call_id") for row in rows) == sorted(ids)
        assert "Invalid JSON arguments" in next(row["content"] for row in rows if row.get("tool_call_id") == "call_0")
        assert "read_file" in {row.get("name") for row in rows if row.get("tool_call_id") == "call_1"}
    assert transcript[-1].get("role") == "assistant"
    assert "three times" in result["final_response"]


def test_true_mixed_json_batch_normalizes_duplicate_and_missing_ids(agent_env):
    agent, _handler = agent_env
    from agent.message_sanitization import uniquify_tool_call_ids
    agent.valid_tool_names = {"terminal", "todo"}
    agent._uniquify_tool_call_ids = uniquify_tool_call_ids
    calls = [
        SimpleNamespace(id="dupe", function=SimpleNamespace(name="terminal", arguments="{\"x\":,}")),
        SimpleNamespace(id="dupe", function=SimpleNamespace(name="todo", arguments="{}")),
        SimpleNamespace(id=None, function=SimpleNamespace(name="todo", arguments="{}")),
    ]
    agent._invalid_json_retries = 0
    verdict = __import__("agent.turn_tool_validation", fromlist=["validate_tool_calls"]).validate_tool_calls(
        agent, SimpleNamespace(tool_calls=calls, content=""), "tool_calls", messages=[],
        conversation_history=[], api_call_count=1, effective_task_id="mixed-ids",
    )
    ids = [call.id for call in calls]
    assert len(ids) == len(set(ids)) == 3
    assert verdict.action == "ok"


def test_invalid_json_retry_counter_resets_for_new_turn(agent_env):
    agent, handler = agent_env
    agent._disable_streaming = True
    handler.response_queue.extend([
        _tc_resp("todo", "{\"x\":,}"), _text_resp("turn one recovered"),
        _tc_resp("todo", "{\"x\":,}"), _text_resp("turn two recovered"),
    ])
    first = agent.run_conversation("first", conversation_history=[], task_id="t")
    second = agent.run_conversation("second", conversation_history=first["messages"], task_id="t")
    assert first["completed"] and second["completed"]
    assert second["final_response"] == "turn two recovered"


def test_duplicate_and_missing_tool_call_ids_are_normalized(agent_env):
    from agent.turn_tool_validation import validate_tool_calls
    agent, handler = agent_env
    from types import SimpleNamespace
    agent.valid_tool_names = {"terminal"}
    from agent.message_sanitization import uniquify_tool_call_ids
    agent._uniquify_tool_call_ids = uniquify_tool_call_ids
    calls = [
        SimpleNamespace(id="same-id", function=SimpleNamespace(name="terminal", arguments="{}")),
        SimpleNamespace(id="same-id", function=SimpleNamespace(name="terminal", arguments="{}")),
        SimpleNamespace(id=None, function=SimpleNamespace(name="terminal", arguments="{}")),
    ]
    assistant = SimpleNamespace(tool_calls=calls, content="")
    verdict = validate_tool_calls(
        agent, assistant, "tool_calls", messages=[], conversation_history=[],
        api_call_count=1, effective_task_id="task-ids",
    )
    ids = [tc.id for tc in calls]
    assert len(ids) == len(set(ids)) == 3
    assert verdict.action == "ok"


def test_different_invalid_names_are_isolated_in_turn_loop(agent_env):
    agent, handler = agent_env
    agent._disable_streaming = True
    handler.response_queue.extend([
        _tc_resp("unknown_alpha", "{}"),
        _tc_resp("unknown_beta", "{}"),
        _text_resp("recovered"),
    ])
    result = agent.run_conversation("different invalid names", conversation_history=[], task_id="t")
    assert result["completed"] is True
    assert result["final_response"] == "recovered"
    assert len([req for req in handler.captured_requests if "messages" in req]) == 3


def test_repeated_invalid_name_in_one_batch_counts_one_strike(agent_env):
    agent, handler = agent_env
    agent._disable_streaming = True
    response = _batch_tc_resp([("unknown_repeat", "{}"), ("unknown_repeat", "{}")])
    response["choices"][0]["message"]["tool_calls"][1]["id"] = "other-id"
    handler.response_queue.extend([response, _tc_resp("unknown_repeat", "{}"), _text_resp("recovered")])
    result = agent.run_conversation("repeated invalid name batch", conversation_history=[], task_id="t")
    assert result["completed"] is True
    assert result["final_response"] == "recovered"
    assert len([req for req in handler.captured_requests if "messages" in req]) == 3

