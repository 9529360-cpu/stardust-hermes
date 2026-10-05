"""Building and reading one auxiliary request: provider-specific payload shaping, call
kwargs, response validation and text extraction, streaming aggregation with progress,
client resolution for a call, request preparation and the shared head of both call paths.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import re
import time
from types import SimpleNamespace
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


# Anthropic-compatible endpoints reached via the OpenAI SDK wrapper; their image content blocks
# must use Anthropic format.
_ANTHROPIC_COMPAT_PROVIDERS = frozenset({"minimax", "minimax-oauth", "minimax-cn"})


def _is_anthropic_compat_endpoint(provider: str, base_url: str) -> bool:
    """True for known Anthropic-compatible providers or any ``/anthropic`` URL path."""
    return provider in _ANTHROPIC_COMPAT_PROVIDERS or "/anthropic" in (base_url or "").lower()


# OpenAI block type → (Anthropic block type, default media type for data: URLs). MiniMax's
# Anthropic-compatible endpoint wants type="video" (not "video_url"/"input_video") with the same
# ``source`` shape as "image".
_ANTHROPIC_MEDIA_BLOCKS = {"image_url": ("image", "image/png"), "video_url": ("video", "video/mp4")}


def _convert_openai_images_to_anthropic(messages: list) -> list:
    """Convert OpenAI ``image_url``/``video_url`` blocks to Anthropic ``image``/``video``;
    only list-content messages with such blocks change."""
    converted = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            converted.append(msg)
            continue
        new_content = []
        changed = False
        for block in content:
            block_type = block.get("type")
            if block_type not in _ANTHROPIC_MEDIA_BLOCKS:
                new_content.append(block)
                continue
            url = (block.get(block_type) or {}).get("url", "")
            anth_type, media_type = _ANTHROPIC_MEDIA_BLOCKS[block_type]
            if url.startswith("data:"):
                header, _, b64data = url.partition(",")
                if ":" in header and ";" in header:
                    media_type = header.split(":", 1)[1].split(";", 1)[0]
                source = {"type": "base64", "media_type": media_type, "data": b64data}
            else:
                source = {"type": "url", "url": url}
            new_content.append({"type": anth_type, "source": source})
            changed = True
        converted.append({**msg, "content": new_content} if changed else msg)
    return converted


_PROFILE_REASONING_KEYS = {
    "reasoning", "reasoning_effort", "thinking", "thinking_config", "thinkingconfig",
    "thinking_budget", "thinkingbudget", "enable_thinking", "think", "verbosity",
}


def _contains_profile_reasoning_fields(value: Any) -> bool:
    """Return whether a profile payload contains a reasoning wire control (recursive)."""
    if not isinstance(value, dict):
        return False
    return any(
        str(key).strip().lower() in _PROFILE_REASONING_KEYS or _contains_profile_reasoning_fields(nested)
        for key, nested in value.items()
    )


_NOUS_PROVIDER_NAMES = frozenset({"nous", "nous-portal", "nousresearch"})


def _nous_on_messages_wire(provider_norm: str, model: str) -> bool:
    """True when a Nous Portal route serves ``model`` over /v1/messages (dual-wire catalog)."""
    if provider_norm not in _NOUS_PROVIDER_NAMES:
        return False
    from hermes_cli.providers import nous_api_mode
    return nous_api_mode(model) == "anthropic_messages"


_NVIDIA_PROVIDER_NAMES = {"nvidia", "nvidia-nim", "nim", "build-nvidia", "nemotron"}
_GEMINI_NATIVE_PROVIDER_NAMES = {"gemini", "google", "google-gemini", "google-ai-studio"}


def _is_gemini_native_route(provider_norm: str, effective_base: str) -> bool:
    """Gemini native by provider name, else (best-effort) by base URL shape."""
    if provider_norm in _GEMINI_NATIVE_PROVIDER_NAMES:
        return True
    if not effective_base:
        return False
    try:
        from agent.gemini_native_adapter import is_native_gemini_base_url
        return is_native_gemini_base_url(effective_base)
    except Exception:
        return False


def _forwards_max_tokens(provider: str, provider_norm: str, model: str, effective_base: str, task: Optional[str]) -> bool:
    """Whether an explicit max_tokens is forwarded on this route.

    No default cap elsewhere (omitted = provider default; avoids max_completion_tokens / ZAI-vision
    quirks). Forward only where mandatory or honored: Anthropic Messages wire (400 without it);
    NVIDIA NIM (empty choices[] when omitted); MoA reference slots; Gemini native (fixed 65,535
    ceiling otherwise); OpenRouter (budgets the FULL window when omitted → 402 on low credit);
    managed local llama-server (uncapped decode with no EOS burns the GPU to the context window).
    """
    return (
        _is_anthropic_compat_endpoint(provider, effective_base)
        or _nous_on_messages_wire(provider_norm, model)
        or provider_norm in _NVIDIA_PROVIDER_NAMES
        or _aux.base_url_host_matches(effective_base, "integrate.api.nvidia.com")
        or str(task) == "moa_reference"
        or _is_gemini_native_route(provider_norm, effective_base)
        or provider_norm == "openrouter"
        or _aux.base_url_host_matches(effective_base, "openrouter.ai")
        or _is_managed_local_endpoint(effective_base)
    )


def _dedupe_tool_names(tools: list, provider: str, model: str) -> list:
    """Drop duplicate tool names (Vertex/Azure/Bedrock 400 on them) with a warning."""
    seen: set = set()
    deduped: list = []
    for tool in tools:
        name = (tool.get("function") or {}).get("name", "")
        if name and name in seen:
            logger.warning("_build_call_kwargs: duplicate tool name '%s' removed (provider=%s model=%s)", name, provider, model)
            continue
        if name:
            seen.add(name)
        deduped.append(tool)
    return deduped


class _ProfileProjection(NamedTuple):
    body: Dict[str, Any]
    reasoning_extra: Dict[str, Any]
    top_level: Dict[str, Any]
    handles_reasoning: bool
    messages_wire: bool = False


def _project_provider_profile(
    provider: str, provider_norm: str, model: str, effective_base: str, reasoning_config: Optional[dict],
) -> _ProfileProjection:
    """Provider profile's extra_body / kwargs projection; partial on failure."""
    body: Dict[str, Any] = {}
    reasoning_extra: Dict[str, Any] = {}
    top_level: Dict[str, Any] = {}
    handles_reasoning = False
    messages_wire = False
    try:
        from providers import get_provider_profile
        from providers.base import ProviderProfile
        profile = get_provider_profile(provider_norm)
        if profile is not None:
            messages_wire = profile.api_mode == "anthropic_messages"
            body = profile.build_extra_body(model=model, base_url=effective_base, reasoning_config=reasoning_config) or {}
            reasoning_extra, top_level = profile.build_api_kwargs_extras(
                reasoning_config=reasoning_config, supports_reasoning=reasoning_config is not None,
                model=model, base_url=effective_base,
            )
            reasoning_extra = reasoning_extra or {}
            top_level = top_level or {}
            handles_reasoning = (
                type(profile).build_api_kwargs_extras is not ProviderProfile.build_api_kwargs_extras
                or _contains_profile_reasoning_fields(body)
                or _contains_profile_reasoning_fields(reasoning_extra)
                or _contains_profile_reasoning_fields(top_level)
            )
    except Exception as exc:
        logger.debug("_build_call_kwargs: provider profile projection failed for %s: %s", provider, exc)
    return _ProfileProjection(body, reasoning_extra, top_level, handles_reasoning, messages_wire)


def _merge_aux_extra_body(
    extra_body: Optional[dict], projection: _ProfileProjection, reasoning_config: Optional[dict], provider_norm: str,
) -> Dict[str, Any]:
    """Caller extra_body + profile body/reasoning + generic reasoning fallback + Nous tags."""
    merged_extra = dict(extra_body or {})
    merged_extra.update(projection.body)
    merged_extra.update(projection.reasoning_extra)
    if reasoning_config and isinstance(reasoning_config, dict) and not projection.handles_reasoning:
        if reasoning_config.get("enabled") is False:
            merged_extra["reasoning"] = {"enabled": False}
        else:
            merged_extra["reasoning"] = {"enabled": True, "effort": reasoning_config.get("effort") or "medium"}
    # Portal tags + sticky session_id fallback when the profile didn't supply them; session_id
    # keeps aux calls on the main turn's upstream instance (cache warmth) — tags alone are not
    # enough on /v1/messages.
    if provider_norm in _NOUS_PROVIDER_NAMES:
        if "tags" not in merged_extra:
            merged_extra["tags"] = _aux._nous_portal_tags()
        if "session_id" not in merged_extra:
            try:
                from agent.portal_tags import get_conversation_context
                sticky_key = get_conversation_context()
            except Exception:
                sticky_key = None
            if sticky_key:
                merged_extra["session_id"] = sticky_key
    return merged_extra


def _build_call_kwargs(
    provider: str, model: str, messages: list, temperature: Optional[float] = None,
    max_tokens: Optional[int] = None, tools: Optional[list] = None, timeout: float = 30.0,
    extra_body: Optional[dict] = None, reasoning_config: Optional[dict] = None,
    base_url: Optional[str] = None, task: Optional[str] = None,
) -> dict:
    """Build kwargs for .chat.completions.create() with model/provider adjustments."""
    kwargs: Dict[str, Any] = {"model": model, "messages": messages, "timeout": timeout}
    # Per-model fixed/omitted temperature, then Opus 4.7+ sampling bans: it rejects any
    # non-default temperature/top_p/top_k, so drop silently rather than 400 when the aux model flips.
    fixed_temperature = _aux._fixed_temperature_for_model(model, base_url)
    if fixed_temperature is _aux.OMIT_TEMPERATURE:
        temperature = None  # strip — let server choose
    elif fixed_temperature is not None:
        temperature = fixed_temperature
    if temperature is not None:
        from agent.anthropic_adapter import _forbids_sampling_params
        if not _forbids_sampling_params(model):
            kwargs["temperature"] = temperature
    effective_base = base_url or (_aux._current_custom_base_url() if provider == "custom" else "")
    provider_norm = str(provider or "").strip().lower()
    if max_tokens is not None and _forwards_max_tokens(provider, provider_norm, model, effective_base, task):
        kwargs.update(_aux.auxiliary_max_tokens_param(max_tokens, model=model))  # picks max_completion_tokens where needed
    if tools:
        kwargs["tools"] = _dedupe_tool_names(tools, provider, model)
    # Provider profiles are the source of truth for reasoning wire shapes (top-level, nested body,
    # or extra_body.reasoning); providers without a reasoning-aware profile keep the generic
    # ``extra_body.reasoning`` fallback.
    projection = _project_provider_profile(provider, provider_norm, model, effective_base, reasoning_config)
    kwargs.update(projection.top_level)
    if merged_extra := _merge_aux_extra_body(extra_body, projection, reasoning_config, provider_norm):
        kwargs["extra_body"] = merged_extra
    # Anthropic Messages adapters take reasoning via a private kwarg that plain OpenAI SDK clients
    # would reject; Portal Claude is dual-wire, so include it only when the catalog id selects
    # /v1/messages. A profile declaring api_mode=anthropic_messages (commandcode-anthropic) is on
    # that wire regardless of URL shape — once it overrides build_api_kwargs_extras the generic
    # ``extra_body.reasoning`` fallback the adapter used to read is gone, so this is the adapter's
    # only path. _wrap_transport wraps such providers on the same declaration.
    if reasoning_config and isinstance(reasoning_config, dict):
        raw_base = base_url or ""
        if (
            provider_norm == "anthropic" or projection.messages_wire or _nous_on_messages_wire(provider_norm, model)
            or _aux._endpoint_speaks_anthropic_messages(raw_base) or _is_anthropic_compat_endpoint(provider_norm, raw_base)
        ):
            kwargs["_reasoning_config"] = dict(reasoning_config)
    # OpenCode relay session affinity — same key as the main turn so compression/title/vision
    # calls stay on the conversation's warm backend.
    from agent.opencode_affinity import merge_opencode_session_headers
    return merge_opencode_session_headers(kwargs, provider, base_url, _aux._runtime_main_value("session_id") or None)


def _validate_llm_response(
    response: Any, task: Optional[str] = None, provider: Optional[str] = None, base_url: Optional[str] = None,
) -> Any:
    """Validate the .choices[0].message shape (fail fast, not a downstream AttributeError).

    Also the single aux-usage accounting chokepoint: every successful non-streaming response
    passes here exactly once; *provider*/*base_url* are optional hints.

    See #7264.
    Recording is best-effort and never affects validation. *provider*/*base_url* are optional accounting
    hints — fallback-path calls omit them and the row keeps the model (read from the response itself) with
    an empty route. See #23270.
    """
    if response is None:
        raise RuntimeError(f"Auxiliary {task or 'call'}: LLM returned None response")
    from agent.aux_accounting import record_aux_usage
    record_aux_usage(response, task, provider=provider, base_url=base_url)
    # Adapter SimpleNamespace responses are fine — they have .choices[0].message.
    try:
        choices = response.choices
        if not choices or not hasattr(choices[0], "message"):
            raise AttributeError("missing choices[0].message")
    except (AttributeError, TypeError, IndexError) as exc:
        recovered = _recover_aux_response_message(response)
        if recovered is None:
            raise RuntimeError(
                f"Auxiliary {task or 'call'}: LLM returned invalid response (type={type(response).__name__}): "
                f"{str(response)[:120]!r}. Expected object with .choices[0].message — check provider "
                f"adapter or custom endpoint compatibility."
            ) from exc
        response = recovered
    # Retain the provider-reported model for terminal relay route attribution.
    context = _aux._RELAY_AUX_CALL_CONTEXT.get()
    if context is not None:
        model = _aux._field(response, "model")
        if isinstance(model, str) and model.strip():
            context["response_model"] = model
    _complete_relay_auxiliary_call()
    return response


def _complete_relay_auxiliary_call(*, outcome: str = "success") -> None:
    """Close one auxiliary logical call after acceptance or terminal failure."""
    context = _aux._RELAY_AUX_CALL_CONTEXT.get()
    if context is None:
        return
    from agent import relay_llm
    relay_llm.complete_logical_call(
        str(context.get("request_id") or ""), outcome=outcome,
        model_name=str(context.get("model") or "unknown"),
        provider_name=str(context.get("provider") or "auxiliary"),
        response_model_name=context.get("response_model"),
    )


def _fail_relay_auxiliary_call() -> None:
    """Close a terminally failed call without replacing its original error."""
    try:
        _complete_relay_auxiliary_call(outcome="failed")
    except Exception:
        logger.warning("Relay auxiliary failure finalization failed", exc_info=True)


def _recover_aux_response_message(response: Any) -> Optional[Any]:
    """Synthesize chat-completions shape from Responses-style text (``output_text``,
    ``output`` items) that some compatible endpoints return outside ``choices``."""
    text = _extract_aux_response_text(response)
    if not text:
        return None
    choice = SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=getattr(response, "finish_reason", None) or "stop")
    try:
        response.choices = [choice]
        return response
    except Exception:
        return SimpleNamespace(
            id=getattr(response, "id", ""), model=getattr(response, "model", ""),
            object=getattr(response, "object", "chat.completion"), choices=[choice],
            usage=getattr(response, "usage", None),
        )


def _extract_aux_response_text(response: Any) -> str:
    """Text from Responses-style ``output_text`` or ``output[].content[].text``."""
    output_text = _aux._field(response, "output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    output = _aux._field(response, "output")
    if not isinstance(output, list):
        return ""
    parts: List[str] = []
    for item in output:
        item_type = _aux._field(item, "type")
        if item_type and item_type != "message":
            continue
        for part in (_aux._field(item, "content") or []):
            if _aux._field(part, "type") in {"output_text", "text", None}:
                text = _aux._field(part, "text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n".join(parts).strip()


# Streamed aggregation for progress-hooked aux calls: ``timeout`` becomes an inter-chunk idle
# timeout (httpx read timeout is per read), each chunk ticks outer watchdogs; the total ceiling
# bounds trickles.
_AUX_STREAM_CEILING_FLOOR_SECONDS = 600.0
_AUX_STREAM_CEILING_MULTIPLIER = 4.0


def _aux_stream_total_ceiling(effective_timeout: Optional[float]) -> float:
    """Absolute wall-clock bound for a streamed aux call; generous by design (the idle
    timeout is the real guard — this only stops a one-token-per-idle-window trickle)."""
    try:
        timeout = float(effective_timeout) if effective_timeout is not None else 0.0
    except (TypeError, ValueError):
        timeout = 0.0
    return max(_AUX_STREAM_CEILING_FLOOR_SECONDS, _AUX_STREAM_CEILING_MULTIPLIER * timeout)


def _client_streams_internally(client: Any) -> bool:
    """Adapters that stream inside .create() tick the hook themselves (Codex, Anthropic) or
    cannot stream (Bedrock); none accept ``stream=True`` from us."""
    return isinstance(client, (_aux.CodexAuxiliaryClient, _aux.AnthropicAuxiliaryClient, _aux.BedrockAuxiliaryClient))


def _is_managed_local_endpoint(base_url: Optional[str]) -> bool:
    """True when *base_url* targets the llama-server this Hermes manages."""
    if not base_url:
        return False
    managed = _aux._managed_local_netloc()
    if not managed:
        return False
    try:
        return urlparse(str(base_url)).netloc.lower() == managed
    except Exception:
        return False


def _provider_requires_stream(provider: str, base_url: Optional[str]) -> bool:
    """Providers that only accept streaming (non-stream = 400): Tencent Copilot, any
    ``auxiliary.stream_only_base_urls`` substring, and the managed local llama-server
    (streamed for cancellation — it only notices a dead client on socket write)."""
    _url = str(base_url or "").lower()
    if not _url:
        return False
    if _aux.base_url_host_matches(_url, "copilot.tencent.com") or _is_managed_local_endpoint(_url):
        return True
    try:
        from hermes_cli.config import load_config
        markers = (load_config() or {}).get("auxiliary", {}).get("stream_only_base_urls") or []
        if isinstance(markers, (list, tuple)):
            return any(
                isinstance(marker, str) and marker.strip() and marker.strip().lower() in _url
                for marker in markers)
    except Exception:
        pass  # Config read is best-effort; never break an aux call over it.
    return False


_AFFORDABLE_TOKENS_RE = re.compile(r"can only afford\s+([0-9][0-9,]*)", re.IGNORECASE)
# Below the floor the affordable budget can't fit a useful aux output — treat as exhaustion;
# the margin keeps provider-side token-count rounding from 402-ing the retry.
_AFFORDABLE_RETRY_FLOOR_TOKENS = 512
# See #49785.
_AFFORDABLE_RETRY_MARGIN_TOKENS = 64


def _affordable_max_tokens_from_error(exc: Exception) -> Optional[int]:
    """Affordable output budget (minus margin) from an OpenRouter credit-limited 402
    ("...but can only afford 7117": credit exists, the cap was too large); ``None``
    when no count is present or the budget is too small to be useful."""
    if not _aux._is_payment_error(exc):
        return None
    match = _AFFORDABLE_TOKENS_RE.search(str(exc))
    if not match:
        return None
    try:
        affordable = int(match.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None
    capped = affordable - _AFFORDABLE_RETRY_MARGIN_TOKENS
    return capped if capped >= _AFFORDABLE_RETRY_FLOOR_TOKENS else None


def _create_with_progress(
    client: Any, kwargs: Dict[str, Any], task: Optional[str] = None, *, force_stream: bool = False
) -> Any:
    """Credit-aware :func:`_create_with_progress_once`: a 402 naming an affordable
    budget retries ONCE with that cap (only ever lowering); anything else re-raises."""
    try:
        return _create_with_progress_once(client, kwargs, task, force_stream=force_stream)
    except Exception as exc:
        affordable = _affordable_max_tokens_from_error(exc)
        if affordable is None:
            raise
        existing_cap = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens")
        if isinstance(existing_cap, (int, float)) and 0 < existing_cap <= affordable:
            raise  # Already within budget — the error is something else; don't spin.
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("max_tokens", None)
        retry_kwargs.pop("max_completion_tokens", None)
        retry_kwargs.update(
            _aux.auxiliary_max_tokens_param(affordable, model=str(kwargs.get("model") or "") or None))
        logger.info("Auxiliary %s: credit-limited 402 (affordable=%d tokens); "
                    "retrying once with a clamped output cap instead of failing: %s",
                    task or "call", affordable, exc)
        return _create_with_progress_once(client, retry_kwargs, task, force_stream=force_stream)


def _stream_request_plan(kwargs: Dict[str, Any]) -> "Tuple[Dict[str, Any], str, float]":
    """(stream kwargs, model name, total ceiling) for a streamed re-aggregation."""
    stream_kwargs = dict(kwargs)
    stream_kwargs["stream"] = True
    stream_kwargs["stream_options"] = {"include_usage": True}
    return (stream_kwargs, str(kwargs.get("model") or ""),
            _aux._aux_stream_total_ceiling(kwargs.get("timeout")))


def _create_with_progress_once(
    client: Any, kwargs: Dict[str, Any], task: Optional[str] = None, *, force_stream: bool = False
) -> Any:
    """create() that streams (and re-aggregates, ticking the hook per substantive chunk) when a
    progress hook is active or the provider is stream-only; plain ``create(**kwargs)`` otherwise
    or when the adapter streams internally. Streaming rejections fall back to a plain call —
    except under ``force_stream``.

    Behavior is byte-for-byte identical to a plain ``create(**kwargs)`` when neither trigger applies (every
    existing caller/task) or when the client's wire adapter streams internally. With a hook + a
    chunk-capable client, the request is sent with ``stream=True`` and aggregated, ticking the hook only for
    substantive chunks. The configured ``timeout`` acts per stream read (idle) rather than as a total
    budget, and outer liveness watchdogs see tokens moving. ``force_stream=True`` (stream-only providers
    such as Tencent Copilot — credit @kudi88, PR #60686) takes the same streamed path even without a hook.
    Providers that reject the streamed request fall back to the plain non-streaming call — except under
    ``force_stream``, where a stream-only provider rejects the plain call by definition, so the original
    error is surfaced to the normal recovery chains instead.
    """
    _aux._notify_aux_dispatch()
    _aux._notify_aux_progress()  # Preserve the watchdog's historical dispatch tick.
    if (not _aux._aux_progress_active() and not force_stream) or _client_streams_internally(client):
        response = client.chat.completions.create(**kwargs)
        if not _client_streams_internally(client):
            _aux._notify_aux_provider_response()
        return response
    stream_kwargs, model, total_ceiling = _stream_request_plan(kwargs)
    try:
        chunks = client.chat.completions.create(**stream_kwargs)
    except Exception as exc:
        # Genuine provider failures aren't streaming's fault — surface unchanged so the
        # recovery chains see the same error as a plain call.
        if (force_stream or _aux._is_transient_transport_error(exc) or _aux._is_auth_error(exc)
                or _aux._is_payment_error(exc) or _aux._is_rate_limit_error(exc)):
            raise
        # Possibly a streaming-specific rejection: retry non-streaming once; a genuinely bad
        # request reproduces the real error for the except-chains.
        logger.debug("Auxiliary %s: streamed request failed (%s); retrying non-streaming",
                     task or "call", exc)
        _aux._notify_aux_dispatch()
        response = client.chat.completions.create(**kwargs)
        _aux._notify_aux_provider_response()
        return response
    # Some shims (MoA quiet mode, defensive adapters) return a complete response despite
    # stream=True; it counts as provider response + forward progress.
    if hasattr(chunks, "choices"):
        _aux._notify_aux_provider_response()
        return chunks
    return _aggregate_chat_stream(chunks, model=model, total_ceiling=total_ceiling)


def _close_chunk_stream(chunks: Any, *, allow_aclose: bool = False) -> Any:
    """Best-effort ``close()`` (or ``aclose()``); returns a pending awaitable or None."""
    close_fn = getattr(chunks, "close", None) or (
        getattr(chunks, "aclose", None) if allow_aclose else None)
    if not callable(close_fn):
        return None
    try:
        result = close_fn()
    except Exception:
        return None
    return result if inspect.isawaitable(result) else None


def _aggregate_chat_stream(
    chunks: Any, *, model: str = "", total_ceiling: Optional[float] = None
) -> Any:
    """Consume a chunk stream into a complete response; TimeoutError (phrased "timed out" so
    ``_is_timeout_error`` matches) when *total_ceiling* elapses."""
    acc = _ChatStreamAccumulator(
        model=model, total_ceiling=total_ceiling, host_deadline=_aux._current_aux_stream_deadline())
    try:
        for chunk in chunks:
            acc.feed(chunk)
    finally:
        _close_chunk_stream(chunks)
    return acc.finish()


# Reasoning-detail fields whose non-empty text counts as forward progress.
_REASONING_DETAIL_TEXT_FIELDS = ("summary", "thinking", "content", "text")


class _ChatStreamAccumulator:
    """Shared per-chunk accumulation so sync and async aggregation cannot drift."""

    def __init__(self, model: str = "", total_ceiling: Optional[float] = None,
                 host_deadline: Optional[float] = None):
        self._started = time.monotonic()
        self._total_ceiling = total_ceiling
        # Absolute instant the waiting host gives up; checked alongside (not instead of) the
        # ceiling, and unaffected by pre-construction dispatch/TTFT.
        # Checked as well as (not instead of) the ceiling above: the ceiling still bounds callers with no
        # host deadline, and the host deadline is absolute, so it is unaffected by however long dispatch and
        # TTFT took before this accumulator was constructed. See #99692.
        self._host_deadline = host_deadline
        self.content_parts: List[str] = []
        self.reasoning_parts: List[str] = []
        self.reasoning_details: List[Any] = []
        self.tool_calls_acc: Dict[int, Dict[str, Any]] = {}
        self.finish_reason = self.usage = None
        self.resp_id = ""
        self.resp_model = model or ""

    def _check_deadlines(self) -> None:
        """Raise TimeoutError past the total ceiling or the host deadline."""
        now = time.monotonic()
        if self._total_ceiling is not None and (now - self._started) >= self._total_ceiling:
            raise TimeoutError(f"Auxiliary streamed call timed out after {self._total_ceiling:.0f}s "
                               "total ceiling (stream still open but over budget)")
        if self._host_deadline is not None and now >= self._host_deadline:
            raise TimeoutError("Auxiliary streamed call timed out at the host compression "
                               f"deadline after {time.monotonic() - self._started:.0f}s "
                               "(the caller already stopped waiting; streaming on would only "
                               "pin its session lease)")

    def _feed_reasoning_details(self, delta: Any) -> bool:
        """Collect ``reasoning_details`` (OpenRouter-style thinking); True only when a detail
        carries text, so structural/signed envelopes can't keep a stall alive."""
        reasoning_details = getattr(delta, "reasoning_details", None)
        if reasoning_details is None:
            model_extra = getattr(delta, "model_extra", None)
            if isinstance(model_extra, dict):
                reasoning_details = model_extra.get("reasoning_details")
        if not isinstance(reasoning_details, list):
            return False
        made_progress = False
        for detail in reasoning_details:
            self.reasoning_details.append(detail)
            if isinstance(detail, dict) and any(
                isinstance(detail.get(f), str) and detail[f] for f in _REASONING_DETAIL_TEXT_FIELDS):
                made_progress = True
        return made_progress

    def _feed_tool_calls(self, delta: Any) -> bool:
        """Merge tool-call fragments by index; True when any fragment carried data."""
        made_progress = False
        for tc in (getattr(delta, "tool_calls", None) or []):
            idx = getattr(tc, "index", 0) or 0
            acc = self.tool_calls_acc.setdefault(idx, {"id": "", "name": "", "arguments": []})
            if getattr(tc, "id", None):
                acc["id"] = tc.id
                made_progress = True
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    acc["name"] = fn.name
                    made_progress = True
                if getattr(fn, "arguments", None):
                    acc["arguments"].append(fn.arguments)
                    made_progress = True
        return made_progress

    def feed(self, chunk: Any) -> None:
        # Every frame records transport timing (TTFP); only a substantive payload ticks the
        # forward-progress hook that keeps compression alive.
        _aux._notify_aux_timing_response()
        self._check_deadlines()
        self.resp_id = getattr(chunk, "id", None) or self.resp_id
        self.resp_model = getattr(chunk, "model", None) or self.resp_model
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage:
            self.usage = chunk_usage
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return
        choice = choices[0]
        self.finish_reason = getattr(choice, "finish_reason", None) or self.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            return
        made_progress = False
        from agent.message_content import flatten_message_text

        piece = flatten_message_text(getattr(delta, "content", None), sep="")
        if piece:
            self.content_parts.append(piece)
            made_progress = True
        reasoning_piece = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
        reasoning_piece = flatten_message_text(reasoning_piece, sep="")
        if reasoning_piece:
            self.reasoning_parts.append(reasoning_piece)
            made_progress = True
        # Evaluate both unconditionally: they accumulate state, not just progress.
        made_progress |= self._feed_reasoning_details(delta)
        made_progress |= self._feed_tool_calls(delta)
        if made_progress:
            _aux._notify_aux_progress()

    def finish(self) -> Any:
        tool_calls = None
        if self.tool_calls_acc:
            tool_calls = [
                SimpleNamespace(id=acc["id"], type="function", function=SimpleNamespace(
                    name=acc["name"], arguments="".join(acc["arguments"])))
                for _idx, acc in sorted(self.tool_calls_acc.items())]
        message = SimpleNamespace(
            role="assistant", content="".join(self.content_parts), tool_calls=tool_calls,
            reasoning="".join(self.reasoning_parts) or None,
            reasoning_details=self.reasoning_details or None,
        )
        choice = SimpleNamespace(index=0, message=message, finish_reason=self.finish_reason or "stop")
        return SimpleNamespace(id=self.resp_id, model=self.resp_model, object="chat.completion",
                               choices=[choice], usage=self.usage)


async def _aggregate_chat_stream_async(
    chunks: Any, *, model: str = "", total_ceiling: Optional[float] = None
) -> Any:
    """Async mirror of :func:`_aggregate_chat_stream` (AsyncOpenAI streams need ``async for``)."""
    acc = _ChatStreamAccumulator(
        model=model, total_ceiling=total_ceiling, host_deadline=_aux._current_aux_stream_deadline())
    try:
        async for chunk in chunks:
            acc.feed(chunk)
    finally:
        pending = _close_chunk_stream(chunks, allow_aclose=True)
        if pending is not None:
            with contextlib.suppress(Exception):
                await pending
    return acc.finish()


async def _acreate_with_stream(client: Any, kwargs: Dict[str, Any], task: Optional[str] = None) -> Any:
    """Async create() for stream-only providers: ``stream=True`` + aggregate the async chunks."""
    stream_kwargs, model, total_ceiling = _stream_request_plan(kwargs)
    chunks = await client.chat.completions.create(**stream_kwargs)
    if hasattr(chunks, "choices"):  # shims may hand back a complete response despite stream=True
        return chunks
    return await _aggregate_chat_stream_async(chunks, model=model, total_ceiling=total_ceiling)


def _async_client_streams_internally(client: Any) -> bool:
    """Async twin of :func:`_client_streams_internally` (the async adapters are separate classes)."""
    return isinstance(client, (_aux.AsyncCodexAuxiliaryClient, _aux.AsyncAnthropicAuxiliaryClient, _aux.AsyncBedrockAuxiliaryClient))


async def _acreate_with_progress(
    client: Any, kwargs: Dict[str, Any], task: Optional[str] = None, *, force_stream: bool = False
) -> Any:
    """Async :func:`_create_with_progress`: stream + re-aggregate (ticking the hook per substantive
    chunk) when a progress hook is active or the provider is stream-only; plain create otherwise."""
    _aux._notify_aux_dispatch()
    _aux._notify_aux_progress()
    if (not _aux._aux_progress_active() and not force_stream) or _async_client_streams_internally(client):
        response = await client.chat.completions.create(**kwargs)
        if not _async_client_streams_internally(client):
            _aux._notify_aux_provider_response()
        return response
    stream_kwargs, model, total_ceiling = _stream_request_plan(kwargs)
    try:
        chunks = await client.chat.completions.create(**stream_kwargs)
    except Exception as exc:
        # Only a rejected stream NEGOTIATION falls back to a plain call (mirrors the sync wrapper); a
        # failure mid-consumption below reaches the classified recovery ladder instead of silently
        # re-sending the whole prompt non-streaming.
        if (force_stream or _aux._is_transient_transport_error(exc) or _aux._is_auth_error(exc)
                or _aux._is_payment_error(exc) or _aux._is_rate_limit_error(exc)):
            raise
        logger.debug("Auxiliary %s: streamed async request failed (%s); retrying non-streaming",
                     task or "call", exc)
        _aux._notify_aux_dispatch()
        response = await client.chat.completions.create(**kwargs)
        _aux._notify_aux_provider_response()
        return response
    if hasattr(chunks, "choices"):  # shims may hand back a complete response despite stream=True
        _aux._notify_aux_provider_response()
        return chunks
    return await _aggregate_chat_stream_async(chunks, model=model, total_ceiling=total_ceiling)


# Shared request head + recovery ladder for call_llm / async_call_llm: the entry points differ
# only in how a request is awaited, so route resolution and the ordered recovery ladder are
# written once. The ladder is a generator yielding ``_LadderStep`` requests and receiving the
# response (or thrown exception), so rung ORDER and accept/re-raise contracts match on both wires.
_ResolvedAuxRoute = NamedTuple("_ResolvedAuxRoute", [
    ("client", Any), ("final_model", Optional[str]), ("resolved_provider", str),
    ("effective_provider", str)])


def _resolve_call_client(
    task: Optional[str], *, provider: Optional[str], model: Optional[str], base_url: Optional[str],
    api_key: Optional[str], resolved_provider: str, resolved_model: Optional[str],
    resolved_base_url: Optional[str], resolved_api_key: Optional[str],
    resolved_api_mode: Optional[str], main_runtime: Optional[Dict[str, Any]], async_mode: bool,
) -> _ResolvedAuxRoute:
    """Resolve the client for one aux call: vision chain, or cached text client with the
    explicit-provider fallback_chain / auto-chain rescue; RuntimeError when nothing is configured."""
    effective_provider = resolved_provider
    if task == "vision":
        effective_provider, client, final_model = _aux.resolve_vision_provider_client(
            provider=resolved_provider if resolved_provider != "auto" else provider,
            model=resolved_model or model, base_url=resolved_base_url or base_url,
            api_key=resolved_api_key or api_key, async_mode=async_mode, main_runtime=main_runtime,
        )
        if client is None and resolved_provider != "auto" and not resolved_base_url:
            logger.warning("Vision provider %s unavailable, falling back to auto vision backends",
                           resolved_provider)
            effective_provider, client, final_model = _aux.resolve_vision_provider_client(
                provider="auto", model=resolved_model, async_mode=async_mode,
                main_runtime=main_runtime)
        if client is not None:
            resolved_provider = effective_provider or resolved_provider
    else:
        client, final_model = _aux._get_cached_client(
            resolved_provider, resolved_model, async_mode=async_mode, base_url=resolved_base_url,
            api_key=resolved_api_key, api_mode=resolved_api_mode, main_runtime=main_runtime,
            task=task)
        effective_provider = _aux._effective_provider_for_client(client, resolved_provider)
        if client is None:
            # Explicit provider with no credentials: honor the task fallback_chain before
            # raising (fallback entries may use OAuth / credential-pool auth).
            _explicit = (resolved_provider or "").strip().lower()
            if _explicit and _explicit not in {"auto", "openrouter", "custom"}:
                fb_client, fb_model, fb_label = _aux._try_configured_fallback_for_unavailable_client(
                    task, _explicit)
                if fb_client is None:
                    raise RuntimeError(
                        f"Provider '{_explicit}' is set in config.yaml but no API key was found. "
                        f"Set the {_explicit.upper()}_API_KEY environment variable, or switch to "
                        f"a different provider with `hermes model`.")
                client, final_model = fb_client, fb_model
                if async_mode:
                    client, final_model = _aux._to_async_client(
                        fb_client, fb_model or "", is_vision=(task == "vision"))
                resolved_provider = fb_label or resolved_provider
                effective_provider = resolved_provider
            # Auto/custom with no credentials: walk the full auto chain (not just OpenRouter).
            # model=None so each provider uses its own default.
            if client is None and not resolved_base_url:
                logger.info("Auxiliary %s: provider %s unavailable, trying auto-detection chain",
                            task or "call", resolved_provider)
                client, final_model = _aux._get_cached_client(
                    "auto", async_mode=async_mode, main_runtime=main_runtime, task=task)
                effective_provider = _aux._effective_provider_for_client(client, "auto")
    if client is None:
        raise RuntimeError(f"No LLM provider configured for task={task} "
                           f"provider={resolved_provider}. Run: hermes setup")
    return _ResolvedAuxRoute(client, final_model, resolved_provider, effective_provider)


_PreparedAuxRequest = NamedTuple("_PreparedAuxRequest", [
    ("client", Any), ("final_model", Optional[str]), ("kwargs", Dict[str, Any]),
    ("resolved_provider", str), ("request_provider", str), ("resolved_model", Optional[str]),
    ("resolved_base_url", Optional[str]), ("resolved_api_key", Optional[str]),
    ("resolved_api_mode", Optional[str]), ("effective_timeout", float),
    ("effective_extra_body", Dict[str, Any]), ("base_info", str)])


def _prepare_aux_request(
    task: Optional[str], *, provider: Optional[str], model: Optional[str], base_url: Optional[str],
    api_key: Optional[str], main_runtime: Dict[str, Any], messages: list,
    temperature: Optional[float], max_tokens: Optional[int], tools: Optional[list],
    timeout: Optional[float], extra_body: Optional[dict], reasoning_config: Optional[dict],
    extra_headers: Optional[Dict[str, str]], api_mode: Optional[str],
    route_info: Optional[Dict[str, str]], async_mode: bool,
) -> _PreparedAuxRequest:
    """Shared head of call_llm/async_call_llm: resolve route + client, publish it, build request kwargs.
    Sync-only: compression fast lane, per-request ``extra_headers``, and ``base_info`` falling
    back to the resolved base_url when the client exposes none."""
    resolved_provider, resolved_model, resolved_base_url, resolved_api_key, resolved_api_mode = _aux._resolve_task_provider_model(
        task, provider, model, base_url, api_key)
    if api_mode:
        resolved_api_mode = api_mode
    effective_extra_body = _aux._get_task_extra_body(task)
    effective_extra_body.update(extra_body or {})
    client, final_model, resolved_provider, effective_provider = _resolve_call_client(
        task, provider=provider, model=model, base_url=base_url, api_key=api_key,
        resolved_provider=resolved_provider, resolved_model=resolved_model,
        resolved_base_url=resolved_base_url, resolved_api_key=resolved_api_key,
        resolved_api_mode=resolved_api_mode, main_runtime=main_runtime, async_mode=async_mode,
    )
    effective_timeout = _aux._effective_aux_timeout(task, timeout)
    request_provider = effective_provider or resolved_provider
    if not async_mode:
        compression_config = _aux._get_auxiliary_task_config("compression") if task == "compression" else {}
        _, effective_extra_body = _aux._compression_fast_lane_controls(
            task, actual_provider=request_provider, actual_model=final_model,
            requested_provider=provider, requested_model=model, route_config=compression_config,
            leak_guard_config=compression_config, max_tokens=max_tokens,
            extra_body=effective_extra_body,
        )
    _aux._set_relay_auxiliary_route(request_provider, final_model, resolved_api_mode)
    _aux._record_route_info(route_info, _aux._fallback_provider_from_label(request_provider), final_model)
    if async_mode:
        base_info = str(getattr(client, "base_url", "") or "")
    else:
        base_info = str(getattr(client, "base_url", resolved_base_url) or "")
        if task:
            logger.info("Auxiliary %s: using %s (%s)%s",
                         task, request_provider or "auto", final_model or "default",
                         f" at {base_info}" if base_info and "openrouter" not in base_info else "")
    # Client's actual base_url so endpoint-specific temperature overrides work on
    # auto-detected routes (api.moonshot.ai vs api.kimi.com/coding).
    kwargs = _aux._build_call_kwargs(
        request_provider, final_model, messages, temperature=temperature, max_tokens=max_tokens,
        tools=tools, timeout=effective_timeout, extra_body=effective_extra_body,
        reasoning_config=reasoning_config, base_url=base_info or resolved_base_url, task=task)
    if extra_headers:
        kwargs["extra_headers"] = dict(extra_headers)
    # Convert image blocks for Anthropic-compatible endpoints (e.g. MiniMax)
    client_base = str(getattr(client, "base_url", "") or "")
    if _is_anthropic_compat_endpoint(request_provider, client_base):
        kwargs["messages"] = _convert_openai_images_to_anthropic(kwargs["messages"])
    return _PreparedAuxRequest(
        client, final_model, kwargs, resolved_provider, request_provider, resolved_model,
        resolved_base_url, resolved_api_key, resolved_api_mode, effective_timeout,
        effective_extra_body, base_info)


def _plan_aux_call(
    task: Optional[str], *, async_mode: bool, provider: Optional[str], model: Optional[str],
    base_url: Optional[str], api_key: Optional[str], main_runtime: Optional[Dict[str, Any]],
    messages: list, temperature: Optional[float], max_tokens: Optional[int], tools: Optional[list],
    timeout: Optional[float], extra_body: Optional[dict], reasoning_config: Optional[dict],
    extra_headers: Optional[Dict[str, str]], api_mode: Optional[str],
    route_info: Optional[Dict[str, str]],
) -> Tuple[_PreparedAuxRequest, Dict[str, Any], Dict[str, Any]]:
    """Shared head of both call impls: prepare the request and bundle the kwargs the recovery
    drivers pass to ``_retry_same_provider_*`` / ``_call_fallback_candidate_*``. One immutable
    runtime snapshot for keying/resolution/retries/fallbacks, so a concurrent /model switch
    can't mix key and client from different runtimes."""
    main_runtime = _aux._normalize_main_runtime(main_runtime)
    req = _prepare_aux_request(
        task, provider=provider, model=model, base_url=base_url, api_key=api_key,
        main_runtime=main_runtime, messages=messages, temperature=temperature,
        max_tokens=max_tokens, tools=tools, timeout=timeout, extra_body=extra_body,
        reasoning_config=reasoning_config, extra_headers=extra_headers,
        api_mode=api_mode, route_info=route_info, async_mode=async_mode,
    )
    candidate_kwargs = dict(
        task=task, messages=messages, temperature=temperature, max_tokens=max_tokens,
        tools=tools, effective_timeout=req.effective_timeout,
        effective_extra_body=req.effective_extra_body, reasoning_config=reasoning_config,
    )
    retry_kwargs = dict(
        candidate_kwargs, resolved_base_url=req.resolved_base_url,
        resolved_api_key=req.resolved_api_key, resolved_api_mode=req.resolved_api_mode,
        main_runtime=main_runtime, final_model=req.final_model, extra_headers=extra_headers,
    )
    return req, retry_kwargs, candidate_kwargs


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
