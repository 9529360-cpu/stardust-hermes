"""A scripted OpenAI-compatible endpoint for rehearsing the exam without a paid model.

A script is a function ``Request -> Reply``. It is used to prove the exam's plumbing end to end (the
real backend runs, real tools execute, real approvals are raised) before any money is spent, and by
the harness tests to feed both good and bad behavior through the real gateway.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Sequence, Tuple


@dataclass
class Reply:
    text: str = ""
    tool_calls: Sequence[Tuple[str, Dict[str, Any]]] = ()
    delay: float = 0.0


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return str(content or "")


@dataclass
class Request:
    body: Dict[str, Any]
    messages: List[Dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        self.messages = list(self.body.get("messages") or [])

    def _since_last_user(self) -> List[Dict[str, Any]]:
        last = max((i for i, m in enumerate(self.messages) if m.get("role") == "user"), default=-1)
        return self.messages[last + 1:]

    def text(self) -> str:
        return "\n".join(_content_text(m.get("content")) for m in self.messages)

    def system(self) -> str:
        return "\n".join(_content_text(m.get("content")) for m in self.messages if m.get("role") == "system")

    def last_user(self) -> str:
        users = [m for m in self.messages if m.get("role") == "user"]
        return _content_text(users[-1].get("content")) if users else ""

    def tool_results(self) -> List[str]:
        return [_content_text(m.get("content")) for m in self._since_last_user() if m.get("role") == "tool"]

    def called(self, name: str) -> bool:
        """Whether an assistant message after the last user message already called ``name``."""
        return any((tc.get("function") or {}).get("name") == name
                   for m in self._since_last_user() for tc in m.get("tool_calls") or [])

    def offered(self, name: str) -> bool:
        return any((t.get("function") or {}).get("name") == name for t in self.body.get("tools") or [])


Script = Callable[[Request], Reply]


def _chunks(model: str, reply: Reply) -> List[Dict[str, Any]]:
    base = {"id": f"chatcmpl-{uuid.uuid4().hex[:10]}", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model}
    deltas: List[Dict[str, Any]] = [{"role": "assistant", "content": reply.text or ""}]
    deltas += [{"tool_calls": [{"index": i, "id": f"call_{uuid.uuid4().hex[:10]}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
               for i, (name, args) in enumerate(reply.tool_calls)]
    chunks: List[Dict[str, Any]] = [{**base, "choices": [{"index": 0, "delta": d, "finish_reason": None}]}
                                    for d in deltas]
    finish = "tool_calls" if reply.tool_calls else "stop"
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    return chunks


def _completion(model: str, reply: Reply) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": reply.text or None}
    if reply.tool_calls:
        message["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                                 for n, a in reply.tool_calls]
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:10]}", "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": message,
                                         "finish_reason": "tool_calls" if reply.tool_calls else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class FakeModel:
    def __init__(self, script: Script, model: str = "exam-rehearsal"):
        self.script, self.model = script, model
        self.requests: List[Dict[str, Any]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self) -> "FakeModel":
        threading.Thread(target=self._server.serve_forever, name="fake-model", daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # keep the exam's output clean
                pass

            def _send(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(json.dumps({"object": "list", "data": [{"id": outer.model, "object": "model"}]}).encode(),
                           "application/json")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.requests.append(body)
                reply = outer.script(Request(body))
                if reply.delay:
                    time.sleep(reply.delay)
                if body.get("stream"):
                    frames = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in _chunks(outer.model, reply))
                    self._send((frames + "data: [DONE]\n\n").encode("utf-8"), "text/event-stream")
                else:
                    self._send(json.dumps(_completion(outer.model, reply), ensure_ascii=False).encode("utf-8"),
                               "application/json")

        return Handler
