"""Wire adapters that give every auxiliary client the ``chat.completions`` shape: Codex
Responses (with its no-progress stream guard), Anthropic Messages and Bedrock, sync and
async, plus the Anthropic-wire detection that wraps an endpoint.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


# Codex Responses → chat.completions adapter, so aux consumers need no changes.
def _parse_codex_final_response(final: Any) -> Tuple[List[str], List[Any], Any]:
    """Split a completed Responses object into (text_parts, tool_calls, usage) in chat.completions shape."""
    text_parts: List[str] = []
    tool_calls_raw: List[Any] = []
    for item in (getattr(final, "output", None) or []):
        item_type = _aux._field(item, "type")
        if item_type == "message":
            for part in (_aux._field(item, "content") or []):
                part_type = _aux._field(part, "type")
                if part_type in {"output_text", "text"}:
                    text_parts.append(_aux._field(part, "text", ""))
                elif part_type == "refusal":
                    # A refusal part carries the model's explanation; dropping it turns a
                    # refusal-only turn into an empty response that gets retried.
                    text_parts.append(_aux._field(part, "refusal", ""))
        elif item_type == "function_call":
            tool_calls_raw.append(SimpleNamespace(
                id=_aux._field(item, "call_id", ""), type="function",
                function=SimpleNamespace(
                    name=_aux._field(item, "name", ""), arguments=_aux._field(item, "arguments", "{}"))))
    usage = None
    resp_usage = getattr(final, "usage", None)
    if resp_usage:
        def _u(key: str) -> int:
            return getattr(resp_usage, key, 0) or (resp_usage.get(key, 0) if isinstance(resp_usage, dict) else 0)
        usage = SimpleNamespace(
            prompt_tokens=_u("input_tokens"), completion_tokens=_u("output_tokens"),
            total_tokens=_u("total_tokens"))
    return text_parts, tool_calls_raw, usage


def _close_quietly(target: Any, failure_note: Optional[str]) -> None:
    """Call ``target.close()`` if present; a failure is debug-logged under ``failure_note`` (silent when None)."""
    close = getattr(target, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            if failure_note:
                logger.debug("Codex auxiliary: %s", failure_note, exc_info=True)


class _CodexStreamGuard:
    """Progress-aware deadline + FD-safe timeout watchdog for one Codex aux stream attempt.

    (1) The first substantive payload must arrive within ``no_progress_timeout`` or we fail fast into
    the caller's retry/fallback chain (a dead or keepalive-only zombie must not hold the budget);
    (2) each substantive event re-arms that window (keepalive/lifecycle frames do NOT, mirroring
    commit-fence gating) so a live stream is never killed by an absolute total; (3) a hard ceiling
    from ``_aux_stream_total_ceiling`` still terminates a pathological drip.
    """

    def __init__(self, client: Any, total_timeout: Optional[float]):
        self._client = client
        self.total_timeout = total_timeout
        self._start = time.monotonic()
        self.no_progress_timeout = _aux._AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS
        # Progress-aware stream deadlines (supersedes the old single absolute kill at ``total_timeout``).
        # Three regimes: 1. First token: the stream must produce its first substantive payload within
        # ``no_progress_timeout`` (60s default) or we fail fast and let the caller's normal retry/fallback
        # chain run — a dead (or keepalive-only zombie) Codex stream no longer holds the full 300s
        # compression budget before falling back (masoria report, Aug 2026: 3 stacked 300s waits -> 20+ min
        # stuck on "Summarizing"). 2. Streaming: every substantive event re-arms the deadline by
        # ``no_progress_timeout`` — a live stream is never killed by an absolute total, so a long reasoning
        # summary that is actually producing tokens completes instead of timing out at 300s and falling back
        # (#54915's original complaint, fixed properly). Keepalive/lifecycle frames do NOT re-arm, mirroring
        # the commit-fence progress gating (#96707). 3. Hard ceiling: an absolute backstop from
        # ``_aux_stream_total_ceiling`` (max(600s, 4x configured timeout) — the same bound the streamed
        # chat.completions path uses) so a pathological one-token-per-59s drip still terminates.
        if total_timeout is not None:
            self.no_progress_timeout = min(self.no_progress_timeout, float(total_timeout))
        self.hard_deadline = self._start + _aux._aux_stream_total_ceiling(total_timeout)
        # The waiting host's absolute deadline clamps the ceiling so the watchdog Timer severs
        # the socket the instant the host stops waiting — a stream blocked between events
        # can't be stopped by a per-event check.
        host_deadline = _aux._current_aux_stream_deadline()
        if isinstance(host_deadline, (int, float)) and host_deadline < self.hard_deadline:
            self.hard_deadline = float(host_deadline)
        self._deadline_lock = threading.Lock()
        self._progress_deadline = self._start + self.no_progress_timeout
        self.saw_content = threading.Event()
        self.timed_out = threading.Event()
        # Set only when the timeout WON (not when the owner hard-cancelled first): tells the
        # owner's ``finally`` the shared client's FDs still need a real close.
        self.timeout_release_pending = threading.Event()
        self.stream_finished = threading.Event()
        self._timer = None
        # The owner may return on hard cancel while this attempt is still blocked in the SDK
        # stream. Timer threads don't inherit the worker's thread-local protection state, so
        # freeze the hard-cancel source before creating the timer.
        self._protected_cancel_check = _aux._capture_aux_cancel_check() if _aux._aux_interrupt_protected() else None
        self._attempt_stream_lock = threading.Lock()
        self._attempt_stream: Any = None
        # The request-driving thread owns the transport FDs — see _close_client_on_timeout.
        self._owner_tid = threading.get_ident()
        # Timeout detection can happen on the owner thread itself, then finish() runs on the
        # same unwind path. The shared client must release its transport FDs exactly once.
        self._client_close_lock = threading.Lock()
        self._client_closed = False

    def effective_deadline(self) -> float:
        with self._deadline_lock:
            return min(self.hard_deadline, self._progress_deadline)

    def cancel_requested(self) -> bool:
        """True when the frozen hard-cancel source says the owner already cancelled."""
        check = self._protected_cancel_check
        return callable(check) and _aux._captured_aux_cancel_requested(check)

    def adopt_stream(self, stream: Any) -> None:
        with self._attempt_stream_lock:
            self._attempt_stream = stream

    def release_stream(self, stream: Any) -> None:
        """Owner-side: close the attempt stream silently and forget it."""
        _close_quietly(stream, None)
        with self._attempt_stream_lock:
            self._attempt_stream = None

    def close_attempt_stream(self, failure_note: str) -> None:
        """Closes only this attempt's stream — never the process-shared client."""
        with self._attempt_stream_lock:
            stream = self._attempt_stream
        _close_quietly(stream, failure_note)

    def record_progress(self) -> None:
        """Substantive payload re-arms the no-progress window; the hard ceiling never moves."""
        with self._deadline_lock:
            self._progress_deadline = time.monotonic() + self.no_progress_timeout

    def timeout_message(self) -> str:
        elapsed = time.monotonic() - self._start
        if time.monotonic() >= self.hard_deadline:
            return f"Codex auxiliary Responses stream exceeded {self.hard_deadline - self._start:.1f}s hard ceiling"
        if not self.saw_content.is_set():
            return (
                "Codex auxiliary Responses stream produced no output "
                f"within {float(self.no_progress_timeout):.1f}s (no-progress timeout, {elapsed:.1f}s elapsed)")
        return (
            "Codex auxiliary Responses stream stalled: no new output "
            f"for {float(self.no_progress_timeout):.1f}s ({elapsed:.1f}s elapsed)")

    def _close_client_once(self, failure_note: str) -> None:
        """Owner-thread shared-client close, idempotent across timeout + finally paths."""
        if threading.get_ident() != self._owner_tid:
            raise RuntimeError("shared Codex auxiliary client close attempted from non-owner thread")
        with self._client_close_lock:
            if self._client_closed:
                return
            self._client_closed = True
        _close_quietly(self._client, failure_note)

    def _close_client_on_timeout(self) -> None:
        begin_timeout_cleanup = getattr(self._protected_cancel_check, "begin_timeout_cleanup", None)
        if callable(begin_timeout_cleanup):
            timeout_won = bool(begin_timeout_cleanup())
        else:
            timeout_won = not self.cancel_requested()
        # Publish transport timeout only after the attempt-local decision is fixed, so owner
        # polling cannot observe completion in between.
        self.timed_out.set()
        if not timeout_won:
            # Owner already hard-cancelled. The OpenAI client is process-shared, so never
            # close/evict it here; wake only this attempt's stream if responses.create()
            # returned one, else rely on the bounded SDK timeout.
            self.close_attempt_stream("cancelled attempt stream close during timeout failed")
            return
        # FD-ownership contract: only the thread driving the request may ``close()`` this
        # client's FDs. From a stranger thread (the watchdog Timer) only ``shutdown()`` is
        # FD-safe — ``close()`` releases the raw TLS fd while the owner's OpenSSL BIO still
        # caches it, the kernel recycles it (e.g. into a SQLite handle), and the owner's TLS
        # flush corrupts that file. The owner does the real close in its ``finally``.
        # This callback has two callers — ``_check_cancelled`` on the owning thread, and the daemon watchdog
        # ``threading.Timer``, which is a stranger thread. The owning thread performs the real close in the
        # ``finally`` below, which is where the FD release belongs. See #70773.
        self.timeout_release_pending.set()
        if threading.get_ident() == self._owner_tid:
            self._close_client_once("client close during timeout failed")
        else:
            try:
                from agent.agent_runtime_helpers import force_close_tcp_sockets
                shutdown_count = force_close_tcp_sockets(self._client)
                logger.info(
                    "Codex auxiliary client aborted (timeout, tcp_force_closed=%d, "
                    "deferred_close=stranger_thread)", shutdown_count)
            except Exception:
                logger.debug("Codex auxiliary: client abort during timeout failed", exc_info=True)
            # Socket shutdown only wakes a reader on a REAL transport; the owner may be blocked
            # inside the SDK's event stream (or a socketless test double). Closing the
            # attempt-owned stream releases it without touching shared FDs.
            self.close_attempt_stream("attempt stream close during stranger-thread timeout failed")
        # The aux client cache wraps this same client; drop the entry so the next aux call
        # doesn't reuse the dead transport and fail fast.
        try:
            # After we close the httpx transport above, the cache must drop that entry — otherwise the next
            # auxiliary call (compression retry, memory flush, etc.) reuses the dead client and fails fast
            # with a connection error. See issue #23432.
            _aux._evict_cached_client_instance(self._client)
        except Exception:
            logger.debug("Codex auxiliary: cache eviction on timeout failed", exc_info=True)

    def check_cancelled(self) -> None:
        if self.total_timeout is not None and time.monotonic() >= self.effective_deadline():
            if not self.timed_out.is_set():
                self._close_client_on_timeout()
            raise TimeoutError(self.timeout_message())
        try:
            from tools.interrupt import is_interrupted
            # Protected atomic aux tasks (compression) must not abort on a mid-flight gateway
            # interrupt (degraded fallback marker); explicit host cancel has its own exception.
            if _aux._aux_interrupt_cancel_requested():
                raise _aux.AuxiliaryExplicitCancellation()
            # Explicit host cancellation has its own frozen exception; timeouts above still fire and other
            # aux tasks remain interruptible. See #23975.
            if is_interrupted() and not _aux._aux_interrupt_protected():
                raise InterruptedError("Codex auxiliary Responses stream interrupted")
        except InterruptedError:
            raise
        except Exception:
            # Interrupt state is best-effort UX; never a new failure mode.
            pass

    def _watchdog_fire(self) -> None:
        # Re-armable: if progress moved the deadline forward, reschedule instead of killing a
        # live stream.
        remaining = self.effective_deadline() - time.monotonic()
        if remaining > 0:
            if not (self.timed_out.is_set() or self.stream_finished.is_set()):
                self._arm_timer(remaining)
            return
        self._close_client_on_timeout()

    def _arm_timer(self, delay: float) -> None:
        self._timer = t = threading.Timer(delay, self._watchdog_fire)
        t.daemon = True
        t.start()

    def start(self) -> None:
        """Arm the watchdog (when a total timeout exists) and run the first cancel check."""
        if self.total_timeout:
            self._arm_timer(max(self.effective_deadline() - time.monotonic(), 0.0))
        self.check_cancelled()

    def on_event(self, _event: Any) -> None:
        # TTFP telemetry records every frame, but forward progress (compression commit fence,
        # no-progress window) counts only substantive payloads — keepalives must not re-arm,
        # so a zombie stream dies at the same window as a dead connection.
        # #93650: keep bulk wire-format payload out of the SDK's GIL-holding request transform on auxiliary
        # calls too.
        if _aux._codex_event_has_content(_event):
            self.record_progress()
            self.saw_content.set()
            _aux._notify_aux_provider_response()
        else:
            _aux._notify_aux_timing_response()
        self.check_cancelled()

    def finish(self) -> None:
        """Owner ``finally``: stop the watchdog and release FDs a stranger-thread timeout only shut down."""
        self.stream_finished.set()
        if self._timer is not None:
            self._timer.cancel()
        # Gated on timeout_release_pending, NOT timed_out: after a hard-cancel the shared
        # client must stay usable for other sessions.
        if self.timeout_release_pending.is_set():
            self._close_client_once("owner-thread close after timeout failed")


