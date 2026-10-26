"""Explicit selected-model inference RPCs; isolated from readiness polling and chat sessions."""

from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from tui_gateway.transport import current_transport

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped

_MAX_ACTIVE_PROBES = 8
_probe_lock = threading.RLock()
_probes: dict[tuple[int, int, str, str], "_Probe"] = {}
_pending_owner_probes: dict[tuple[int, str, str, str], "_Probe"] = {}


def _pending_owner_key(transport, session_id: str, profile: str, probe_id: str) -> tuple[int, str, str, str]:
    return (id(transport), session_id, profile, probe_id)


def _terminal_result(status: str, provider: str, model: str, message: str, *,
                     inference_ok: bool | None, error_kind: str | None = None,
                     cancel_supported: bool = False, started_at: float) -> dict:
    return {
        "status": status,
        "inference_ok": inference_ok,
        "provider": provider[:128],
        "model": model[:256],
        "source": "resolved_runtime",
        "latency_ms": round((time.monotonic() - started_at) * 1000, 2),
        "error_kind": error_kind,
        "error": message,
        "cancel_supported": cancel_supported,
    }


@dataclass
class _Probe:
    transport: Any
    session: dict | None
    profile: str
    session_id: str
    probe_id: str
    provider: str
    model: str
    timeout_s: float
    started_at: float = field(default_factory=time.monotonic)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    worker_done_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    agent: Any = None
    state: str = "resolving"
    result: dict | None = None
    worker_done: bool = False

    @property
    def deadline(self) -> float:
        return self.started_at + self.timeout_s

    @property
    def key(self) -> tuple[int, int, str, str]:
        return (id(self.transport), id(self.session), self.profile, self.probe_id)

    def should_stop(self) -> bool:
        with self.lock:
            return self.state in {"cancel_deciding", "timeout_deciding", "canceled", "timed_out",
                                  "cancel_not_supported"}

    def _timeout_locked(self) -> None:
        """Settle a deadline while ``lock`` is held; timed out is never inference success."""
        self.cancel_event.set()
        self.state = "timed_out"
        self.result = _terminal_result(
            "timed_out", self.provider, self.model, "The model request timed out.",
            inference_ok=False, error_kind="timeout", cancel_supported=False,
            started_at=self.started_at,
        )
        self.done_event.set()

    def is_active(self) -> bool:
        with self.lock:
            if self.state not in {"resolving", "constructing", "dispatching"}:
                return False
            if time.monotonic() >= self.deadline:
                self._timeout_locked()
                return False
            return True

    def attach_agent(self, agent) -> None:
        with self.lock:
            if self.state not in {"resolving", "constructing"}:
                return
            if time.monotonic() >= self.deadline:
                self._timeout_locked()
                return
            self.agent = agent
            self.state = "constructing"

    def begin_dispatch(self) -> bool:
        """Linearize the final permission to send just before the SDK request."""
        with self.lock:
            if self.state not in {"resolving", "constructing"}:
                return False
            if time.monotonic() >= self.deadline:
                self._timeout_locked()
                return False
            self.state = "dispatching"
            return True

    def response_received(self) -> bool:
        """Reserve a completed response before cancellation can claim the probe."""
        with self.lock:
            if self.state != "dispatching":
                return False
            if time.monotonic() >= self.deadline:
                self._timeout_locked()
                return False
            self.state = "response_received"
            return True

    def complete(self, result: dict) -> bool:
        with self.lock:
            if self.state not in {"resolving", "constructing", "dispatching", "response_received"}:
                return False
            if self.state != "response_received" and time.monotonic() >= self.deadline:
                self._timeout_locked()
                return False
            self.state = str(result.get("status") or "failed")
            self.result = result
            self.done_event.set()
            return True

    def cancel(self) -> str:
        """Atomically arbitrate cancellation against the original deadline and response completion."""
        expired_agent = None
        expired = False
        with self.lock:
            if self.state in {"response_received", "success", "failed", "unsupported", "timed_out",
                              "canceled", "cancel_not_supported"}:
                return "not_running"
            if time.monotonic() >= self.deadline:
                expired_agent = self.agent if self.state in {"dispatching", "cancel_deciding"} else None
                self._timeout_locked()
                expired = True
            elif self.state in {"resolving", "constructing"}:
                self.cancel_event.set()
                self.state = "canceled"
                self.result = _terminal_result(
                    "canceled", self.provider, self.model, "The model test was stopped before inference dispatch.",
                    inference_ok=None, cancel_supported=True, started_at=self.started_at,
                )
                self.done_event.set()
                return "cancel_requested"
            elif self.state == "dispatching":
                self.state = "cancel_deciding"
                self.cancel_event.set()
                agent = self.agent
            else:
                return "not_running"

        if expired:
            self._abort_timed_out_agent(expired_agent)
            return "not_running"

        confirmed = False
        if agent is not None:
            try:
                confirmed = bool(agent.hard_interrupt(tool_reason="model test canceled"))
            except Exception:
                confirmed = False
        expired_after_abort = False
        with self.lock:
            if self.state != "cancel_deciding":
                return "not_running"
            if time.monotonic() >= self.deadline:
                self._timeout_locked()
                expired_after_abort = True
            elif confirmed:
                self.state = "canceled"
                self.result = _terminal_result(
                    "canceled", self.provider, self.model,
                    "The model test connection was interrupted; the provider may still finish processing it.",
                    inference_ok=None, cancel_supported=True, started_at=self.started_at,
                )
                status = "cancel_requested"
            else:
                self.state = "cancel_not_supported"
                self.result = _terminal_result(
                    "cancel_not_supported", self.provider, self.model,
                    "The provider request may continue until its timeout.",
                    inference_ok=None, cancel_supported=False, started_at=self.started_at,
                )
                status = "cancel_not_supported"
            if not expired_after_abort:
                self.done_event.set()
                return status

        if not confirmed:
            self._abort_timed_out_agent(agent)
        return "not_running"

    def _abort_timed_out_agent(self, agent) -> None:
        if agent is None:
            return

        def abort_provider_request() -> None:
            try:
                agent.hard_interrupt(tool_reason="model test deadline reached")
            except Exception:
                pass

        threading.Thread(
            target=abort_provider_request,
            name="model-inference-timeout-abort",
            daemon=True,
        ).start()

    def timeout(self) -> None:
        """Settle the absolute probe deadline; a late resolver can no longer dispatch."""
        with self.lock:
            if self.state in {"response_received", "success", "failed", "unsupported", "timed_out",
                              "canceled", "cancel_not_supported"}:
                return
            attempted = self.state in {"dispatching", "cancel_deciding"}
            agent = self.agent if attempted else None
            self._timeout_locked()
        # Publish the terminal timeout before touching provider state. A socket
        # abort may race an explicit cancellation or block briefly; neither is
        # allowed to extend the probe's RPC deadline.
        self._abort_timed_out_agent(agent)

    def mark_worker_done(self) -> None:
        with self.lock:
            self.worker_done = True
            self.worker_done_event.set()
            if self.result is None and self.state not in {"timeout_deciding", "cancel_deciding"}:
                self.state = "failed"
                self.result = _terminal_result(
                    "failed", self.provider, self.model,
                    "The selected model route could not be resolved.",
                    inference_ok=False, error_kind="unknown", started_at=self.started_at,
                )
                self.done_event.set()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.result or _terminal_result(
                "failed", self.provider, self.model,
                "The selected model route could not be resolved.",
                inference_ok=False, error_kind="unknown", started_at=self.started_at,
            ))


