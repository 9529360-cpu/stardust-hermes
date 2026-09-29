"""Fold the desktop backend's event stream into a gradeable trace.

Only sessions registered with :meth:`Trace.watch` are recorded (a restart resumes the same
conversation under a new live session id, so a trace may watch several). Deferred tools are reached
through the ``tool_call`` bridge; the trace records the tool that was actually invoked, never the
bridge, so a scorer asking "was a cron job created?" sees ``cronjob_manage`` either way.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

BRIDGE_CALL = "tool_call"


@dataclass
class ToolCall:
    tool_id: str
    name: str
    args: Dict[str, Any]
    via_bridge: bool
    turn: int
    started_at: float
    completed: bool = False
    result_text: Optional[str] = None


@dataclass
class Turn:
    index: int
    origin: str  # "user" (the exam submitted a prompt) or "notification" (the backend started it)
    started_at: float
    completed_at: Optional[float] = None
    text: str = ""
    status: Optional[str] = None
    error: Optional[str] = None

    @property
    def duration(self) -> Optional[float]:
        return None if self.completed_at is None else round(self.completed_at - self.started_at, 3)


@dataclass
class Approval:
    command: str
    description: str
    tool_name: Optional[str]
    smart_denied: Optional[bool]
    choice: str
    turn: int
    at: float


@dataclass
class Clarify:
    question: str
    choices: List[str]
    answer: str
    turn: int
    at: float


def _parse_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    return {}


def _bridged_calls(args: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    batch = args.get("calls")
    calls: List[Any] = batch if isinstance(batch, list) else [args]
    return [(str(c.get("name") or ""), _parse_args(c.get("arguments")))
            for c in calls if isinstance(c, dict) and c.get("name")]


@dataclass
class Trace:
    tool_calls: List[ToolCall] = field(default_factory=list)
    turns: List[Turn] = field(default_factory=list)
    approvals: List[Approval] = field(default_factory=list)
    clarifies: List[Clarify] = field(default_factory=list)
    subagent_tools: List[Tuple[str, str]] = field(default_factory=list)
    subagent_events: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    last_event_at: Optional[float] = None
    _sessions: set = field(default_factory=set)
    _pending_submit: Optional[float] = None
    _open: Optional[Turn] = None

    @property
    def busy(self) -> bool:
        """A turn is in flight: submitted and not yet completed, or announced by the backend."""
        return self._open is not None or self._pending_submit is not None

    def watch(self, session_id: str) -> None:
        self._sessions.add(session_id)

    def mark_submit(self, session_id: str, text: str, at: float) -> None:
        self.watch(session_id)
        self._pending_submit = at

    # ── event folding ────────────────────────────────────────────────────
    def ingest(self, params: Dict[str, Any], at: float) -> None:
        if params.get("session_id") not in self._sessions:
            return
        self.last_event_at = at
        kind = str(params.get("type") or "")
        payload = params.get("payload") or {}
        handler = _HANDLERS.get(kind)
        if handler is not None:
            handler(self, payload, at)
        elif kind == "error":
            self.errors.append(str(payload.get("message") or ""))

    def _current_turn_index(self) -> int:
        return self._open.index if self._open is not None else len(self.turns)

    def _open_turn(self, at: float) -> Turn:
        if self._open is None:
            submitted = self._pending_submit
            self._pending_submit = None
            turn = Turn(index=len(self.turns), origin="user" if submitted is not None else "notification",
                        started_at=submitted if submitted is not None else at)
            self.turns.append(turn)
            self._open = turn
        return self._open

    def _on_start(self, payload: Dict[str, Any], at: float) -> None:
        self._open_turn(at)

    def _on_complete(self, payload: Dict[str, Any], at: float) -> None:
        turn = self._open_turn(at)
        turn.completed_at = at
        turn.text = str(payload.get("text") or "")
        turn.status = payload.get("status")
        turn.error = payload.get("error")
        self._open = None

    def _on_tool_start(self, payload: Dict[str, Any], at: float) -> None:
        name = str(payload.get("name") or "")
        args = _parse_args(payload.get("args"))
        tool_id = str(payload.get("tool_id") or "")
        turn = self._current_turn_index()
        if name == BRIDGE_CALL:
            for inner, inner_args in _bridged_calls(args):
                self.tool_calls.append(ToolCall(tool_id, inner, inner_args, True, turn, at))
            return
        self.tool_calls.append(ToolCall(tool_id, name, args, False, turn, at))

    def _on_tool_complete(self, payload: Dict[str, Any], at: float) -> None:
        tool_id = str(payload.get("tool_id") or "")
        text = payload.get("result_text")
        if text is None and payload.get("result") is not None:
            text = json.dumps(payload.get("result"), ensure_ascii=False, default=str)
        for call in self.tool_calls:
            if call.tool_id == tool_id and not call.completed:
                call.completed = True
                call.result_text = text

    def _on_subagent(self, payload: Dict[str, Any], at: float) -> None:
        self.subagent_events.append({"at": at, **payload})

    def _on_subagent_tool(self, payload: Dict[str, Any], at: float) -> None:
        self._on_subagent(payload, at)
        self.subagent_tools.append((str(payload.get("tool_name") or ""),
                                    str(payload.get("tool_preview") or payload.get("text") or "")))

    # ── server→client requests the exam answered ─────────────────────────
    def record_approval(self, params: Dict[str, Any], choice: str, at: float) -> None:
        self.approvals.append(Approval(
            command=str(params.get("command") or ""), description=str(params.get("description") or ""),
            tool_name=params.get("tool_name"), smart_denied=params.get("smart_denied"),
            choice=choice, turn=self._current_turn_index(), at=at))

    def record_clarify(self, params: Dict[str, Any], answer: str, at: float) -> None:
        questions = [str(params.get("question") or "")] if params.get("question") else []
        questions += [str(q.get("question") or "") for q in params.get("questions") or [] if isinstance(q, dict)]
        self.clarifies.append(Clarify(
            question="\n".join(q for q in questions if q), choices=[str(c) for c in params.get("choices") or []],
            answer=answer, turn=self._current_turn_index(), at=at))

    # ── queries ──────────────────────────────────────────────────────────
    def calls(self, *names: str) -> List[ToolCall]:
        return [c for c in self.tool_calls if c.name in names]

    def to_json(self) -> Dict[str, Any]:
        return {
            "tool_calls": [asdict(c) for c in self.tool_calls],
            "turns": [{**asdict(t), "duration": t.duration} for t in self.turns],
            "approvals": [asdict(a) for a in self.approvals],
            "clarifies": [asdict(c) for c in self.clarifies],
            "subagent_tools": self.subagent_tools,
            "errors": self.errors,
        }


_HANDLERS = {
    "message.start": Trace._on_start,
    "message.complete": Trace._on_complete,
    "tool.start": Trace._on_tool_start,
    "tool.complete": Trace._on_tool_complete,
    "subagent.start": Trace._on_subagent,
    "subagent.complete": Trace._on_subagent,
    "subagent.tool": Trace._on_subagent_tool,
}
