"""Shared auxiliary client router for side tasks (compression, search, vision, ...).

Text auto chain: main provider+model → OpenRouter → Nous Portal → custom endpoint →
native Anthropic → direct API-key providers → None. Vision auto chain: main
provider (if a supported vision backend) → OpenRouter → Nous → Anthropic → custom.
``auxiliary.free_only`` restricts the OpenRouter lane to ``:free`` SKUs. Codex OAuth is
in neither chain (undocumented, shifting allow-list): main provider or explicit
``auxiliary.<task>.provider`` only. HTTP 402 in call_llm() falls through the chain.

This module keeps the entry points, call-scoped context and every helper that rebinds module
state; the rest lives in ``agent.auxiliary_*`` siblings (catalog, adapters, providers, errors,
client_cache, fallback, routing, task_config, request, ladder), re-exported at the bottom of
this file. They reach this module late-bound via ``_aux``.
"""

import contextlib
import contextvars
import functools
import hashlib
import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple, TYPE_CHECKING
from urllib.parse import urlparse, parse_qs, urlunparse

from agent.codex_headers import (
    CODEX_AUX_BASE_URL as _CODEX_AUX_BASE_URL,
    apply_required_codex_headers as _apply_required_codex_headers,
    codex_cloudflare_headers as _codex_cloudflare_headers,
    is_official_codex_base_url as _is_official_codex_base_url,
)
from agent.codex_runtime import _codex_event_has_content

# `openai.OpenAI` is imported lazily (~240 ms cold); `OpenAI` below is a proxy
# so in-module calls, `auxiliary_client.OpenAI` reads and
# `patch("agent.auxiliary_client.OpenAI")` all keep working.
if TYPE_CHECKING:
    from openai import OpenAI  # noqa: F401 — type hints only

_OPENAI_CLS_CACHE: Optional[type] = None


def _load_openai_cls() -> type:
    """Import and cache ``openai.OpenAI``."""
    global _OPENAI_CLS_CACHE
    if _OPENAI_CLS_CACHE is None:
        from openai import OpenAI as _cls
        _OPENAI_CLS_CACHE = _cls
    return _OPENAI_CLS_CACHE


class _OpenAIProxy:
    """Lazy stand-in for ``openai.OpenAI``: forwards calls and isinstance checks, importing on first use."""
    __slots__ = ()

    def __call__(self, *args, **kwargs):
        return _load_openai_cls()(*args, **kwargs)

    def __instancecheck__(self, obj):
        return isinstance(obj, _load_openai_cls())

    def __repr__(self):
        return "<lazy openai.OpenAI proxy>"


OpenAI = _OpenAIProxy()


# Availability probe mode: check_fns only need to know whether a client is RESOLVABLE, so
# inside `aux_probe_mode()` constructors return a stub instead of importing openai + building
# httpx/SSL (~0.3s on CLI startup). Stubs are never cached (see _store_cached_client).
_aux_probe_state = threading.local()


class _AuxProbeClientStub:
    """Non-functional placeholder returned while `aux_probe_mode` is active."""
    __slots__ = ("api_key", "base_url")

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = base_url

    def __getattr__(self, name: str) -> Any:
        # Loud failure if a probe stub ever leaks into a runtime call path.
        raise RuntimeError(
            f"_AuxProbeClientStub used as a real client (attribute {name!r}); "
            "aux_probe_mode is for availability checks only")

    def __repr__(self) -> str:
        return "<aux availability-probe client stub>"


def _aux_probe_active() -> bool:
    return bool(getattr(_aux_probe_state, "active", False))


@contextlib.contextmanager
def aux_probe_mode():
    """Resolve provider availability without constructing real SDK clients."""
    prev = getattr(_aux_probe_state, "active", False)
    _aux_probe_state.active = True
    try:
        yield
    finally:
        _aux_probe_state.active = prev


from agent.credential_pool import load_pool
from agent.model_metadata import MINIMUM_CONTEXT_LENGTH, get_model_context_length
from hermes_cli.config import get_hermes_home
from agent.auxiliary_health import _custom_health_base_url, _unhealthy_cache_key
from hermes_constants import OPENROUTER_BASE_URL, hermes_home_key
from utils import base_url_host_matches, base_url_hostname, base_url_origin, env_float, is_truthy_value, model_forces_max_completion_tokens, normalize_proxy_env_vars

logger = logging.getLogger(__name__)


# resolve_provider_client fall-through dedup: misconfigured-provider warnings fire on every
# retry, so only the first per process surfaces. Separate sets let tests clear each branch.
_LOGGED_UNKNOWN_PROVIDER_KEYS: set = set()
_LOGGED_UNHANDLED_AUTHTYPE_KEYS: set = set()
_LOGGED_UNSUPPORTED_EXTPROC_KEYS: set = set()
_LOGGED_UNSUPPORTED_OAUTH_KEYS: set = set()


def _resolve_aux_verify(base_url: Optional[str]) -> Any:
    """httpx ``verify`` for an aux base_url, mirroring the main client (per-provider ``ssl_ca_cert`` /
    ``ssl_verify``, ``HERMES_CA_BUNDLE`` / ``SSL_CERT_FILE``); any failure → httpx default (``True``)."""
    try:
        from agent.ssl_verify import resolve_httpx_verify
        from hermes_cli.config import get_custom_provider_tls_settings, load_config_readonly
        tls = get_custom_provider_tls_settings(str(base_url or ""), config=load_config_readonly())
        return resolve_httpx_verify(
            ca_bundle=tls.get("ssl_ca_cert"), ssl_verify=tls.get("ssl_verify"), base_url=str(base_url or ""))
    except Exception:
        return True


_WARNED_KEEPALIVE_IMPORT_SKEW = False


def _openai_http_client_kwargs(base_url: Optional[str], *, async_mode: bool = False) -> Dict[str, Any]:
    """Inject keepalive httpx client with env-only proxy (not macOS system proxy)."""
    try:
        from agent.process_bootstrap import build_keepalive_http_client
        client = build_keepalive_http_client(
            str(base_url or ""), async_mode=async_mode, verify=_resolve_aux_verify(base_url))
    except (ImportError, AttributeError):
        # Version-skewed install (Desktop runtime lagging a git tree) lacks this helper:
        # degrade to the SDK default httpx client rather than kill the job; warn once.
        global _WARNED_KEEPALIVE_IMPORT_SKEW
        if not _WARNED_KEEPALIVE_IMPORT_SKEW:
            _WARNED_KEEPALIVE_IMPORT_SKEW = True
            logger.warning(
                "agent.process_bootstrap.build_keepalive_http_client is "
                "unavailable — mixed/stale install detected (#64333). Falling "
                "back to the SDK default HTTP client. Run `hermes update` (or "
                "reinstall the Desktop app) to resync the runtime.")
        client = None
    return {"http_client": client} if client is not None else {}


def _create_openai_client(*, api_key: str, base_url: str, **kwargs: Any) -> Any:
    if _aux_probe_active():
        # Availability probe: resolved credentials/base_url are the answer.
        return _AuxProbeClientStub(api_key=api_key, base_url=base_url)
    kwargs = {**_openai_http_client_kwargs(base_url), **kwargs}
    # OpenCode Zen free tier: the keyless placeholder must never hit the wire (relay 401s any
    # unrecognized bearer) — blank the Authorization header.
    with contextlib.suppress(Exception):
        from hermes_cli.models import OPENCODE_ZEN_FREE_KEYLESS_PLACEHOLDER, opencode_zen_free_headers
        if api_key == OPENCODE_ZEN_FREE_KEYLESS_PLACEHOLDER:
            kwargs["default_headers"] = {**(kwargs.get("default_headers") or {}), **opencode_zen_free_headers()}
    _apply_required_codex_headers(kwargs, access_token=api_key, base_url=base_url)
    # Hermes owns aux retry/fallback policy; the SDK default (max_retries=2) would triple
    # wall time on a hung endpoint before Hermes sees one failure.
    # Hermes owns auxiliary retry + provider/model fallback policy (the same-provider transient retry in
    # call_llm plus the except-chain fallback). The OpenAI SDK's own default (max_retries=2 → up to 3
    # attempts) silently multiplies the effective wall time of every aux call by 3× on a slow/hung endpoint,
    # so a 120s timeout can stall ~360s before Hermes sees a single failure (issue #54465). Disable
    # SDK-internal retries by default and let Hermes control the budget; explicit callers can still override
    # via kwargs.
    kwargs.setdefault("max_retries", 0)
    return OpenAI(api_key=api_key, base_url=base_url, **kwargs)


# Interrupt protection for atomic aux tasks: a compression summary killed by an ordinary
# gateway interrupt degrades to a static marker, so a thread-local flag marks such calls
# protected. Explicit host cancel (Ctrl+C, /stop) still overrides it, timeouts still fire.
# ── Interrupt protection for atomic auxiliary tasks ────────────────────── Some auxiliary tasks must NOT be
# aborted mid-flight by a gateway interrupt (e.g. an incoming user message while the agent is busy). Context
# compression is the prime case: if the summary LLM call is interrupted part-way, compression falls back to
# a static "summary unavailable" marker and the real handoff is lost (#23975). A thread-local flag lets such
# a task mark its in-flight LLM call as interrupt-protected; the Codex Responses stream's cancellation check
# honors it. TIMEOUTS still fire (a hung call must die), and all OTHER aux tasks (vision, web_extract,
# title_generation, …) remain freely interruptible.
_aux_interrupt_protection = threading.local()


class AuxiliaryExplicitCancellation(BaseException):
    """Frozen signal that an auxiliary attempt was explicitly hard-cancelled. ``BaseException`` so broad
    ``except Exception`` retry/fallback code never treats a host stop as a transport failure; ``cause``
    is immutable class data so nothing re-queries a mutable host Event after the transport unwound."""
    cause = "explicit_host_cancel"

    def __init__(self) -> None:
        super().__init__("auxiliary request explicitly cancelled by host")


def _aux_interrupt_protected() -> bool:
    return bool(getattr(_aux_interrupt_protection, "active", False))


def _aux_interrupt_cancel_requested() -> bool:
    """Return whether an explicit host cancel overrides aux protection."""
    check = _capture_aux_cancel_check()
    return _captured_aux_cancel_requested(check) if check is not None else False


@contextlib.contextmanager
def aux_interrupt_protection(active: bool = True, cancel_check=None, cancel_event=None):
    """Mark this thread's aux LLM call interrupt-protected (re-entrant-safe). ``cancel_check`` /
    ``cancel_event`` keep an explicit host hard-cancel path (Event preferred); nested scopes inherit both."""
    prev = getattr(_aux_interrupt_protection, "active", False)
    prev_cancel_check = getattr(_aux_interrupt_protection, "cancel_check", None)
    prev_cancel_event = getattr(_aux_interrupt_protection, "cancel_event", None)
    _aux_interrupt_protection.active = active
    if callable(cancel_check):
        _aux_interrupt_protection.cancel_check = cancel_check
    if cancel_event is not None and callable(getattr(cancel_event, "is_set", None)):
        _aux_interrupt_protection.cancel_event = cancel_event
    try:
        yield
    finally:
        _aux_interrupt_protection.active = prev
        _aux_interrupt_protection.cancel_check = prev_cancel_check
        _aux_interrupt_protection.cancel_event = prev_cancel_event


def _capture_aux_cancel_check() -> Optional[Callable[[], Any]]:
    """Capture the current explicit-cancel source on the owning request thread."""
    is_set = getattr(getattr(_aux_interrupt_protection, "cancel_event", None), "is_set", None)
    if callable(is_set):
        return is_set
    # Return the callable itself so attempt-local decision objects keep begin_timeout_cleanup().
    check = getattr(_aux_interrupt_protection, "cancel_check", None)
    return check if callable(check) else None


def _captured_aux_cancel_requested(cancel_check: Callable[[], Any]) -> bool:
    """Read a request-thread cancellation source without leaking its failures."""
    try:
        return bool(cancel_check())
    except Exception:
        logger.debug("captured aux cancel check failed", exc_info=True)
        return False


class _AuxiliaryCancellationDecision:
    """Atomically choose explicit cancellation or provider timeout per attempt."""

    def __init__(self, source_cancel_check: Callable[[], Any]) -> None:
        self._source_cancel_check = source_cancel_check
        self._lock = threading.Lock()
        self._outcome = "active"

    def __call__(self) -> bool:
        with self._lock:
            if self._outcome == "active" and _captured_aux_cancel_requested(self._source_cancel_check):
                self._outcome = "cancelled"
            return self._outcome == "cancelled"

    def begin_timeout_cleanup(self) -> bool:
        """Return whether timeout won and destructive cleanup is permitted."""
        with self._lock:
            if self._outcome == "active":
                cancelled = _captured_aux_cancel_requested(self._source_cancel_check)
                self._outcome = "cancelled" if cancelled else "timed_out"
            return self._outcome == "timed_out"


# Forward-progress hooks for streamed aux calls: a fixed host deadline kills a SLOW model
# streaming a big summary as hard as a HUNG one, so wire consumers tick the progress hook only
# for non-empty payloads and the host extends its deadline while tokens move. Thread-local:
# the call and its stream consumption run on the installing thread.
_aux_progress = threading.local()
_aux_dispatch = threading.local()
_aux_provider_response = threading.local()
# Absolute monotonic deadline of the waiting HOST. The stream's own ceiling
# (_aux_stream_total_ceiling, >= the host's and started later) would otherwise leave an
# orphaned stream still billing after every host-ceiling timeout.
# Absolute wall-clock deadline (time.monotonic) of the HOST waiting for this auxiliary call, when it has one
# (#99692). Liveness alone is not enough: a host also stops waiting at its own total ceiling, and the
# streamed consumer below bounds itself only by _aux_stream_total_ceiling() — a budget derived from the aux
# request timeout, which is >= the host ceiling for every configured value AND starts counting later. So the
# stream that outlives its abandoned host is not an edge case; it is the guaranteed outcome of every
# total-ceiling timeout.
_aux_stream_deadline = threading.local()


def _tick_hook(local: threading.local, label: str) -> None:
    """Call the thread-local hook installed on ``local``, if any. Never raises."""
    hook = getattr(local, "hook", None)
    if hook is None:
        return
    try:
        hook()
    except Exception:
        logger.debug("aux %s hook failed", label, exc_info=True)


def _notify_aux_progress() -> None:
    """Tick the installed forward-progress hook, if any."""
    _tick_hook(_aux_progress, "progress")


def _notify_aux_dispatch() -> None:
    """Record an actual provider dispatch without claiming response progress."""
    _tick_hook(_aux_dispatch, "dispatch")


def _notify_aux_timing_response() -> None:
    """Record a content-free frame (keepalive/empty delta): counts toward
    ``time_to_first_progress_ms`` but must not reset a compression inactivity fence."""
    _tick_hook(_aux_provider_response, "provider response")


def _notify_aux_provider_response() -> None:
    """Record a provider response/chunk, then preserve the liveness signal."""
    _notify_aux_timing_response()
    _notify_aux_progress()


def _aux_progress_active() -> bool:
    return getattr(_aux_progress, "hook", None) is not None


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Field access for wire objects that may be dicts or SDK/SimpleNamespace objects."""
    val = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    return default if val is None else val


def _anthropic_event_has_content(event: Any) -> bool:
    """Whether an Anthropic stream event carries a non-empty payload."""
    event_type = _field(event, "type")
    if event_type == "content_block_delta":
        delta = _field(event, "delta")
        return any(bool(_field(delta, f)) for f in ("text", "thinking", "partial_json", "signature", "citation"))
    if event_type == "content_block_start":
        block = _field(event, "content_block")
        return _field(block, "type") == "tool_use" and any(bool(_field(block, f)) for f in ("id", "name"))
    return False


def _anthropic_aux_stream_event_hook() -> Callable[[Any], None]:
    """Per-event callback for the Anthropic aux wire: progress only for substantive payloads
    (keepalives must not keep a stalled summary alive), stop at the host deadline or explicit
    cancel. The ``TimeoutError`` text must say "timed out" so ``_is_timeout_error`` classifies it."""
    host_deadline = _current_aux_stream_deadline()
    started = time.monotonic()

    def _on_event(event: Any) -> None:
        if _anthropic_event_has_content(event):
            _notify_aux_provider_response()
        else:
            _notify_aux_timing_response()
        if _aux_interrupt_cancel_requested():
            raise AuxiliaryExplicitCancellation()
        if host_deadline is not None and time.monotonic() >= host_deadline:
            raise TimeoutError(
                "Anthropic auxiliary stream timed out at the host compression "
                f"deadline after {time.monotonic() - started:.0f}s (the caller already stopped waiting)")

    return _on_event


# A dead stream fails at the no-progress window (first token AND between tokens); a live
# stream re-arms per event, bounded by _aux_stream_total_ceiling().
_AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS = 60.0


@contextlib.contextmanager
def _aux_thread_local_hook(local: threading.local, hook):
    """Install one thread-local hook, restoring the prior on exit (non-callable = passthrough)."""
    previous = getattr(local, "hook", None)
    local.hook = hook if callable(hook) else previous
    try:
        yield
    finally:
        local.hook = previous


@contextlib.contextmanager
def aux_progress_hook(hook):
    """Install *hook* as the current thread's aux forward-progress callback (None = passthrough)."""
    with _aux_thread_local_hook(_aux_progress, hook):
        yield


def _current_aux_stream_deadline() -> Optional[float]:
    """The waiting host's absolute monotonic deadline, if one is installed."""
    return getattr(_aux_stream_deadline, "value", None)


@contextlib.contextmanager
def aux_stream_deadline(deadline: Optional[float]):
    """Publish the host's absolute ``time.monotonic()`` deadline to the stream consumer.

    ``None`` is a passthrough; re-entrant-safe. Host->worker return leg of the progress hook:
    without it the isolated provider daemon streams to its own ceiling after the host stopped
    waiting, billing a summary the commit fence refuses.

    ``8207862212`` releases the compression OWNER when the fence is cancelled, but the isolated provider
    daemon (:func:`_run_protected_sync_provider_call`) that holds the socket keeps streaming to its own
    ``_aux_stream_total_ceiling`` budget — >= the host's ceiling by construction — billing an abandoned
    summary the commit fence is already guaranteed to refuse, and stacking one fresh orphan per turn on a
    session that compression never managed to shrink. See #99692.
    """
    previous = getattr(_aux_stream_deadline, "value", None)
    _aux_stream_deadline.value = deadline if isinstance(deadline, (int, float)) else previous
    try:
        yield
    finally:
        _aux_stream_deadline.value = previous


def _run_protected_sync_provider_call(callback: Callable[[dict[str, Any]], Any], kwargs: dict[str, Any]) -> Any:
    """Run one protected provider callback in an attempt-isolated daemon thread.

    Aux clients are process-shared and cannot be closed to wake one request, so the callback (incl.
    stream aggregation) runs in a daemon while the owner polls cancellation; on cancel the owner
    unwinds at once and the daemon finishes under the provider timeout in ``kwargs`` (it owns no
    transcript/commit state, never holds the session lock). Unprotected / no cancel source: direct.
    """
    source_cancel_check = _capture_aux_cancel_check()
    if not _aux_interrupt_protected() or not callable(source_cancel_check):
        return callback(kwargs)
    # One linearized outcome per attempt: the host Event is reused/cleared on later turns and
    # the Codex timeout Timer may race owner polling — same lock for both.
    cancel_check = _AuxiliaryCancellationDecision(source_cancel_check)
    if cancel_check():
        raise AuxiliaryExplicitCancellation()
    # Thread-locals do not cross into the daemon: timing hooks fire from the thread running
    # the callback, and the host deadline is inert unless carried along.
    progress_hook = getattr(_aux_progress, "hook", None)
    dispatch_hook = getattr(_aux_dispatch, "hook", None)
    provider_response_hook = getattr(_aux_provider_response, "hook", None)
    host_deadline = _current_aux_stream_deadline()
    # #99692: the stream is consumed on the daemon below, and thread-locals do not cross that boundary — an
    # owner-thread-only deadline would leave the fix inert on exactly the path large-session compression
    # takes (protected call + hard-cancel source installed).
    provider_context = contextvars.copy_context()
    done = threading.Event()
    outcome: dict[str, Any] = {}

    def _provider_worker() -> None:
        try:
            with (
                aux_progress_hook(progress_hook),
                _aux_thread_local_hook(_aux_dispatch, dispatch_hook),
                _aux_thread_local_hook(_aux_provider_response, provider_response_hook),
                aux_stream_deadline(host_deadline),
                aux_interrupt_protection(cancel_check=cancel_check),
            ):
                outcome["result"] = callback(kwargs)
        except BaseException as exc:
            outcome["exception"] = exc
        finally:
            done.set()

    threading.Thread(
        target=provider_context.run, args=(_provider_worker,), name="hermes-protected-aux-provider",
        daemon=True).start()
    while True:
        # Check cancel before AND after each wait so it wins when result publication and the
        # host Event land in the same polling interval.
        if _captured_aux_cancel_requested(cancel_check):
            raise AuxiliaryExplicitCancellation()
        if not done.wait(0.02):
            continue
        if _captured_aux_cancel_requested(cancel_check):
            raise AuxiliaryExplicitCancellation()
        exception = outcome.get("exception")
        if exception is not None:
            raise exception
        return outcome.get("result")


def _client_declares(client_obj: Any, flag: str) -> bool:
    """Whether ``client_obj`` (or its class) sets ``flag`` truthy; absent → False. Capability declaration,
    not isinstance, so out-of-tree clients can opt out of wrappers unimported (cf. SUPPORTS_HERMES_TOOL_CALLS)."""
    try:
        return bool(getattr(client_obj, flag, False))
    except Exception:
        return False


def _safe_isinstance(obj: Any, maybe_type: Any) -> bool:
    """Return False instead of raising when a patched symbol is not a type."""
    try:
        return isinstance(obj, maybe_type)
    except TypeError:
        return False