def _prune_probes() -> None:
    """Remove only workers that have actually returned; never reap a live probe by age."""
    with _probe_lock:
        for registry in (_probes, _pending_owner_probes):
            for key, probe in list(registry.items()):
                with probe.lock:
                    worker_done = probe.worker_done
                if worker_done:
                    registry.pop(key, None)


def _owner(rid, params):
    session_id = str(params.get("session_id") or "").strip()
    transport, session = _current_session_steer_authority(session_id)
    if transport is None or session is None:
        return None, None, None, _err(rid, 4001, "A live session on this connection is required.")
    requested_profile = str(params.get("profile") or "").strip()
    expected_home = _profile_home(requested_profile)
    session_home = session.get("profile_home")
    if (str(session_home or "") != str(expected_home or "")
            or _session_is_retired(session)):
        return None, None, None, _err(rid, 4001, "The selected profile does not own this session.")
    profile = requested_profile or "default"
    return transport, session, (profile, session_id), None


def _session_is_retired(session: dict) -> bool:
    with _sessions_lock:
        return session.get("retired", False) or session.get("running_transport_detached", False)


def _safe_failure(provider: str, model: str) -> dict:
    return {
        "status": "failed", "inference_ok": False, "provider": provider[:128], "model": model[:256],
        "source": "resolved_runtime", "error_kind": "unknown",
        "error": "The selected model route could not be resolved.", "cancel_supported": False,
    }


@method("setup.inference_test")
@_profile_scoped
def _inference_test(rid, params: dict) -> dict:
    received_at = time.monotonic()
    session_id = str(params.get("session_id") or "").strip()
    requested_profile = str(params.get("profile") or "").strip()
    profile = requested_profile or "default"
    selected_provider = str(params.get("provider") or "").strip()
    selected_model = str(params.get("model") or "").strip()
    probe_id = str(params.get("probe_id") or "").strip()
    timeout_s = min(max(float(params.get("timeout_s", 20)), 0.1), 30.0)
    transport = current_transport()
    if transport is None or not session_id:
        return _err(rid, 4001, "A live session on this connection is required.")

    probe = _Probe(
        transport, None, profile, session_id, probe_id,
        selected_provider, selected_model, timeout_s, started_at=received_at,
    )
    pending_key = _pending_owner_key(transport, session_id, profile, probe_id)
    with _probe_lock:
        _prune_probes()
        if len(_probes) + len(_pending_owner_probes) >= _MAX_ACTIVE_PROBES:
            return _err(rid, 4028, "Model test capacity is busy; try again after active probes finish.")
        if any(
            active.transport is transport and active.session_id == session_id and active.profile == profile
            for active in [*_probes.values(), *_pending_owner_probes.values()]
        ):
            return _err(rid, 4028, "A model test is already running for this session.")
        _pending_owner_probes[pending_key] = probe

    owner_failure: dict[str, dict] = {}

    def remove_probe() -> None:
        with _probe_lock:
            for registry in (_pending_owner_probes, _probes):
                for key, active in list(registry.items()):
                    if active is probe:
                        registry.pop(key, None)

    def worker() -> None:
        try:
            if not probe.is_active():
                return
            resolved_transport, session, owner, failure = _owner(rid, params)
            if not probe.is_active():
                return
            if failure:
                owner_failure["response"] = failure
                probe.complete(_safe_failure(selected_provider, selected_model))
                return
            if resolved_transport is not transport or session is None or owner != (profile, session_id):
                owner_failure["response"] = _err(
                    rid, 4001, "The selected profile does not own this session."
                )
                probe.complete(_safe_failure(selected_provider, selected_model))
                return

            # Transfer the already-reserved bounded slot only after owner validation.
            # A cancel remains addressable by pending_key until this transition wins.
            with _probe_lock:
                with probe.lock:
                    if probe.state not in {"resolving", "constructing"}:
                        _pending_owner_probes.pop(pending_key, None)
                        return
                    if time.monotonic() >= probe.deadline:
                        probe._timeout_locked()
                        _pending_owner_probes.pop(pending_key, None)
                        return
                    probe.transport = resolved_transport
                    probe.session = session
                    _pending_owner_probes.pop(pending_key, None)
                    _probes[probe.key] = probe

            if not probe.is_active():
                return
            from hermes_cli.runtime_provider import resolve_runtime_provider
            from hermes_cli.model_inference import run_model_inference_test

            runtime = resolve_runtime_provider(requested=selected_provider, target_model=selected_model)
            if not probe.is_active():
                return
            result = run_model_inference_test(
                runtime,
                selected_provider=selected_provider,
                selected_model=selected_model,
                timeout_s=timeout_s,
                deadline=probe.deadline,
                cancel_check=probe.should_stop,
                on_agent=probe.attach_agent,
                request_guard=probe.begin_dispatch,
                on_response_received=probe.response_received,
            )
            probe.complete(result)
        except Exception:
            probe.complete(_safe_failure(selected_provider, selected_model))
        finally:
            probe.mark_worker_done()
            remove_probe()

    timer = threading.Timer(max(0.0, probe.deadline - time.monotonic()), probe.timeout)
    timer.name = "model-inference-deadline"
    timer.daemon = True
    worker_context = contextvars.copy_context()
    thread = threading.Thread(target=lambda: worker_context.run(worker), name="model-inference-probe", daemon=True)
    try:
        timer.start()
        thread.start()
    except Exception:
        timer.cancel()
        probe.complete(_safe_failure(selected_provider, selected_model))
        probe.mark_worker_done()
        remove_probe()
    remaining = max(0.0, probe.deadline - time.monotonic())
    if not probe.done_event.wait(remaining):
        probe.timeout()
    timer.cancel()
    snapshot = probe.snapshot()
    if snapshot.get("status") == "failed" and "response" in owner_failure:
        return owner_failure["response"]
    return _ok(rid, snapshot)


@method("setup.inference_test_cancel")
@_profile_scoped
def _cancel_inference_test(rid, params: dict) -> dict:
    transport = current_transport()
    session_id = str(params.get("session_id") or "").strip()
    profile = str(params.get("profile") or "").strip() or "default"
    probe_id = str(params.get("probe_id") or "").strip()
    if transport is None or not session_id or not probe_id:
        return _ok(rid, {"status": "forbidden", "message": "This connection does not own that model test.",
                         "cancel_supported": False})
    with _probe_lock:
        _prune_probes()
        candidates = [
            *[probe for probe in _pending_owner_probes.values()
              if probe.session_id == session_id and probe.profile == profile and probe.probe_id == probe_id],
            *[probe for probe in _probes.values()
              if probe.session_id == session_id and probe.profile == profile and probe.probe_id == probe_id],
        ]
        probe = next((candidate for candidate in candidates if candidate.transport is transport), None)
    if probe is None:
        if candidates:
            return _ok(rid, {"status": "forbidden", "message": "This connection does not own that model test.",
                             "cancel_supported": False})
        return _ok(rid, {"status": "not_running", "message": "No matching model test is running.",
                         "cancel_supported": False})
    cancel_status = probe.cancel()
    if cancel_status == "not_running":
        return _ok(rid, {"status": "not_running", "message": "The model test has already completed.",
                         "cancel_supported": False})
    if cancel_status == "cancel_not_supported":
        return _ok(rid, {"status": "cancel_not_supported",
                         "message": "The provider request may continue until its timeout.",
                         "cancel_supported": False})
    return _ok(rid, {"status": "cancel_requested",
                     "message": "The model test was canceled before dispatch or its active socket was shut down.",
                     "cancel_supported": True})


def register(server):
    bind_module(globals(), server)
    server._LONG_HANDLERS = server._LONG_HANDLERS | {"setup.inference_test"}
