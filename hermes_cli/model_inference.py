"""One-shot selected-model inference through the existing runtime and agent transport."""

from __future__ import annotations

import time
from typing import Any


_PROMPT = "Reply with exactly: Stardust model test OK"
_SYSTEM = "You are checking model connectivity. Reply only with the requested short text."
_MAX_TIMEOUT_S = 30.0
_TOKEN_LIMIT_KEYS = (
    "max_tokens", "max_completion_tokens", "max_output_tokens", "max_new_tokens", "num_predict",
)
_RESERVED_EXTRA_BODY_KEYS = frozenset({
    "tools", "functions", "function_call", "tool_choice", "parallel_tool_calls",
    "messages", "model", "stream",
})
_FORBIDDEN_TOP_LEVEL_FUNCTION_KEYS = frozenset({
    "functions", "function_call", "tool_choice", "parallel_tool_calls",
})
_SAFE_EXTRA_BODY_KEYS = frozenset({
    "temperature", "top_p", "frequency_penalty", "presence_penalty", "seed", "service_tier", "metadata",
})
_SUPPORTED_EXTRA_BODY_KEYS = _SAFE_EXTRA_BODY_KEYS.union(_TOKEN_LIMIT_KEYS)


def _status_code(exc: BaseException) -> int | None:
    for owner in (exc, getattr(exc, "response", None)):
        raw = getattr(owner, "status_code", None) or getattr(owner, "status", None)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _error_kind(exc: BaseException) -> tuple[str, str]:
    code = _status_code(exc)
    if code in (401, 403):
        return "auth", "The provider rejected authentication."
    if code == 429:
        return "rate_limit", "The provider rate limit was reached."
    if code is not None and code >= 500:
        return "server", "The provider returned a server error."
    name = type(exc).__name__.lower()
    if "timeout" in name or isinstance(exc, TimeoutError):
        return "timeout", "The model request timed out."
    if any(token in name for token in ("connection", "network", "socket", "protocol", "readerror")):
        return "network", "Could not connect to the selected model route."
    return "unknown", "The model request could not be completed."