class _CodexCompletionsAdapter:
    """Drop-in shim routing chat.completions.create() kwargs through Codex Responses streaming."""

    def __init__(self, real_client: _aux.OpenAI, model: str):
        self._client = real_client
        self._model = model

    def _build_responses_kwargs(self, kwargs: Dict[str, Any]) -> Tuple[Dict[str, Any], str, Any]:
        """chat.completions kwargs → Responses API kwargs, ``(resp_kwargs, model, timeout)``; mirrors codex.py::build_kwargs."""
        # Separate system/instructions from replayable conversation messages, then route the rest through
        # the SINGLE shared chat->Responses converter used by the main agent transport
        # (agent/transports/codex.py). Maintaining a private conversion loop here let chat-style messages
        # with role="tool" leak straight into Responses input[] — which the Responses API rejects with
        # "Invalid value: 'tool'. Supported values are: 'assistant', 'system', 'developer', and 'user'."
        # (issue #5709, hit hard by flush_memories() / compression replaying real session history that
        # includes assistant tool_calls + role="tool" results). The shared converter encodes assistant tool
        # calls as `function_call` items and tool results as `function_call_output` items with a valid
        # call_id, so every Responses path normalizes tool history identically and cannot drift.
        from agent.codex_responses_adapter import (
            _chat_messages_to_responses_input,
            _classify_responses_issuer,
            _wire_model_identity,
            classify_responses_route,
        )
        model = kwargs.get("model", self._model)
        wire_model = _wire_model_identity(model)
        host = str(getattr(self._client, "base_url", "") or "")
        is_copilot = _aux.base_url_host_matches(host, "githubcopilot.com")
        # Same route classifier as the main transport, so the issuer stamp matches what it minted.
        route = classify_responses_route(SimpleNamespace(provider=None, base_url=host))
        is_xai = route.is_xai_responses
        is_github = route.is_github_responses
        # System → ``instructions``; the rest goes through the SINGLE shared chat→Responses
        # converter (a private loop here once let role="tool" leak into input[]; the shared one
        # encodes tool history as function_call/function_call_output).
        instructions = "You are a helpful assistant."
        replay_messages: List[Dict[str, Any]] = []
        for msg in kwargs.get("messages", []):
            content = msg.get("content") or ""
            if msg.get("role", "user") == "system":
                instructions = content if isinstance(content, str) else str(content)
            else:
                replay_messages.append(msg)
        # Copilot binds replayed codex_message_items ids to a backend connection that doesn't
        # survive credential rotation (401 on replay) — same guard as build_kwargs. Aux calls
        # never send ``context_management`` (main-turn feature): no compaction checkpoint.
        # Auxiliary calls (context compression, flush_memories, MoA aggregation) go through this adapter
        # instead of agent/transports/codex.py's build_kwargs, so they need the same guard applied
        # independently. See #32716.
        # Aux requests run their own model; stamp/filter reasoning provenance against it, not the main agent's.
        input_items = _chat_messages_to_responses_input(
            replay_messages, is_github_responses=is_copilot,
            current_issuer_kind=_classify_responses_issuer(base_url=host, **route._asdict()),
            current_issuer_model=wire_model, native_compaction_eligible=False,
        )
        resp_kwargs: Dict[str, Any] = {
            # Codex only knows the base slug; strip the Hermes ``-900k`` picker suffix.
            "model": wire_model, "instructions": instructions,
            "input": input_items or [{"role": "user", "content": ""}], "store": False,
        }
        # Forward the chat.completions timeout; otherwise a Codex stream can sit behind a
        # dead-looking CLI until the user force-interrupts.
        timeout = kwargs.get("timeout")
        if timeout is not None:
            resp_kwargs["timeout"] = timeout
        # Per-request HTTP headers (OpenCode session affinity, Copilot x-initiator) map to real
        # headers via the SDK kwarg — forward them.
        if isinstance(kwargs.get("extra_headers"), dict) and kwargs["extra_headers"]:
            resp_kwargs["extra_headers"] = dict(kwargs["extra_headers"])
        # The Codex endpoint rejects max_output_tokens/temperature (400) — omit.
        extra_body = kwargs.get("extra_body") or {}
        if isinstance(extra_body, dict):
            # service_tier (fast mode) is a top-level Responses field; xAI's endpoint rejects it.
            service_tier = extra_body.get("service_tier")
            if isinstance(service_tier, str) and service_tier.strip() and not is_xai:
                resp_kwargs["service_tier"] = service_tier.strip()
            reasoning_cfg = extra_body.get("reasoning")
            # ``enabled: False`` leaves reasoning/include unset (Codex still thinks by default).
            if isinstance(reasoning_cfg, dict) and reasoning_cfg.get("enabled") is not False:
                # Truthy-only: Codex 400s on e.g. {"effort": null}, so falsy → default. Shared
                # per-model clamp with the main transport ("max" is gpt-5.6-only; "minimal"/"ultra" rejected).
                from agent.reasoning_effort import clamp_effort
                from agent.transports.codex import _codex_efforts_for_route
                effort = clamp_effort(
                    reasoning_cfg.get("effort") or "medium",
                    _codex_efforts_for_route(model, host, is_codex_backend=route.is_codex_backend),
                )
                resp_kwargs["reasoning"] = {"effort": effort, "summary": "auto"}
                resp_kwargs["include"] = ["reasoning.encrypted_content"]
        tools = kwargs.get("tools")
        if tools:
            # xAI Responses rejects ``pattern``/``format`` JSON Schema keywords (400); strip for
            # chat_completion_helpers.py parity. Deep-copy first — sanitizers mutate inner dicts
            # in place and would strip the caller's tool registry.
            try:
                import copy as _copy
                from tools.schema_sanitizer import strip_pattern_and_format, strip_slash_enum
                tools = _copy.deepcopy(list(tools))
                tools, _ = strip_pattern_and_format(tools)
                tools, _ = strip_slash_enum(tools)
            except Exception as exc:
                logger.warning(
                    "Auxiliary client: failed to sanitize tool schemas for "
                    "Codex/xAI Responses path: %s", exc,
                )
            converted = []
            for t in tools:
                fn = t.get("function", {}) if isinstance(t, dict) else {}
                name = fn.get("name")
                if name:
                    converted.append({
                        "type": "function", "name": name, "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
            if converted:
                resp_kwargs["tools"] = converted
        # Stable prompt-cache routing: key is content-addressed from the static prefix
        # (instructions + tool schemas) so it survives across turns, scoped by the owning
        # conversation (rotation-stable logical scope, else the physical session id). Skip the
        # key where the main transport does: xAI takes it in extra_body, GitHub opts out.
        try:
            # Reuse the Responses transport's single authoritative hash algorithm and session-scope
            # normalization so equivalent static prefixes route to the same cache bucket across modes,
            # without concentrating unrelated sessions into one shared bucket (see #78941).
            from agent.transports.codex import _cache_scope_from_session_id, _content_cache_key
            from agent.transports.codex import _default_prompt_cache_retention_for_request
            if not (is_xai or is_github) and "prompt_cache_key" not in resp_kwargs:
                scope = _cache_scope_from_session_id(
                    _aux._runtime_main_value("cache_scope") or _aux._runtime_main_value("session_id")
                )
                cache_key = _content_cache_key(resp_kwargs["instructions"], resp_kwargs.get("tools"), scope)
                if cache_key:
                    resp_kwargs["prompt_cache_key"] = cache_key
            if "prompt_cache_retention" not in resp_kwargs:
                cache_retention = _default_prompt_cache_retention_for_request(model, host)
                if cache_retention:
                    resp_kwargs["prompt_cache_retention"] = cache_retention
        except Exception:
            logger.debug("Codex auxiliary: prompt_cache_key derivation skipped", exc_info=True)
        # Last, like the main transport: caller extra_body must not put a rejected Astra field back.
        from agent.transports.codex import _sanitize_astra_request_kwargs
        _sanitize_astra_request_kwargs(resp_kwargs, model, host)
        return resp_kwargs, model, timeout

    def create(self, **kwargs) -> Any:
        from hermes_cli.providers import is_actual_route

        if is_actual_route(
            getattr(self._client, "_hermes_aux_effective_provider", ""),
            str(getattr(self._client, "base_url", "") or ""),
        ):
            raise ValueError(
                "Actual requests require Chat Completions; refusing to call /responses."
            )
        # Low-level ``responses.create(stream=True)`` and assemble the final response ourselves
        # from ``response.output_item.done``: the high-level ``responses.stream()`` rebuilds from
        # ``response.completed.response.output``, which Codex returns as ``null`` (SDK crash).
        resp_kwargs, model, timeout = self._build_responses_kwargs(kwargs)
        total_timeout = timeout if isinstance(timeout, (int, float)) and timeout > 0 else None
        guard = _CodexStreamGuard(self._client, total_timeout)
        try:
            guard.start()
            from agent.codex_runtime import _bypass_sdk_request_transform, _consume_codex_event_stream
            # Keep bulk wire payload out of the SDK's GIL-holding request transform.
            stream_kwargs = _bypass_sdk_request_transform({**resp_kwargs, "stream": True})
            event_stream = self._client.responses.create(**stream_kwargs)
            guard.adopt_stream(event_stream)
            # The timer may fire while responses.create() is blocked; if the cancelled attempt
            # had no stream to close then, close it now that it is attempt-owned — never the shared client.
            if guard.timed_out.is_set() and guard.cancel_requested():
                guard.close_attempt_stream("late cancelled attempt stream close failed")
            try:
                # Some Codex-compatible hosts accept ``stream=True`` but return a completed
                # Responses object (not iterable) — don't hand it to the consumer.
                if hasattr(event_stream, "output"):
                    final = event_stream
                else:
                    final = _consume_codex_event_stream(
                        event_stream, model=str(resp_kwargs.get("model") or model), on_event=guard.on_event
                    )
            finally:
                guard.release_stream(event_stream)
            if final is None:
                raise RuntimeError("Codex auxiliary Responses stream did not return a final response")
            text_parts, tool_calls_raw, usage = _parse_codex_final_response(final)
        except Exception as exc:
            if guard.timed_out.is_set():
                raise TimeoutError(guard.timeout_message()) from exc
            logger.debug("Codex auxiliary Responses API call failed: %s", exc)
            raise
        finally:
            guard.finish()
        # Shape the result like chat.completions.
        message = SimpleNamespace(
            role="assistant", content="".join(text_parts).strip() or None,
            tool_calls=tool_calls_raw or None,
        )
        choice = SimpleNamespace(
            index=0, message=message, finish_reason="stop" if not tool_calls_raw else "tool_calls"
        )
        return SimpleNamespace(choices=[choice], model=model, usage=usage)


class _ChatShim:
    """Exposes ``client.chat.completions.create()`` over a sync or async adapter."""

    def __init__(self, adapter: Any):
        self.completions = adapter


class _AsyncCompletionsAdapter:
    """Async adapter: runs the sync adapter's ``create`` via asyncio.to_thread()."""

    def __init__(self, sync_adapter: Any):
        self._sync = sync_adapter

    async def create(self, **kwargs) -> Any:
        import asyncio
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncAuxiliaryClientBase:
    """Async-compatible wrapper matching AsyncOpenAI.chat.completions.create().

    Mirrors ``_real_client`` (when the sync wrapper has one) so cache eviction by
    leaf OpenAI client drops this async entry too instead of reusing a closed transport.
    """

    def __init__(self, sync_wrapper: Any):
        self.chat = _ChatShim(_AsyncCompletionsAdapter(sync_wrapper.chat.completions))
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url
        if hasattr(sync_wrapper, "_real_client"):
            # Mirror the sync wrapper's _real_client so cache eviction by leaf OpenAI client (e.g.
            # _close_client_on_timeout in #23482) drops this async entry too. Without this, sync and async
            # cache entries diverge on poisoning: the sync entry is evicted but the async entry keeps
            # reusing the closed transport, failing every subsequent async aux call with 'Connection error'
            # until the gateway restarts.
            self._real_client = sync_wrapper._real_client


_AsyncAnthropicCompletionsAdapter = _AsyncCompletionsAdapter  # imported by tests


class CodexAuxiliaryClient:
    """OpenAI-client-compatible wrapper routing through the Codex Responses API (.api_key/.base_url for introspection)."""

    def __init__(self, real_client: _aux.OpenAI, model: str):
        self._real_client = real_client
        self.chat = _ChatShim(_CodexCompletionsAdapter(real_client, model))
        self.api_key = real_client.api_key
        self.base_url = real_client.base_url

    def close(self):
        self._real_client.close()


class AsyncCodexAuxiliaryClient(_AsyncAuxiliaryClientBase):
    pass


def _translate_anthropic_response_format(anthropic_kwargs: Dict[str, Any], response_format: Any) -> None:
    """Merge an OpenAI response format into Anthropic ``output_config``."""
    if not isinstance(response_format, dict):
        return
    format_type = response_format.get("type")
    if format_type == "json_schema":
        json_schema = response_format.get("json_schema")
        if not isinstance(json_schema, dict) or "schema" not in json_schema:
            return
        schema = json_schema["schema"]
    elif format_type == "json_object":
        # Anthropic SDK has no schema-less JSON mode; only ``json_schema``.
        schema = {"type": "object"}
    else:
        return
    output_config = anthropic_kwargs.get("output_config")
    if not isinstance(output_config, dict):
        output_config = {}
        anthropic_kwargs["output_config"] = output_config
    output_config["format"] = {"type": "json_schema", "schema": schema}


class _AnthropicCompletionsAdapter:
    """OpenAI-client-compatible adapter for Anthropic Messages API."""

    def __init__(self, real_client: Any, model: str, is_oauth: bool = False, base_url: str | None = None):
        self._client = real_client
        self._model = model
        self._is_oauth = is_oauth
        # Caller URL first; fall back to the SDK client's host only for Nous Portal — a blanket
        # fallback would flip MiniMax/Zhipu aux adapters to third-party handling (strips thinking sigs).
        self._base_url = base_url or None
        if not self._base_url:
            candidate = str(getattr(real_client, "base_url", "") or "") or None
            if candidate:
                with contextlib.suppress(Exception):
                    from agent.anthropic_endpoints import _is_nous_portal_endpoint
                    if _is_nous_portal_endpoint(candidate):
                        self._base_url = candidate

    def create(self, **kwargs) -> Any:
        from agent.anthropic_adapter import build_anthropic_kwargs, create_anthropic_message
        from agent.transports import get_transport
        model = kwargs.get("model", self._model)
        # ZAI's Anthropic endpoint rejects max_tokens on vision models (code 1210);
        # callers signal this via _skip_zai_max_tokens.
        if kwargs.pop("_skip_zai_max_tokens", False):
            max_tokens = None
        else:
            max_tokens = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens")
        temperature = kwargs.get("temperature")
        # Reasoning priority: explicit per-call _reasoning_config (MoA per-slot) wins over
        # extra_body.reasoning; build_anthropic_kwargs translates to ``thinking``.
        reasoning_cfg = kwargs.get("_reasoning_config")
        if reasoning_cfg is None:
            _eb = kwargs.get("extra_body")
            _rc = _eb.get("reasoning") if isinstance(_eb, dict) else None
            if isinstance(_rc, dict):
                reasoning_cfg = _rc
        # OpenAI tool_choice (str or dict) → Anthropic-style name/mode string.
        tool_choice = kwargs.get("tool_choice")
        if isinstance(tool_choice, dict):
            choice_type = str(tool_choice.get("type", "")).lower()
            if choice_type == "function":
                tool_choice = tool_choice.get("function", {}).get("name")
            else:
                tool_choice = choice_type if choice_type in {"auto", "required", "none"} else None
        elif not isinstance(tool_choice, str):
            tool_choice = None
        anthropic_kwargs = build_anthropic_kwargs(
            model=model, messages=kwargs.get("messages", []), tools=kwargs.get("tools"),
            max_tokens=max_tokens, reasoning_config=reasoning_cfg, tool_choice=tool_choice,
            is_oauth=self._is_oauth,
            # Portal routes on ``anthropic/<slug>`` ids and replays signed thinking
            # keyed off base_url; omitting it breaks Portal model resolution.
            base_url=self._base_url,
        )
        # Opus 4.7+ rejects non-default temperature/top_p/top_k; build_anthropic_kwargs
        # also strips these as a safety net — keep both layers.
        if temperature is not None:
            from agent.anthropic_adapter import _forbids_sampling_params
            if not _forbids_sampling_params(model):
                anthropic_kwargs["temperature"] = temperature
        # Per-request HTTP headers (OpenCode session affinity) — the Anthropic SDK accepts
        # ``extra_headers`` on messages.create/stream too.
        if isinstance(kwargs.get("extra_headers"), dict) and kwargs["extra_headers"]:
            anthropic_kwargs["extra_headers"] = {
                **(anthropic_kwargs.get("extra_headers") or {}),
                **kwargs["extra_headers"],
            }
        # response_format: top-level gets the same translation as the extra_body form; when both
        # are present the extra_body form wins. Passthrough excludes ``reasoning``/``response_format``
        # (already TRANSLATED to native fields — raw would 400 on strict gateways) and ``_`` Hermes plumbing.
        # The adapter builds the Messages body from a fixed allow-list of kwargs, so before this an
        # unrecognized top-level kwarg was dropped on the floor: the request succeeded but the schema
        # contract silently became prompt compliance (#85626 review, point 2).
        top_level_response_format = kwargs.get("response_format")
        if top_level_response_format is not None:
            _translate_anthropic_response_format(anthropic_kwargs, top_level_response_format)
        caller_extra_body = kwargs.get("extra_body")
        if caller_extra_body and isinstance(caller_extra_body, dict):
            _translate_anthropic_response_format(anthropic_kwargs, caller_extra_body.get("response_format"))
            passthrough = {
                k: v for k, v in caller_extra_body.items()
                if k not in {"reasoning", "response_format"} and not str(k).startswith("_")
            }
            if passthrough:
                existing = anthropic_kwargs.get("extra_body") or {}
                if not isinstance(existing, dict):
                    existing = {}
                anthropic_kwargs["extra_body"] = {**existing, **passthrough}
        response = create_anthropic_message(
            self._client,
            anthropic_kwargs,
            # Record provider-response timing every event, but tick forward progress only for
            # substantive payloads so keepalives can't hold a stalled summary open. None keeps
            # the fast get_final_message path.
            on_stream_event=(_aux._anthropic_aux_stream_event_hook() if _aux._aux_progress_active() else None),
        )
        _nr = get_transport("anthropic_messages").normalize_response(response, strip_tool_prefix=self._is_oauth)
        usage = None
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = getattr(response.usage, "input_tokens", 0) or 0
            completion_tokens = getattr(response.usage, "output_tokens", 0) or 0
            usage = SimpleNamespace(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=getattr(response.usage, "total_tokens", 0) or (prompt_tokens + completion_tokens),
            )
        # ToolCall already duck-types as OpenAI shape via properties.
        choice = SimpleNamespace(
            index=0,
            message=SimpleNamespace(content=_nr.content, tool_calls=_nr.tool_calls, reasoning=_nr.reasoning),
            finish_reason=_nr.finish_reason,
        )
        return SimpleNamespace(choices=[choice], model=model, usage=usage)


class AnthropicAuxiliaryClient:
    """OpenAI-client-compatible wrapper over a native Anthropic client."""

    def __init__(self, real_client: Any, model: str, api_key: str, base_url: str, is_oauth: bool = False):
        self._real_client = real_client
        self.chat = _ChatShim(_AnthropicCompletionsAdapter(real_client, model, is_oauth=is_oauth, base_url=base_url))
        self.api_key = api_key
        self.base_url = base_url

    def close(self):
        close_fn = getattr(self._real_client, "close", None)
        if callable(close_fn):
            close_fn()


class AsyncAnthropicAuxiliaryClient(_AsyncAuxiliaryClientBase):
    pass


class _BedrockCompletionsAdapter:
    """Translates ``chat.completions.create(**kwargs)`` into Bedrock Converse."""

    def __init__(self, region: str, model: str):
        self._region = region
        self._model = model

    def create(self, **kwargs) -> Any:
        from agent.bedrock_adapter import call_converse
        model = kwargs.get("model", self._model)
        max_tokens = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens")
        # OpenAI accepts ``stop`` as str or list; Converse requires a list.
        stop = kwargs.get("stop")
        if isinstance(stop, str):
            stop = [stop]
        if kwargs.get("tool_choice") is not None:
            # Converse toolChoice isn't wired through call_converse(); surface the drop.
            logger.debug(
                "BedrockAuxiliaryClient: tool_choice=%r not supported by the "
                "Converse shim — ignored.", kwargs.get("tool_choice"),
            )
        if kwargs.get("stream"):
            # Converse streaming isn't wired here; call_llm's streaming consumer
            # detects a final object and downgrades to non-live output.
            logger.debug(
                "BedrockAuxiliaryClient: stream=True requested for %s — returning a complete response "
                "(Converse shim does not stream); caller downgrades to non-streaming.", model,
            )
        response = call_converse(
            region=self._region, model=model, messages=kwargs.get("messages", []), tools=kwargs.get("tools"),
            # Converse specifically defaults to the model maximum when omitted.
            # Truthiness mirrors the Anthropic shim: explicit 0 means omit.
            max_tokens=int(max_tokens) if max_tokens else None, temperature=kwargs.get("temperature"),
            top_p=kwargs.get("top_p"), stop_sequences=stop,
        )
        # Converse is complete-response here: mark provider progress only after
        # return so TTFP reflects real Bedrock latency, not dispatch/setup.
        _aux._notify_aux_provider_response()
        return response


class BedrockAuxiliaryClient:
    """OpenAI-client-compatible wrapper over AWS Bedrock Converse API."""

    def __init__(self, region: str, model: str):
        self._region = region
        self._model = model
        self.chat = _ChatShim(_BedrockCompletionsAdapter(region, model))
        self.api_key = "aws-sdk"
        self.base_url = f"https://bedrock-runtime.{region}.amazonaws.com"

    def close(self):
        pass


class AsyncBedrockAuxiliaryClient(_AsyncAuxiliaryClientBase):
    pass


def _endpoint_speaks_anthropic_messages(base_url: str) -> bool:
    """True if ``base_url`` speaks Anthropic Messages, not OpenAI chat.completions.

    Mirrors ``hermes_cli.runtime_provider._detect_api_mode_for_url`` so aux and main agree: any
    ``/anthropic`` URL (MiniMax, Zhipu, LiteLLM), ``api.kimi.com/coding`` (chat 404s), ``api.anthropic.com``.
    """
    normalized = (base_url or "").strip().lower().rstrip("/")
    if not normalized:
        return False
    if urlparse(normalized).path.rstrip("/").endswith(("/anthropic", "/anthropic/v1")):
        return True
    hostname = _aux.base_url_hostname(normalized)
    return hostname == "api.anthropic.com" or bool(hostname == "api.kimi.com" and "/coding" in normalized)


def _maybe_wrap_anthropic(
    client_obj: Any, model: str, api_key: str, base_url: str, api_mode: Optional[str] = None
) -> Any:
    """Rewrap a plain OpenAI client in ``AnthropicAuxiliaryClient`` when the endpoint speaks Anthropic Messages.

    Single transport-correction chokepoint at the end of every ``resolve_provider_client`` branch; returns
    ``client_obj`` unchanged for probe stubs/specialized adapters, OpenAI-wire, explicit non-Anthropic
    ``api_mode``, or missing ``anthropic`` SDK.
    """
    # Anthropic/Bedrock/Codex wrappers, plus any client declaring HERMES_SKIP_TRANSPORT_WRAP
    # (native/ACP shims, in-tree or plugin), must never be re-dispatched through a wire adapter —
    # a class-attribute declaration rather than isinstance so this hot path never imports them.
    if (
        isinstance(client_obj, _aux._AuxProbeClientStub)
        or _aux._safe_isinstance(client_obj, (AnthropicAuxiliaryClient, BedrockAuxiliaryClient, CodexAuxiliaryClient))
        or _aux._client_declares(client_obj, "HERMES_SKIP_TRANSPORT_WRAP")
    ):
        return client_obj
    # Explicit non-anthropic api_mode wins over URL heuristics.
    if api_mode != "anthropic_messages" and (api_mode or not _endpoint_speaks_anthropic_messages(base_url)):
        return client_obj
    try:
        from agent.anthropic_adapter import build_anthropic_client
    except ImportError:
        logger.warning(
            "Endpoint %s speaks Anthropic Messages but the anthropic SDK is "
            "not installed — falling back to OpenAI-wire (will likely 404).",
            base_url,
        )
        return client_obj
    try:
        real_client = build_anthropic_client(api_key, base_url)
    except Exception as exc:
        logger.warning(
            "Failed to build Anthropic client for %s (%s) — falling back to "
            "OpenAI-wire client.", base_url, exc,
        )
        return client_obj
    logger.debug(
        "Auxiliary transport: wrapping client in AnthropicAuxiliaryClient "
        "(model=%s, base_url=%s, api_mode=%s)",
        model, base_url[:60] if base_url else "", api_mode or "auto-detected",
    )
    return AnthropicAuxiliaryClient(real_client, model, api_key, base_url, is_oauth=False)


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