def _extract_url_query_params(url: str):
    """Extract query params from URL, return (clean_url, default_query dict or None)."""
    parsed = urlparse(url)
    if parsed.query:
        return urlunparse(parsed._replace(query="")), {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return url, None


# Warn only once per process about stale OPENAI_BASE_URL.
_stale_base_url_warned = False

# Set at resolve time — True if the auxiliary client points to Nous Portal
auxiliary_is_nous: bool = False

_AUTH_JSON_PATH = get_hermes_home() / "auth.json"
_AUTH_JSON_PATH_AT_IMPORT = _AUTH_JSON_PATH


def _auth_json_path():
    """Active profile's ``auth.json`` at call time (a patched ``_AUTH_JSON_PATH`` still wins). The
    import-time constant is the LAUNCH profile's; under multiplexing a secondary's auxiliary calls
    would otherwise authenticate to Nous with the default profile's token."""
    from hermes_cli.auth import _auth_file_path
    return _AUTH_JSON_PATH if _AUTH_JSON_PATH != _AUTH_JSON_PATH_AT_IMPORT else _auth_file_path()

def _try_nous(vision: bool = False) -> Tuple[Optional[OpenAI], Optional[str]]:
    # Cross-session rate guard: another session's 429 means skip Nous rather than pile onto the tapped RPH bucket.
    with contextlib.suppress(Exception):
        from agent.nous_rate_guard import nous_rate_limit_remaining
        _remaining = nous_rate_limit_remaining()
        if _remaining is not None and _remaining > 0:
            logger.debug("Auxiliary: skipping Nous Portal (rate-limited, resets in %.0fs)", _remaining)
            _mark_provider_unhealthy("nous", ttl=_remaining)
            return None, None
    nous = _read_nous_auth()
    runtime = _resolve_nous_runtime_api(force_refresh=False)
    if runtime is None and not nous:
        logger.warning("Auxiliary Nous client unavailable: no Nous authentication found (run: hermes auth).")
        _mark_provider_unhealthy("nous", ttl=60)
        return None, None
    if runtime is None and nous:
        logger.debug("Auxiliary Nous: runtime JWT refresh failed; checking stored auth.json token.")
    if runtime is not None:
        api_key, base_url = runtime
    else:
        api_key = _nous_api_key(nous or {})
        if not api_key:
            logger.warning(
                "Auxiliary Nous client unavailable: no usable inference JWT found "
                "(run: hermes auth add nous)."
            )
            _mark_provider_unhealthy("nous", ttl=60)
            return None, None
        base_url = str(
            (nous or {}).get("inference_base_url") or _scoped_key_env("NOUS_INFERENCE_BASE_URL") or _NOUS_DEFAULT_BASE_URL
        ).rstrip("/")
    lane = "vision" if vision else "text"
    # The free tier's host serves exactly one model, for every lane: asking it for the Portal's
    # recommended aux model is a guaranteed 429 ``model_not_free``. Pin the route's model instead.
    # Vision rides the same id (the backing model is multimodal; a backing that is not answers
    # the request with the upstream's own error, which the ladder handles like any other).
    from hermes_cli.anon_auth import GUEST_MODEL, route_is_welcome_host
    global auxiliary_is_nous
    if route_is_welcome_host(base_url):
        auxiliary_is_nous = True
        logger.debug("Auxiliary/%s: Nous free tier; using %s", lane, GUEST_MODEL)
        return _create_openai_client(api_key=api_key, base_url=base_url), GUEST_MODEL
    auxiliary_is_nous = True
    logger.debug("Auxiliary client: Nous Portal")
    # Portal recommended-models is authoritative (tier-aware); _NOUS_MODEL when unreachable/null.
    # Probes skip the lookup: exact model is irrelevant and it hits the network.
    model = _NOUS_MODEL
    if not _aux_probe_active():
        try:
            from hermes_cli.models import get_nous_recommended_aux_model
            recommended = get_nous_recommended_aux_model(vision=vision)
            if recommended:
                model = recommended
                logger.debug("Auxiliary/%s: using Portal-recommended model %s", lane, model)
            else:
                logger.debug("Auxiliary/%s: no Portal recommendation, falling back to %s", lane, model)
        except Exception as exc:
            logger.debug(
                "Auxiliary/%s: recommended-models lookup failed (%s); "
                "falling back to %s",
                lane, exc, model,
            )
    return _create_openai_client(api_key=api_key, base_url=base_url), model


# Compatibility mirrors for older readers/tests; the ContextVar below is
# authoritative (overlapping gateway sessions make a process-global unsafe).
_RUNTIME_MAIN_PROVIDER: str = ""
_RUNTIME_MAIN_MODEL: str = ""
_RUNTIME_MAIN_BASE_URL: str = ""
_RUNTIME_MAIN_API_KEY: Any = ""
_RUNTIME_MAIN_API_MODE: str = ""
_RUNTIME_MAIN_AUTH_MODE: str = ""
_RUNTIME_MAIN_CONTEXT: contextvars.ContextVar[Optional[Dict[str, Any]]] = (
    contextvars.ContextVar("auxiliary_runtime_main", default=None)
)

_RELAY_AUX_CALL_CONTEXT: contextvars.ContextVar[Optional[Dict[str, Any]]] = (
    contextvars.ContextVar("auxiliary_relay_call", default=None)
)


@contextlib.contextmanager
def _relay_aux_call_scope(args: tuple, kwargs: dict):
    """Bind a fresh relay call context for one auxiliary call; mark it failed on any exception."""
    task = args[0] if args else kwargs.get("task")
    token = _RELAY_AUX_CALL_CONTEXT.set({
        "task": str(task or "unknown"),
        "request_id": f"aux-{uuid.uuid4().hex}",
        "attempt_count": 0,
        "provider": "",
        "model": "",
        "response_model": None,
        "api_mode": "chat_completions",
    })
    try:
        yield
    except BaseException:
        _fail_relay_auxiliary_call()
        raise
    finally:
        _RELAY_AUX_CALL_CONTEXT.reset(token)


def _relay_auxiliary_call(callback):
    """Give every physical retry in one auxiliary call a shared Relay identity."""
    @functools.wraps(callback)
    def wrapped(*args, **kwargs):
        with _relay_aux_call_scope(args, kwargs):
            return callback(*args, **kwargs)
    return wrapped


def _relay_auxiliary_call_async(callback):
    """Async counterpart to :func:`_relay_auxiliary_call`."""
    @functools.wraps(callback)
    async def wrapped(*args, **kwargs):
        with _relay_aux_call_scope(args, kwargs):
            return await callback(*args, **kwargs)
    return wrapped


def _set_relay_auxiliary_route(provider: str | None, model: str | None, api_mode: str | None) -> None:
    context = _RELAY_AUX_CALL_CONTEXT.get()
    if context is None:
        return
    context["provider"] = str(provider or "auxiliary")
    context["model"] = str(model or "unknown")
    context["response_model"] = None
    context["api_mode"] = str(api_mode or "chat_completions")


def _record_route_info(
    route_info: Optional[Dict[str, str]], provider: Optional[str], model: Optional[str]
) -> None:
    """Expose the concrete route selected for one auxiliary call."""
    if route_info is not None:
        route_info["provider"] = provider or "auto"
        route_info["model"] = model or "default"


def _relay_auxiliary_metadata(
    *, provider: str | None = None, api_mode: str | None = None
) -> tuple[str, str, dict[str, Any]] | None:
    context = _RELAY_AUX_CALL_CONTEXT.get()
    if context is None:
        return None
    attempt_count = int(context.get("attempt_count") or 0)
    context["attempt_count"] = attempt_count + 1
    provider_name = str(provider or context.get("provider") or "auxiliary")
    model_name = str(context.get("model") or "unknown")
    return provider_name, model_name, {
        "api_mode": str(api_mode or context.get("api_mode") or "chat_completions"),
        "api_request_id": str(context["request_id"]),
        "call_role": f"auxiliary:{context['task']}",
        "retry_count": attempt_count,
        "auxiliary_task": str(context["task"]),
    }


def _relay_sync_completion(
    client: Any, kwargs: dict[str, Any], *, provider: str | None = None,
    api_mode: str | None = None, create: Callable[[dict[str, Any]], Any] | None = None,
) -> Any:
    from agent.auxiliary_wire import prepare_chat_messages

    kwargs = prepare_chat_messages(client, kwargs)
    # The progress hook is installed per TASK, so every attempt (retries, recovery rungs, fallbacks)
    # must stream through _create_with_progress or the compression watchdog sees silence (#98466).
    callback = create or (lambda request: _create_with_progress(client, request))
    route = _relay_auxiliary_metadata(provider=provider, api_mode=api_mode)
    # Isolate only the provider callback so the owning thread can unwind its lease/DB
    # transaction on hard cancel without touching the shared client.
    if route is None:
        return _run_protected_sync_provider_call(callback, kwargs)
    provider_name, fallback_model, metadata = route
    from agent import relay_llm
    return relay_llm.execute_current(
        kwargs, lambda request: _run_protected_sync_provider_call(callback, request),
        name=provider_name, model_name=str(kwargs.get("model") or fallback_model),
        metadata=metadata, defer_logical_completion=True,
    )


async def _relay_async_completion(
    client: Any, kwargs: dict[str, Any], *, provider: str | None = None,
    api_mode: str | None = None, create: Callable[[dict[str, Any]], Any] | None = None,
) -> Any:
    from agent.auxiliary_wire import prepare_chat_messages

    kwargs = prepare_chat_messages(client, kwargs)
    # Async twin of the seam default above (#98466).
    callback = create or (lambda request: _acreate_with_progress(client, request))
    route = _relay_auxiliary_metadata(provider=provider, api_mode=api_mode)
    if route is None:
        return await callback(kwargs)
    provider_name, fallback_model, metadata = route
    from agent import relay_llm
    return await relay_llm.execute_current_async(
        kwargs, callback, name=provider_name, model_name=str(kwargs.get("model") or fallback_model),
        metadata=metadata, defer_logical_completion=True,
    )


def _relay_sync_stream(
    client: Any, kwargs: dict[str, Any], *, provider: str | None = None, api_mode: str | None = None
) -> Any:
    from agent.auxiliary_wire import prepare_chat_messages

    kwargs = prepare_chat_messages(client, kwargs)
    route = _relay_auxiliary_metadata(provider=provider, api_mode=api_mode)
    if route is None:
        return client.chat.completions.create(**kwargs)
    provider_name, fallback_model, metadata = route
    from agent import relay_llm
    return relay_llm.stream_current(
        kwargs, lambda request: client.chat.completions.create(**request), name=provider_name,
        model_name=str(kwargs.get("model") or fallback_model), finalizer=dict, metadata=metadata,
        completed_response_predicate=lambda value: hasattr(value, "choices"),
    )


_RUNTIME_MAIN_COMPAT_SNAPSHOT: Tuple[Any, ...] = ("", "", "", "", "", "")
_RUNTIME_MAIN_COMPAT_LOCK = threading.Lock()


def _publish_runtime_main_mirrors(values: Tuple[Any, ...]) -> None:
    """Write the legacy globals + compat snapshot (``_MAIN_RUNTIME_FIELDS`` order) under the lock."""
    global _RUNTIME_MAIN_PROVIDER, _RUNTIME_MAIN_MODEL, _RUNTIME_MAIN_BASE_URL, _RUNTIME_MAIN_API_KEY
    global _RUNTIME_MAIN_API_MODE, _RUNTIME_MAIN_AUTH_MODE, _RUNTIME_MAIN_COMPAT_SNAPSHOT
    with _RUNTIME_MAIN_COMPAT_LOCK:
        (_RUNTIME_MAIN_PROVIDER, _RUNTIME_MAIN_MODEL, _RUNTIME_MAIN_BASE_URL,
         _RUNTIME_MAIN_API_KEY, _RUNTIME_MAIN_API_MODE, _RUNTIME_MAIN_AUTH_MODE) = values
        _RUNTIME_MAIN_COMPAT_SNAPSHOT = tuple(values)


def _compat_runtime_main() -> Optional[Dict[str, Any]]:
    """Expose deliberately patched legacy globals as a main context.

    Mirrors must never become runtime inputs: a direct patch counts only when it differs from
    the mirrored snapshot and only on the main thread.
    """
    if threading.current_thread() is not threading.main_thread():
        return None
    values = (_RUNTIME_MAIN_PROVIDER, _RUNTIME_MAIN_MODEL, _RUNTIME_MAIN_BASE_URL,
              _RUNTIME_MAIN_API_KEY, _RUNTIME_MAIN_API_MODE, _RUNTIME_MAIN_AUTH_MODE)
    if values == _RUNTIME_MAIN_COMPAT_SNAPSHOT:
        return None
    return dict(zip(_MAIN_RUNTIME_FIELDS, values))


def _runtime_main_value(field: str) -> Any:
    """Read one runtime field through context-local/controlled legacy state."""
    runtime = _RUNTIME_MAIN_CONTEXT.get()
    if runtime is None:
        runtime = _compat_runtime_main()
    return (runtime.get(field) or "") if isinstance(runtime, dict) else ""


def set_runtime_main(
    provider: str, model: str, *, requested_provider: str = "", base_url: str = "",
    api_key: Any = "", api_mode: str = "", auth_mode: str = "", session_id: str = "",
    cache_scope: str = "",
) -> contextvars.Token:
    """Record the current context's live main runtime for auxiliary routing.

    Context-local so concurrent gateway sessions don't clobber each other; legacy mirrors are
    updated for old readers. ``cache_scope`` is the rotation-stable logical cache scope,
    preferred over ``session_id`` for prompt_cache_key derivation.

    ``cache_scope`` is the rotation-stable logical cache scope (compression- lineage root —
    agent/prompt_cache_scope.py) resolved once per turn by turn_context; auxiliary Responses calls prefer it
    over ``session_id`` for prompt_cache_key derivation (#79017).
    """
    runtime = {
        "provider": (provider or "").strip().lower(),
        "requested_provider": (requested_provider or "").strip().lower(),
        "model": (model or "").strip(),
        "base_url": (base_url or "").strip(),
        "api_key": api_key.strip() if isinstance(api_key, str) else api_key if callable(api_key) else "",
        "api_mode": (api_mode or "").strip(),
        "auth_mode": (auth_mode or "").strip().lower(),
        "session_id": (session_id or "").strip(),
        "cache_scope": (cache_scope or "").strip(),
    }
    # Publish authoritative context before updating the locked mirrors.
    token = _RUNTIME_MAIN_CONTEXT.set(runtime)
    _publish_runtime_main_mirrors(tuple(runtime[field] for field in _MAIN_RUNTIME_FIELDS))
    return token


def reset_runtime_main(token: contextvars.Token) -> None:
    """Restore the runtime binding that preceded one scoped turn."""
    if token is None:
        return
    try:
        _RUNTIME_MAIN_CONTEXT.reset(token)
    except (RuntimeError, ValueError):
        pass  # Tokens can't be reset from a copied Context (workers inherit values, not token ownership).


@contextlib.contextmanager
def scoped_runtime_main(main_runtime: Optional[Dict[str, Any]]):
    """Temporarily bind an explicit runtime without touching legacy mirrors."""
    runtime = _normalize_main_runtime(main_runtime)
    token = _RUNTIME_MAIN_CONTEXT.set(runtime or None)
    try:
        yield runtime
    finally:
        _RUNTIME_MAIN_CONTEXT.reset(token)


def clear_runtime_main() -> None:
    """Clear the runtime override in the current context."""
    _RUNTIME_MAIN_CONTEXT.set(None)
    _publish_runtime_main_mirrors(("", "", "", "", "", ""))


def _warn_stale_openai_base_url(runtime_provider: str) -> None:
    """Warn once when OPENAI_BASE_URL is set but config.yaml names a non-custom provider (a stale
    ~/.hermes/.env value after `hermes model` poisons routing)."""
    global _stale_base_url_warned
    if _stale_base_url_warned:
        return
    _env_base = os.getenv("OPENAI_BASE_URL", "").strip()
    _cfg_provider = runtime_provider or _read_main_provider()
    if (_env_base and _cfg_provider and _cfg_provider != "custom" and not _cfg_provider.startswith("custom:")):
        logger.warning(
            "OPENAI_BASE_URL is set (%s) but model.provider is '%s'. "
            "Auxiliary clients may route to the wrong endpoint. "
            "Run: hermes model to reconfigure, or remove "
            "OPENAI_BASE_URL from ~/.hermes/.env",
            _env_base, _cfg_provider,
        )
        _stale_base_url_warned = True


def _resolve_auto_route(
    main_runtime: Optional[Dict[str, Any]] = None, task: Optional[str] = None
) -> Tuple[Optional[OpenAI], Optional[str], str]:
    """Full auto-detection chain, including the selected provider identity. Priority: (1) main provider +
    main model, regardless of provider type ("auto" means "my main model for side tasks too"; explicit
    per-task overrides still win); (2) configured fallback policy — task chain, then the main agent's
    top-level chain; (3) OpenRouter → Nous → custom → Codex → API-key providers, only with no policy
    and no working main client."""
    global auxiliary_is_nous
    auxiliary_is_nous = False  # Reset — _try_nous() will set True if it wins
    runtime = _normalize_main_runtime(main_runtime)
    _warn_stale_openai_base_url(runtime.get("provider", ""))
    main_provider, main_model, base_url, api_key, api_mode = _main_route_target(runtime, task)
    routed = _try_main_provider_route(main_provider, main_model, base_url, api_key, api_mode)
    if routed is not None:
        return routed
    if task:
        fb_client, fb_model, fb_label = _try_configured_fallback_chain(
            task, main_provider or "auto", reason="main provider unavailable")
        if fb_client is not None:
            return fb_client, fb_model, _fallback_provider_from_label(fb_label)
    fb_client, fb_model, fb_label = _try_main_fallback_chain(
        task, main_provider or "auto", reason="main provider unavailable")
    if fb_client is not None:
        return fb_client, fb_model, fb_label
    if not _discovery_chain_allowed(main_provider, task):
        return None, None, ""
    return _try_discovery_chain()


_MANAGED_LOCAL_STATE_TTL_S = 15.0
_managed_local_cache: "tuple[float, str]" = (0.0, "")


def _managed_local_netloc() -> str:
    """host:port of the managed local llama-server ("" when none), read with a short TTL from
    the supervisor state file provider resolution also uses (exact match)."""
    global _managed_local_cache
    now = time.monotonic()
    ts, cached = _managed_local_cache
    if now - ts < _MANAGED_LOCAL_STATE_TTL_S:
        return cached
    try:
        from hermes_cli.local_runtime.supervisor import state_path
        raw = state_path().read_text(encoding="utf-8")
        base = str((json.loads(raw) or {}).get("base_url", ""))
        netloc = urlparse(base).netloc.lower()
    except Exception:
        netloc = ""
    _managed_local_cache = (now, netloc)
    return netloc


# ── Centralized LLM Call API: call_llm()/async_call_llm() own resolve → cached client → shape
# request → call → return. Every auxiliary LLM consumer should use these.

def _elapsed_ms(started_at: float, now: Optional[float] = None) -> int:
    """Whole milliseconds since ``started_at`` (clamped at 0)."""
    return max(0, int(((time.monotonic() if now is None else now) - started_at) * 1000))


def _stamp_latency_once(latency_info: Optional[Dict[str, int]], key: str, started_at: float) -> None:
    """Record ``key`` in ``latency_info`` the first time it fires."""
    if latency_info is not None and key not in latency_info:
        latency_info[key] = _elapsed_ms(started_at)


@_relay_auxiliary_call
def call_llm(
    task: str = None, *, provider: str = None, model: str = None, base_url: str = None,
    api_key: str = None, main_runtime: Optional[Dict[str, Any]] = None, messages: list,
    temperature: Optional[float] = None, max_tokens: int = None, tools: list = None,
    timeout: float = None, extra_body: dict = None, reasoning_config: Optional[dict] = None,
    extra_headers: Optional[Dict[str, str]] = None, api_mode: str = None, stream: bool = False,
    stream_options: dict = None, route_info: Optional[Dict[str, str]] = None,
    latency_info: Optional[Dict[str, int]] = None,
) -> Any:
    """Run an auxiliary LLM request, applying the configured task limit."""
    queue_started_at = time.monotonic()
    semaphore = _acquire_sync_aux_semaphore(task)
    if semaphore is not None:
        semaphore.acquire()
    request_started_at = time.monotonic()
    if latency_info is not None:
        latency_info["queue_wait_ms"] = _elapsed_ms(queue_started_at, request_started_at)
    prior_progress_hook = getattr(_aux_progress, "hook", None)
    try:
        with (
            aux_progress_hook(
                prior_progress_hook
                if callable(prior_progress_hook)
                else ((lambda: None) if latency_info is not None else None)
            ),
            _aux_thread_local_hook(_aux_dispatch, functools.partial(
                _stamp_latency_once, latency_info, "provider_dispatch_ms", request_started_at)),
            _aux_thread_local_hook(_aux_provider_response, functools.partial(
                _stamp_latency_once, latency_info, "time_to_first_progress_ms", request_started_at)),
        ):
            response = _call_llm_impl(
                task=task, provider=provider, model=model, base_url=base_url, api_key=api_key,
                main_runtime=main_runtime, messages=messages, temperature=temperature,
                max_tokens=max_tokens, tools=tools, timeout=timeout, extra_body=extra_body,
                reasoning_config=reasoning_config, extra_headers=extra_headers, api_mode=api_mode,
                stream=stream, stream_options=stream_options, route_info=route_info,
            )
        if stream and semaphore is not None:
            stream_semaphore = semaphore
            semaphore = None
            return _release_sync_semaphore_after_stream(response, stream_semaphore)
        return response
    finally:
        if latency_info is not None:
            latency_info["summary_generation_ms"] = _elapsed_ms(request_started_at)
        if semaphore is not None:
            semaphore.release()


def _release_sync_semaphore_after_stream(stream: Any, semaphore: threading.BoundedSemaphore):
    """Release a permit only after a streaming response is consumed or closed."""
    try:
        yield from stream
    finally:
        try:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        finally:
            semaphore.release()


def _call_llm_impl(
    task: str = None, *, provider: str = None, model: str = None, base_url: str = None,
    api_key: str = None, main_runtime: Optional[Dict[str, Any]] = None, messages: list,
    temperature: Optional[float] = None, max_tokens: int = None, tools: list = None,
    timeout: float = None, extra_body: dict = None, reasoning_config: Optional[dict] = None,
    extra_headers: Optional[Dict[str, str]] = None, api_mode: str = None, stream: bool = False,
    stream_options: dict = None, route_info: Optional[Dict[str, str]] = None,
) -> Any:
    """Centralized synchronous LLM call: resolve provider/model, auth, kwargs, fallbacks.
    task: aux task whose provider:model comes from config (ignored if provider set); api_mode
    overrides task config; timeout=None reads auxiliary.{task}.timeout; extra_headers override
    client defaults. stream=True returns the raw SDK stream (caller consumes/falls back)
    instead of a validated response. RuntimeError if no provider is configured."""
    req, retry_kwargs, candidate_kwargs = _plan_aux_call(
        task, async_mode=False, provider=provider, model=model, base_url=base_url,
        api_key=api_key, main_runtime=main_runtime, messages=messages,
        temperature=temperature, max_tokens=max_tokens, tools=tools, timeout=timeout,
        extra_body=extra_body, reasoning_config=reasoning_config,
        extra_headers=extra_headers, api_mode=api_mode, route_info=route_info,
    )
    client, kwargs, request_provider = req.client, req.kwargs, req.request_provider
    # Streaming path (MoA aggregator): return the raw SDK stream, skipping validation and
    # the fallback chain (they assume a complete response); the caller owns reassembly/fallback.
    if stream:
        kwargs["stream"] = True
        if stream_options:
            kwargs["stream_options"] = stream_options
        if task == "moa_aggregator" and isinstance(client, CodexAuxiliaryClient):
            # Responses-shim clients consume the stream internally and return a completed
            # object Relay's managed stream would iterate; the MoA facade wraps it as one chunk.
            return client.chat.completions.create(**kwargs)
        return _relay_sync_stream(client, kwargs, provider=request_provider, api_mode=req.resolved_api_mode)

    def _primary(**validate_kw: Any) -> Any:
        # Retry on the same provider for a transient transport blip (connection reset / streaming-close /
        # incomplete chunked read / 5xx / 408) before the except-chain below escalates to provider/model
        # fallback. A dropped connection shouldn't abandon an otherwise-healthy provider — this especially
        # matters for pinned auxiliary calls like MoA reference advisors, where "fallback to another
        # provider" is not a meaningful recovery (the advisor is a specific model), so a transient blip that
        # isn't retried simply loses that advisor for the turn (root of the run2 double-advisor "Connection
        # error" collapse — a genuine upstream blip hitting both parallel advisors at once). Attempts are
        # bounded and use exponential backoff. Count is configurable via auxiliary.transient_retries
        # (default 2 retries → 3 total attempts); a second/third failure or any non-transient error falls
        # through to ``first_err`` and the existing fallback handling unchanged. Unified home for the
        # transient retry every auxiliary task shares. (PR #16587)
        return _validate_llm_response(
            _relay_sync_completion(
                client, kwargs, provider=request_provider, api_mode=req.resolved_api_mode,
                create=lambda request: _create_with_progress(
                    client, request, task,
                    force_stream=_provider_requires_stream(
                        request_provider, req.base_info or req.resolved_base_url),
                ),
            ),
            task, **validate_kw,
        )
    try:
        # Bounded same-provider retry (exponential backoff, auxiliary.transient_retries) for
        # transient blips before escalating to fallback — a dropped connection shouldn't
        # abandon a healthy provider (matters for pinned MoA advisors).
        try:
            return _primary(provider=request_provider, base_url=req.base_info)
        except Exception as transient_err:
            if not _should_retry_same_provider(task, transient_err, ""):
                raise
            _max_transient_retries = _transient_retry_count()
            _last_transient = transient_err
            for _attempt in range(1, _max_transient_retries + 1):
                _backoff = min(_TRANSIENT_RETRY_BACKOFF_BASE * (2.0 ** (_attempt - 1)), 8.0)
                logger.info("Auxiliary %s: transient transport error (attempt %d/%d); "
                            "retrying same provider after %.1fs before fallback: %s",
                            task or "call", _attempt, _max_transient_retries, _backoff, _last_transient)
                time.sleep(_backoff)
                try:
                    return _primary()
                except Exception as retry_transient:
                    if not _is_transient_transport_error(retry_transient):
                        raise
                    _last_transient = retry_transient
            raise _last_transient
    except Exception as first_err:
        def _perform(step: _LadderStep) -> Any:
            kind, args, kw = _ladder_step_call(step, req, retry_kwargs, candidate_kwargs)
            if kind == "call":
                return _validate_llm_response(_relay_sync_completion(*args, **kw), task)
            if kind == "retry":
                return _retry_same_provider_sync(**kw)
            return _call_fallback_candidate_sync(*args, **kw)
        return _drive_ladder(
            _start_recovery_ladder(first_err, req, retry_kwargs, task=task, async_mode=False, route_info=route_info),
            _perform)


def _coerce_llm_message(response):
    """Pull a message (dict, object, or str) out of a response-or-message value: dict-shaped
    responses/bare messages (compression, proxies) and ChatCompletion objects; MagicMock
    ``reasoning_*`` attrs are deliberately not strings."""
    if response is None or isinstance(response, str):
        return response
    if isinstance(response, dict):
        if "choices" not in response:
            return response
        choices = response.get("choices") or []
    else:
        choices = getattr(response, "choices", None)
        if not choices:
            return response
    return _message_field(choices[0], "message") if choices else None


def _message_field(msg, name):
    return msg.get(name) if isinstance(msg, dict) else getattr(msg, name, None)


def extract_content_or_reasoning(response, *, max_reasoning_chars: int | None = None) -> str:
    """Extract content from an LLM response, falling back to reasoning fields.
    Order: ``content`` (inline think blocks stripped) → ``reasoning``/``reasoning_content`` →
    ``reasoning_details`` (OpenRouter array). Accepts a response or bare message;
    ``max_reasoning_chars`` bounds a reasoning fallback so unbounded chain-of-thought can't
    become the compaction summary. Returns ``""`` if nothing found."""
    msg = _coerce_llm_message(response)
    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg.strip()
    raw = _message_field(msg, "content")
    if not isinstance(raw, str):
        raw = str(raw) if raw else ""
    content = raw.strip()
    if content:
        # Mirrors _strip_think_blocks
        cleaned = re.sub(
            r"<(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>"
            r".*?"
            r"</(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>",
            "", content, flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if cleaned:
            return cleaned
    # Content is empty or reasoning-only — try structured reasoning fields
    reasoning_parts: list[str] = []
    for field in ("reasoning", "reasoning_content"):
        val = _message_field(msg, field)
        if val and isinstance(val, str) and val.strip() and val not in reasoning_parts:
            reasoning_parts.append(val.strip())
    details = _message_field(msg, "reasoning_details")
    if details and isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict):
                summary = detail.get("summary") or detail.get("content") or detail.get("text")
                if summary and summary not in reasoning_parts:
                    reasoning_parts.append(summary.strip() if isinstance(summary, str) else str(summary))
    if not reasoning_parts:
        return ""
    text = "\n\n".join(reasoning_parts)
    if max_reasoning_chars is not None and len(text) > max_reasoning_chars:
        logger.warning("fell back to reasoning fields (%d chars); truncating to %d",
                       len(text), max_reasoning_chars)
        return text[:max_reasoning_chars]
    return text


@_relay_auxiliary_call_async
async def async_call_llm(
    task: str = None, *, provider: str = None, model: str = None, base_url: str = None,
    api_key: str = None, main_runtime: Optional[Dict[str, Any]] = None, messages: list,
    temperature: Optional[float] = None, max_tokens: int = None, tools: list = None,
    timeout: float = None, extra_body: dict = None, reasoning_config: Optional[dict] = None,
    route_info: Optional[Dict[str, str]] = None,
) -> Any:
    """Run an asynchronous auxiliary LLM request under the configured limit."""
    semaphore = _acquire_async_aux_semaphore(task)
    if semaphore is not None:
        await semaphore.acquire()
    try:
        return await _async_call_llm_impl(
            task=task, provider=provider, model=model, base_url=base_url, api_key=api_key,
            main_runtime=main_runtime, messages=messages, temperature=temperature,
            max_tokens=max_tokens, tools=tools, timeout=timeout, extra_body=extra_body,
            reasoning_config=reasoning_config, route_info=route_info,
        )
    finally:
        if semaphore is not None:
            semaphore.release()


async def _async_call_llm_impl(
    task: str = None, *, provider: str = None, model: str = None, base_url: str = None,
    api_key: str = None, main_runtime: Optional[Dict[str, Any]] = None, messages: list,
    temperature: Optional[float] = None, max_tokens: int = None, tools: list = None,
    timeout: float = None, extra_body: dict = None, reasoning_config: Optional[dict] = None,
    route_info: Optional[Dict[str, str]] = None,
) -> Any:
    """Centralized asynchronous LLM call; see call_llm() for full documentation.
    No per-request header / api_mode override on the async entry point."""
    req, retry_kwargs, candidate_kwargs = _plan_aux_call(
        task, async_mode=True, provider=provider, model=model, base_url=base_url,
        api_key=api_key, main_runtime=main_runtime, messages=messages,
        temperature=temperature, max_tokens=max_tokens, tools=tools, timeout=timeout,
        extra_body=extra_body, reasoning_config=reasoning_config,
        extra_headers=None, api_mode=None, route_info=route_info,
    )
    client, kwargs, request_provider = req.client, req.kwargs, req.request_provider
    try:
        # Retry ONCE on the same provider for a transient blip before fallback (see call_llm()).
        # (PR #16587)
        _force_stream_async = _provider_requires_stream(request_provider, req.base_info or req.resolved_base_url)

        async def _acreate(_kwargs: Dict[str, Any]) -> Any:
            return await _acreate_with_progress(client, _kwargs, task, force_stream=_force_stream_async)

        async def _primary(**validate_kw: Any) -> Any:
            return _validate_llm_response(
                await _relay_async_completion(
                    client, kwargs, provider=request_provider, api_mode=req.resolved_api_mode,
                    create=_acreate),
                task, **validate_kw)
        try:
            return await _primary(provider=request_provider, base_url=req.base_info)
        except Exception as transient_err:
            # The async Codex adapter wraps the sync stream via to_thread: same TimeoutError here.
            if not _should_retry_same_provider(task, transient_err, " (async)"):
                raise
            logger.info("Auxiliary %s (async): transient transport error; retrying "
                        "once on the same provider before fallback: %s", task or "call", transient_err)
            return await _primary()
    except Exception as first_err:
        async def _perform(step: _LadderStep) -> Any:
            kind, args, kw = _ladder_step_call(step, req, retry_kwargs, candidate_kwargs)
            if kind == "call":
                return _validate_llm_response(await _relay_async_completion(*args, **kw), task)
            if kind == "retry":
                return await _retry_same_provider_async(**kw)
            fb_client, fb_model, fb_label = args
            fb_client, _ = _to_async_client(fb_client, fb_model or "", is_vision=(task == "vision"))
            return await _call_fallback_candidate_async(fb_client, fb_model, fb_label, **kw)
        return await _drive_ladder_async(
            _start_recovery_ladder(first_err, req, retry_kwargs, task=task, async_mode=True, route_info=route_info),
            _perform)


# Responsibilities split out of this module (re-exported for every caller).
from agent.auxiliary_catalog import (  # noqa: E402
    _PROVIDER_ALIASES,
    _normalize_aux_provider,
    OMIT_TEMPERATURE,
    _bare_model,
    _is_kimi_model,
    _is_arcee_trinity_thinking,
    _CODEX_GPT54_GPT55_COMPACTION_THRESHOLD,
    _CODEX_SPARK_COMPACTION_THRESHOLD,
    _is_codex_gpt54_or_gpt55,
    _codex_route_bare_model,
    _is_codex_spark,
    _fixed_temperature_for_model,
    _compression_threshold_for_model,
    _FAST_MODEL_FAMILIES,
    _FAST_MODEL_EXCLUDE,
    _model_recency_key,
    _fast_model_from_catalog,
    _get_aux_model_for_provider,
    _API_KEY_PROVIDER_AUX_MODELS_FALLBACK,
    _API_KEY_PROVIDER_AUX_MODELS,
    _FAST_MODEL_TASKS,
    _task_prefers_fast_model,
    _PROVIDER_VISION_MODELS,
    _resolve_provider_vision_default,
    _PROVIDERS_WITHOUT_VISION,
    _OR_HEADERS_BASE,
    _apply_user_default_headers,
    build_or_headers,
    _NVIDIA_NIM_CLOUD_HEADERS,
    build_nvidia_nim_headers,
    _HERMES_VERSION,
    _AI_GATEWAY_HEADERS,
    _nous_portal_tags,
    _nous_extra_body,
    _OPENROUTER_MODEL,
    _NOUS_MODEL,
    _NOUS_DEFAULT_BASE_URL,
    _ANTHROPIC_DEFAULT_BASE_URL,
    _DUAL_SURFACE_ANTHROPIC_HOST_SUFFIXES,
    _DUAL_SURFACE_ANTHROPIC_HOST_PREFIXES,
    _is_dual_surface_anthropic_host,
    _to_openai_base_url,
    _load_pool_with_credentials,
    _select_pool_entry,
    _peek_pool_entry,
    _pool_runtime_api_key,
    _pool_runtime_base_url,
    _ANTHROPIC_COMPATIBLE_HOSTS,
    _is_anthropic_compatible_host,
    _nous_min_key_ttl_seconds,
    _scoped_key_env,
)
from agent.auxiliary_adapters import (  # noqa: E402
    _parse_codex_final_response,
    _close_quietly,
    _CodexStreamGuard,
    _CodexCompletionsAdapter,
    _ChatShim,
    _AsyncCompletionsAdapter,
    _AsyncAuxiliaryClientBase,
    _AsyncAnthropicCompletionsAdapter,
    CodexAuxiliaryClient,
    AsyncCodexAuxiliaryClient,
    _translate_anthropic_response_format,
    _AnthropicCompletionsAdapter,
    AnthropicAuxiliaryClient,
    AsyncAnthropicAuxiliaryClient,
    _BedrockCompletionsAdapter,
    BedrockAuxiliaryClient,
    AsyncBedrockAuxiliaryClient,
    _endpoint_speaks_anthropic_messages,
    _maybe_wrap_anthropic,
)
from agent.auxiliary_providers import (  # noqa: E402
    _read_nous_auth,
    _nous_api_key,
    _resolve_nous_pool_runtime_api,
    _resolve_nous_runtime_api,
    _creds_pair,
    _resolve_xai_oauth_for_aux,
    _read_codex_access_token,
    _resolve_api_key_provider,
    _endpoint_default_headers,
    _profile_default_headers,
    _paid_lane_warned,
    _is_free_model,
    _aux_openrouter_settings,
    _warn_paid_lane_once,
    _try_openrouter,
    _describe_openrouter_unavailable,
    _refresh_nous_recommended_model,
    _read_main_field,
    _read_main_model,
    _read_main_provider,
    _read_main_api_key,
    _read_main_base_url,
    _resolve_moa_aggregator,
    _read_main_model_for_aux,
    _read_main_api_key_if_same_host,
    _resolve_custom_runtime,
    _current_custom_base_url,
    _validate_proxy_env_urls,
    _validate_base_url,
    _try_custom_endpoint,
    _build_xai_oauth_aux_client,
    _codex_base_url_override,
    _build_codex_client,
    _try_azure_foundry,
    _try_anthropic,
    _MAIN_RUNTIME_FIELDS,
    _MAIN_RUNTIME_CONTEXT_FIELDS,
    _normalize_main_runtime,
    _get_provider_chain,
)
from agent.auxiliary_errors import (  # noqa: E402
    _AUX_UNHEALTHY_TTL_SECONDS,
    _aux_unhealthy_until,
    _aux_unhealthy_logged_at,
    _AUX_UNHEALTHY_LABEL_ALIASES,
    _normalize_chain_label,
    _mark_provider_unhealthy,
    _is_provider_unhealthy,
    _log_skip_unhealthy,
    _reset_aux_unhealthy_cache,
    _contains_any,
    _PAYMENT_KEYWORDS,
    _is_payment_error,
    _nous_portal_account_has_fresh_paid_access,
    _RATE_LIMIT_KEYWORDS,
    _RATE_LIMIT_BILLING_KEYWORDS,
    _is_rate_limit_error,
    _is_timeout_error,
    _is_connection_error,
    _is_transient_transport_error,
    _DEFAULT_TRANSIENT_RETRIES,
    _TRANSIENT_RETRY_BACKOFF_BASE,
    _transient_retry_count,
    _is_auth_error,
    _is_unsupported_parameter_error,
    _is_structured_output_rejection,
    _without_structured_output_format,
    _is_model_not_found_error,
    _is_model_incompatible_error,
    _is_invalid_aux_response_error,
    _TIMEOUT_NO_RETRY_TASKS,
    _should_skip_same_provider_retry,
    _FALLBACK_REASONS,
    _should_retry_same_provider,
)
from agent.auxiliary_client_cache import (  # noqa: E402
    _evict_cached_clients,
    _evict_cached_client_instance,
    _client_cache,
    _client_cache_lock,
    _CLIENT_CACHE_MAX_SIZE,
    _CallableCacheDiscriminator,
    _runtime_cache_discriminator,
    _client_cache_key,
    _current_event_loop,
    _store_cached_client,
    _refresh_nous_auxiliary_client,
    neuter_async_httpx_del,
    _force_close_async_httpx,
    _schedule_async_close,
    _close_cached_client,
    shutdown_cached_clients,
    cleanup_stale_async_clients,
    _compat_model,
    _get_cached_client,
)
from agent.auxiliary_fallback import (  # noqa: E402
    _pool_cache_hint,
    _POOL_PROVIDER_BY_HOST,
    _AUTH_REFRESH_PROVIDER_BY_HOST,
    _provider_for_host,
    _recoverable_pool_provider,
    _recover_provider_pool,
    _prepare_same_provider_retry,
    _retry_same_provider_sync,
    _retry_same_provider_async,
    _creds_have_api_key,
    _refresh_copilot_credentials,
    _refresh_codex_credentials,
    _refresh_nous_credentials,
    _refresh_anthropic_credentials,
    _refresh_xai_oauth_credentials,
    _refresh_vertex_credentials,
    _CREDENTIAL_REFRESHERS,
    _refresh_provider_credentials,
    _auth_refresh_provider_for_route,
    _fallback_chain_entry,
    _coerce_positive_timeout,
    _fallback_entry_timeout,
    _fallback_provider_from_label,
    _FallbackDestination,
    _complete_fallback_destination,
    _fallback_destination_from_entry,
    _fallback_destination,
    _replan_synchronous_cache_sections,
    _fallback_request_kwargs,
    _plan_fallback_candidate,
    _quarantine_fallback_candidate,
    _plan_fallback_auth_retry,
    _call_fallback_candidate_sync,
    _call_fallback_candidate_async,
    _try_payment_fallback,
    _failed_backend_skip,
    _try_main_agent_model_fallback,
    _task_minimum_context_length,
    _candidate_context_window,
    _context_too_small,
    _try_configured_fallback_chain,
    _try_configured_fallback_for_unavailable_client,
    _fallback_entry_api_key,
    _resolve_fallback_entry,
    _try_main_fallback_chain,
    _main_route_target,
    _try_main_provider_route,
    _discovery_chain_allowed,
    _try_discovery_chain,
)
from agent.auxiliary_routing import (  # noqa: E402
    _effective_provider_for_client,
    _to_async_client,
    _normalize_resolved_model,
    _named_custom_api_key,
    _build_bedrock_client,
    _build_vertex_client,
    _ResolveRequest,
    _ResolveResult,
    _log_once_debug,
    _is_actual_auxiliary_route,
    _wrap_transport,
    _profile_declared_messages_wire,
    _route_client,
    _route_or_warn,
    _resolve_auto_branch,
    _resolve_openrouter_branch,
    _resolve_nous_branch,
    _resolve_openai_codex_branch,
    _resolve_xai_oauth_branch,
    _resolve_custom_branch,
    _named_custom_openai_wire_client,
    _resolve_named_custom_branch,
    _resolve_azure_foundry_branch,
    _resolve_api_key_branch,
    _resolve_external_process_branch,
    _resolve_registry_branch,
    _EXPLICIT_PROVIDER_BRANCHES,
    resolve_provider_client,
    get_text_auxiliary_client,
    _VISION_AUTO_PROVIDER_ORDER,
    _main_model_supports_vision,
    _normalize_vision_provider,
    _deepinfra_strict_vision_backend,
    _STRICT_VISION_BACKENDS,
    _resolve_strict_vision_backend,
    get_available_vision_backends,
    _finalize_vision_client,
    _vision_main_provider_client,
    _vision_auto_route,
    _ZAI_OPENAI_VISION_URLS,
    resolve_vision_provider_client,
    get_auxiliary_extra_body,
    auxiliary_max_tokens_param,
)
from agent.auxiliary_task_config import (  # noqa: E402
    _AUX_DIRECT_API_BASE_URLS,
    _unwrap_moa_provider,
    _expand_direct_api_alias,
    _preserve_provider_with_base_url,
    _resolve_task_provider_model,
    _DEFAULT_AUX_TIMEOUT,
    _COMPRESSION_TIMEOUT_FLOOR_SECONDS,
    _get_auxiliary_task_config,
    CompressionFastLane,
    _fast_lane_config_fields,
    resolve_compression_fast_lane,
    _compression_config_claims_fast_lane,
    _compression_fast_lane_controls,
    _get_task_timeout,
    _effective_aux_timeout,
    _get_task_extra_body,
    _aux_sync_semaphores,
    _aux_async_semaphores,
    _aux_sem_lock,
    _get_task_max_concurrency,
    _cached_semaphore,
    _acquire_sync_aux_semaphore,
    _acquire_async_aux_semaphore,
    _reset_aux_semaphores,
)
from agent.auxiliary_request import (  # noqa: E402
    _ANTHROPIC_COMPAT_PROVIDERS,
    _is_anthropic_compat_endpoint,
    _ANTHROPIC_MEDIA_BLOCKS,
    _convert_openai_images_to_anthropic,
    _PROFILE_REASONING_KEYS,
    _contains_profile_reasoning_fields,
    _NOUS_PROVIDER_NAMES,
    _nous_on_messages_wire,
    _NVIDIA_PROVIDER_NAMES,
    _GEMINI_NATIVE_PROVIDER_NAMES,
    _is_gemini_native_route,
    _forwards_max_tokens,
    _dedupe_tool_names,
    _ProfileProjection,
    _project_provider_profile,
    _merge_aux_extra_body,
    _build_call_kwargs,
    _validate_llm_response,
    _complete_relay_auxiliary_call,
    _fail_relay_auxiliary_call,
    _recover_aux_response_message,
    _extract_aux_response_text,
    _AUX_STREAM_CEILING_FLOOR_SECONDS,
    _AUX_STREAM_CEILING_MULTIPLIER,
    _aux_stream_total_ceiling,
    _client_streams_internally,
    _is_managed_local_endpoint,
    _provider_requires_stream,
    _AFFORDABLE_TOKENS_RE,
    _AFFORDABLE_RETRY_FLOOR_TOKENS,
    _AFFORDABLE_RETRY_MARGIN_TOKENS,
    _affordable_max_tokens_from_error,
    _create_with_progress,
    _stream_request_plan,
    _create_with_progress_once,
    _close_chunk_stream,
    _aggregate_chat_stream,
    _REASONING_DETAIL_TEXT_FIELDS,
    _ChatStreamAccumulator,
    _aggregate_chat_stream_async,
    _acreate_with_stream,
    _async_client_streams_internally,
    _acreate_with_progress,
    _ResolvedAuxRoute,
    _resolve_call_client,
    _PreparedAuxRequest,
    _prepare_aux_request,
    _plan_aux_call,
)
from agent.auxiliary_ladder import (  # noqa: E402
    _LadderStep,
    _rung,
    _param_rung_accepts,
    _credential_rung_accepts,
    _LadderRoute,
    _ladder_parameter_rungs,
    _refreshed_nous_step,
    _ladder_nous_rungs,
    _ladder_credential_rungs,
    _next_fallback_after_quarantine,
    _ladder_provider_fallback,
    _aux_recovery_ladder,
    _drive_ladder,
    _drive_ladder_async,
    _ladder_step_call,
    _start_recovery_ladder,
)