def run_model_inference_test(
    runtime: dict[str, Any], *, selected_provider: str, selected_model: str,
    timeout_s: float, deadline: float | None = None, cancel_check=lambda: False,
    on_agent=lambda _agent: None, request_guard=lambda: True,
    on_response_received=lambda: True,
):
    """Send exactly one bounded, non-streaming request without a session/history or tool surface.

    Only the existing non-streaming OpenAI-compatible client path currently has a proven cross-thread
    socket-abort hook. Other API modes return unsupported, rather than being translated to a different
    protocol or claiming that cancellation works.
    """
    started = time.monotonic()
    timeout = min(max(float(timeout_s), 0.1), _MAX_TIMEOUT_S)
    absolute_deadline = deadline if deadline is not None else started + timeout
    timeout = min(timeout, max(absolute_deadline - started, 0.0))
    provider = str(runtime.get("provider") or selected_provider)
    source = "resolved_runtime"
    api_mode = str(runtime.get("api_mode") or "")
    if runtime.get("command") or api_mode != "chat_completions" or provider in {"moa", "lmstudio"}:
        return {
            "status": "unsupported", "inference_ok": None, "provider": provider,
            "model": selected_model, "source": source,
            "error": "Inference testing is unsupported for this provider route.",
            "cancel_supported": False,
        }
    if cancel_check():
        return {"status": "canceled", "inference_ok": None, "provider": provider,
                "model": selected_model, "source": source, "error": "The model test was stopped.",
                "cancel_supported": True}
    if timeout <= 0:
        return {"status": "timed_out", "inference_ok": False, "provider": provider,
                "model": selected_model, "source": source, "error_kind": "timeout",
                "error": "The model request timed out.", "cancel_supported": False}

    agent = None
    dispatch_authorized = False

    def authorize_dispatch() -> bool:
        nonlocal dispatch_authorized
        if time.monotonic() >= absolute_deadline:
            return False
        accepted = bool(request_guard())
        if accepted:
            dispatch_authorized = True
        return accepted

    try:
        from run_agent import AIAgent
        from agent.chat_completion_helpers import direct_api_call
        from agent.message_sanitization import sanitize_outbound_kwargs

        class _ModelInferenceAgent(AIAgent):
            def _client_log_context(self) -> str:
                return "one-shot-model-inference"

        agent = _ModelInferenceAgent(
            model=selected_model,
            provider=provider,
            requested_provider=str(runtime.get("requested_provider") or selected_provider),
            api_mode=api_mode,
            base_url=runtime.get("base_url"),
            api_key=runtime.get("api_key"),
            credential_pool=runtime.get("credential_pool"),
            command=runtime.get("command"),
            request_overrides=runtime.get("request_overrides") or {},
            enabled_toolsets=[],
            disabled_toolsets=[],
            max_iterations=1,
            max_tokens=16,
            service_tier="normal",
            quiet_mode=True,
            save_trajectories=False,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            run_budget_seconds=timeout,
            context_length_override=64_000,
        )
        agent._persist_disabled = True
        agent._api_max_retries = 0
        agent._fallback_chain = []
        agent._fallback_model = None
        on_agent(agent)
        if cancel_check():
            return {"status": "canceled", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source, "error": "The model test was stopped.",
                    "cancel_supported": True}
        remaining = absolute_deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "timed_out", "inference_ok": False, "provider": provider,
                    "model": selected_model, "source": source, "error_kind": "timeout",
                    "error": "The model request timed out.", "cancel_supported": False}
        agent._compute_non_stream_stale_timeout = lambda _payload: max(0.001, absolute_deadline - time.monotonic())

        api_kwargs = agent._build_api_kwargs(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": _PROMPT}],
            tools_for_api=[],
        )
        sanitize_outbound_kwargs(agent, api_kwargs)
        if _FORBIDDEN_TOP_LEVEL_FUNCTION_KEYS.intersection(api_kwargs):
            return {"status": "unsupported", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source,
                    "error": "This provider route has unsupported function-call request fields.",
                    "cancel_supported": False}
        extra_body = api_kwargs.get("extra_body")
        if extra_body is not None:
            if not isinstance(extra_body, dict):
                return {"status": "unsupported", "inference_ok": None, "provider": provider,
                        "model": selected_model, "source": source,
                        "error": "This provider route has unsupported extra request fields.",
                        "cancel_supported": False}
            extra_body_keys = set(extra_body)
            if (_RESERVED_EXTRA_BODY_KEYS.intersection(extra_body_keys)
                    or not extra_body_keys.issubset(_SUPPORTED_EXTRA_BODY_KEYS)):
                return {"status": "unsupported", "inference_ok": None, "provider": provider,
                        "model": selected_model, "source": source,
                        "error": "This provider route has unsupported extra request fields.",
                        "cancel_supported": False}
        extra_body = api_kwargs.get("extra_body")
        if isinstance(extra_body, dict) and _RESERVED_EXTRA_BODY_KEYS.intersection(extra_body):
            return {"status": "unsupported", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source,
                    "error": "This provider route overrides required minimal request fields.",
                    "cancel_supported": False}
        wire_tools = api_kwargs.get("tools")
        if wire_tools:
            return {"status": "unsupported", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source,
                    "error": "This provider route added tools to the minimal probe request.",
                    "cancel_supported": False}
        api_kwargs.pop("tools", None)
        api_kwargs["model"] = selected_model
        api_kwargs["stream"] = False
        api_kwargs["timeout"] = remaining
        token_keys = _TOKEN_LIMIT_KEYS
        bounded_token_keys = [key for key in token_keys if key in api_kwargs]
        if not bounded_token_keys:
            return {"status": "unsupported", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source,
                    "error": "This provider route cannot enforce a small output limit.",
                    "cancel_supported": False}
        try:
            for key in bounded_token_keys:
                value = api_kwargs[key]
                if isinstance(value, bool) or int(value) <= 0:
                    raise ValueError("invalid output cap")
                api_kwargs[key] = min(int(value), 16)
            extra_body = api_kwargs.get("extra_body")
            if isinstance(extra_body, dict):
                for key in token_keys:
                    if key in extra_body:
                        value = extra_body[key]
                        if isinstance(value, bool) or int(value) <= 0:
                            raise ValueError("invalid nested output cap")
                        extra_body[key] = min(int(value), 16)
        except (TypeError, ValueError):
            return {"status": "unsupported", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source,
                    "error": "This provider route cannot enforce a small output limit.",
                    "cancel_supported": False}
        wire_messages = api_kwargs.get("messages")
        if (not isinstance(wire_messages, list) or len(wire_messages) != 2
                or any(not isinstance(message, dict) for message in wire_messages)
                or wire_messages[0].get("role") not in {"system", "developer"}
                or wire_messages[0].get("content") != _SYSTEM
                or wire_messages[1].get("role") != "user"
                or wire_messages[1].get("content") != _PROMPT):
            return {"status": "unsupported", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source,
                    "error": "This provider route changed the minimal probe message shape.",
                    "cancel_supported": False}
        remaining = absolute_deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "timed_out", "inference_ok": False, "provider": provider,
                    "model": selected_model, "source": source, "error_kind": "timeout",
                    "error": "The model request timed out.", "cancel_supported": False}
        api_kwargs["timeout"] = remaining

        response = direct_api_call(
            agent, api_kwargs, request_guard=authorize_dispatch,
            on_response_received=on_response_received,
        )
        if cancel_check():
            return {"status": "canceled", "inference_ok": None, "provider": provider,
                    "model": selected_model, "source": source, "error": "The model test was stopped.",
                    "cancel_supported": True}
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            return {"status": "failed", "inference_ok": False, "provider": provider,
                    "model": selected_model, "source": source,
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    "error_kind": "empty_response", "error": "The model returned an empty response.",
                    "nonempty_response": False, "cancel_supported": True}
        return {"status": "success", "inference_ok": True, "provider": provider,
                "model": selected_model, "source": source,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "nonempty_response": True, "cancel_supported": True}
    except (TimeoutError, InterruptedError) as exc:
        canceled = bool(agent is not None and getattr(agent, "_interrupt_requested", False))
        abort_confirmed = bool(agent is not None and getattr(agent, "_last_interrupt_abort_succeeded", False))
        kind, error = _error_kind(exc)
        if canceled and dispatch_authorized and not abort_confirmed:
            return {"status": "cancel_not_supported", "inference_ok": None,
                    "provider": provider, "model": selected_model, "source": source,
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    "error_kind": None,
                    "error": "The provider request may continue until its timeout.",
                    "cancel_supported": False}
        return {"status": "canceled" if canceled else "timed_out" if kind == "timeout" else "failed",
                "inference_ok": None if canceled else False,
                "provider": provider, "model": selected_model,
                "source": source, "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error_kind": None if canceled else kind,
                "error": "The model test was stopped." if canceled else error,
                "cancel_supported": abort_confirmed}
    except Exception as exc:  # raw SDK exceptions/bodies can contain credentials or endpoint metadata
        kind, error = _error_kind(exc)
        return {"status": "timed_out" if kind == "timeout" else "failed",
                "inference_ok": False,
                "provider": provider, "model": selected_model, "source": source,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error_kind": kind, "error": error, "cancel_supported": True}
    finally:
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass
