"""The auxiliary client cache: keys, eviction, storing and closing cached sync/async
clients, and ``_get_cached_client``.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import threading
from typing import Any, Dict, Optional, Tuple


def _evict_cached_clients(provider: str) -> None:
    """Drop cached auxiliary clients for a provider so fresh creds are used."""
    normalized = _aux._normalize_aux_provider(provider)
    with _client_cache_lock:
        for key in [key for key in _aux._client_cache if _aux._normalize_aux_provider(str(key[0])) == normalized]:
            client = _aux._client_cache.get(key, (None, None, None))[0]
            if client is not None:
                _close_cached_client(client)
            _aux._client_cache.pop(key, None)


def _evict_cached_client_instance(target: Any) -> bool:
    """Drop cache entries whose stored client (or its ``_real_client``) is *target*; True if any evicted.

    Used when a cached client is poisoned (closed transport after a timeout). Async wrappers must
    expose the same ``_real_client`` as their sync sibling or the async entry survives.
    """
    if target is None:
        return False
    evicted = False
    with _client_cache_lock:
        for key, entry in list(_aux._client_cache.items()):
            cached = entry[0] if entry is not None else None
            if cached is not None and (cached is target or getattr(cached, "_real_client", None) is target):
                del _aux._client_cache[key]
                evicted = True
    return evicted


# Client cache: (provider, async_mode, base_url, api_key, api_mode, runtime_key) -> (client, default_model, loop)
# Loop identity is NOT part of the key: stale-loop entries are replaced in place on async hits,
# bounding growth to one entry per provider config (avoids fd accumulation in gateways).
# This bounds cache growth to one entry per unique provider config rather than one per (config ×
# event-loop), which previously caused unbounded fd accumulation in long-running gateway processes (#10200).
_client_cache: Dict[tuple, tuple] = {}
_client_cache_lock = threading.Lock()
_CLIENT_CACHE_MAX_SIZE = 64  # safety belt — evict oldest when exceeded


class _CallableCacheDiscriminator:
    """Hash a credential callback by identity without exposing its state."""

    __slots__ = ("_callback",)

    def __init__(self, callback: Any) -> None:
        self._callback = callback  # retained so its id cannot be reused while cached

    def __hash__(self) -> int:
        return id(self._callback)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _CallableCacheDiscriminator) and self._callback is other._callback

    def __repr__(self) -> str:
        return "<callable-api-key>"


def _runtime_cache_discriminator(field: str, value: Any) -> Any:
    """Return a hashable, secret-safe runtime cache-key component."""
    if field == "api_key" and callable(value):
        return _CallableCacheDiscriminator(value)
    if field == "api_key" and isinstance(value, str) and value:
        return ("api-key-digest", hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest())
    return value


def _client_cache_key(
    provider: str, *, async_mode: bool, base_url: Optional[str] = None,
    api_key: Optional[str] = None, api_mode: Optional[str] = None,
    main_runtime: Optional[Dict[str, Any]] = None, is_vision: bool = False,
    task: Optional[str] = None, model: Optional[str] = None,
) -> tuple:
    runtime = _aux._normalize_main_runtime(main_runtime)
    # `auto` resolves through the main runtime and task-specific policy, so both join the key.
    runtime_key = tuple(_runtime_cache_discriminator(f, runtime.get(f, "")) for f in _aux._MAIN_RUNTIME_FIELDS) if provider == "auto" else ()
    task_key = (task or "", _aux._task_prefers_fast_model(task)) if provider == "auto" else ""
    pool_hint = _aux._pool_cache_hint(provider, main_runtime=main_runtime)
    # Model MUST be in the key: concurrent calls to the same endpoint with different models would
    # share an entry, and the second builder's _store_cached_client would close the first's client.
    model_key = model or runtime.get("model", "")
    api_key_key = _runtime_cache_discriminator("api_key", api_key or "")
    # Profile home leads the key: callers that omit api_key (pool / Nous auth.json paths) would
    # otherwise share one client across multiplex profiles holding different credentials.
    return (_aux.hermes_home_key(), provider, async_mode, base_url or "", api_key_key, api_mode or "", runtime_key, is_vision, task_key, pool_hint, model_key)


def _current_event_loop() -> Any:
    """``asyncio.get_event_loop()`` or None when no loop can be obtained (async cache-key binding)."""
    try:
        import asyncio as _aio
        return _aio.get_event_loop()
    except RuntimeError:
        return None


def _store_cached_client(cache_key: tuple, client: Any, default_model: Optional[str], *, bound_loop: Any = None) -> None:
    if isinstance(client, _aux._AuxProbeClientStub):
        return  # probe stubs must never be cached — the next hit would get a dud client
    with _client_cache_lock:
        old_entry = _aux._client_cache.get(cache_key)
        if old_entry is not None and old_entry[0] is not client:
            _close_cached_client(old_entry[0])
        _aux._client_cache[cache_key] = (client, default_model, bound_loop)


def _refresh_nous_auxiliary_client(
    *, cache_provider: str, model: Optional[str], async_mode: bool, base_url: Optional[str] = None,
    api_key: Optional[str] = None, api_mode: Optional[str] = None,
    main_runtime: Optional[Dict[str, Any]] = None, is_vision: bool = False,
    lookup_model: Optional[str] = None, lookup_task: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Refresh Nous runtime creds, rebuild the client, and replace the cache entry.

    ``model`` is the resolved wire model stored as the entry's usable model and returned. The
    cache KEY MUST be built from ``lookup_model``/``lookup_task`` — the model and task as passed
    to ``_get_cached_client`` when the stale client was acquired — so the fresh client overwrites
    the exact entry the stale one is served from. Keying on the resolved model or an empty task
    would leave the expired client immortal and every auxiliary call 401ing forever.

    See #56889.
    For ``provider == "auto"`` the task participates in the cache key (task-specific fallback policy), so it
    MUST be carried into the key here for the same reason as ``lookup_model``; otherwise an auto-provider
    client refreshed on a 401 lands under the ``task=""`` key while the stale entry survives under the
    task-scoped key (#58894).
    """
    runtime = _aux._resolve_nous_runtime_api(force_refresh=True, stale_access_token=api_key)
    if runtime is None:
        return None, model
    fresh_key, fresh_base_url = runtime
    sync_client = _aux._create_openai_client(api_key=fresh_key, base_url=fresh_base_url)
    current_loop = _current_event_loop() if async_mode else None
    if async_mode:
        client, final_model = _aux._to_async_client(sync_client, model or "", is_vision=is_vision)
    else:
        client, final_model = sync_client, model
    cache_key = _client_cache_key(
        cache_provider, async_mode=async_mode, base_url=base_url, api_key=api_key,
        api_mode=api_mode, main_runtime=main_runtime, is_vision=is_vision, task=lookup_task,
        model=lookup_model,
    )
    _store_cached_client(cache_key, client, final_model, bound_loop=current_loop)
    return client, final_model


def neuter_async_httpx_del() -> None:
    """Monkey-patch ``AsyncHttpxClientWrapper.__del__`` to be a no-op.

    The SDK's ``__del__`` schedules ``aclose()`` on the *running* loop, but the transport is
    bound to the loop the client was created on; when that loop is dead this raises "Event loop
    is closed" into prompt_toolkit's loop. Safe because cached clients are closed explicitly and
    the OS reaps the rest. Call once at CLI startup, before any ``AsyncOpenAI`` is created.
    """
    try:
        from openai._base_client import AsyncHttpxClientWrapper
        AsyncHttpxClientWrapper.__del__ = lambda self: None  # type: ignore[assignment]
    except (ImportError, AttributeError):
        pass  # Graceful degradation if the SDK changes its internals


def _force_close_async_httpx(client: Any) -> None:
    """Mark the httpx AsyncClient inside an AsyncOpenAI client as closed so ``__del__`` won't
    schedule ``aclose()`` on a dead loop. Skips the full async close — the OS drops connections."""
    with contextlib.suppress(Exception):
        from httpx._client import ClientState
        inner = getattr(client, "_client", None)
        if inner is not None and not getattr(inner, "is_closed", True):
            inner._state = ClientState.CLOSED


def _schedule_async_close(close_result: Any, client: Any) -> None:
    """Finish an async close without leaking an unawaited coroutine."""
    async def _await_close() -> None:
        try:
            await close_result
        except Exception:
            pass
        finally:
            _force_close_async_httpx(client)
    runner = _await_close()
    try:
        import asyncio as _aio
        try:
            loop = _aio.get_running_loop()
        except RuntimeError:
            _aio.run(runner)
        else:
            task = loop.create_task(runner)

            def _consume(completed_task) -> None:
                with contextlib.suppress(BaseException):
                    completed_task.exception()
            task.add_done_callback(_consume)
            runner = None
    except Exception:
        if runner is not None:
            with contextlib.suppress(Exception):
                runner.close()
        _force_close_async_httpx(client)


def _close_cached_client(client: Any, *, close_async: bool = False) -> None:
    """Close one cached client, awaiting async transports only when safe."""
    if client is None:
        return
    close_fn = getattr(client, "close", None)
    if not callable(close_fn):
        _force_close_async_httpx(client)
        return
    try:
        close_result = close_fn()
    except Exception:
        _force_close_async_httpx(client)
        return
    if inspect.isawaitable(close_result):
        if close_async:
            _schedule_async_close(close_result, client)
        else:
            # Never await a client owned by another live loop; close the coroutine (no
            # unawaited warning) and neuter the transport.
            with contextlib.suppress(Exception):
                close_result.close()
            _force_close_async_httpx(client)
        return
    _force_close_async_httpx(client)


def shutdown_cached_clients() -> None:
    """Close all cached clients; call at CLI shutdown *before* the loop closes.

    Snapshot+clear under the lock, close outside it: async teardown can block while an owner
    loop drains, and holding the lock would convoy every caller.
    """
    with _client_cache_lock:
        clients = [(entry[0], entry[2]) for entry in _aux._client_cache.values() if entry[0] is not None]
        _aux._client_cache.clear()
    try:
        import asyncio as _aio
        running_loop = _aio.get_running_loop()
    except RuntimeError:
        running_loop = None
    for client, owner_loop in clients:
        # A live foreign loop owns its transport — neuter only and let it finish teardown.
        # Closed loops and the current loop are safe to drain here.
        close_async = owner_loop is not None and (owner_loop.is_closed() or owner_loop is running_loop)
        _close_cached_client(client, close_async=close_async)


def cleanup_stale_async_clients() -> None:
    """Force-close cached async clients whose loop is closed; call after each agent turn
    (defense-in-depth behind ``neuter_async_httpx_del``)."""
    with _client_cache_lock:
        stale = [(key, entry[0]) for key, entry in _aux._client_cache.items() if entry[2] is not None and entry[2].is_closed()]
        for key, _client in stale:
            del _aux._client_cache[key]
    for _key, client in stale:
        _close_cached_client(client, close_async=True)


def _compat_model(client: Any, model: Optional[str], cached_default: Optional[str]) -> Optional[str]:
    """Keep slash-bearing model IDs only for cached clients that accept ``vendor/model`` (OpenRouter
    or a slash-bearing default). Mirrors the resolve_provider_client() guard, which cache hits skip."""
    if model and "/" in model:
        accepts_slash = any(
            obj and _aux.base_url_host_matches(str(getattr(obj, "base_url", "") or ""), "openrouter.ai")
            for obj in (client, getattr(client, "_client", None), getattr(client, "client", None))
        ) or bool(cached_default and "/" in cached_default)
        if not accepts_slash:
            return cached_default
    return model or cached_default


def _get_cached_client(
    provider: str, model: str = None, async_mode: bool = False, base_url: str = None,
    api_key: str = None, api_mode: str = None, main_runtime: Optional[Dict[str, Any]] = None,
    is_vision: bool = False, task: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Get or create a cached client for the given provider.

    Async clients bind to the loop they were created on, so every async hit validates the cached
    loop is the current, open loop; stale entries are replaced in place (bounded, no cross-loop reuse).

    This keeps cache size bounded to one entry per unique provider config, preventing the fd-exhaustion that
    previously occurred in long-running gateways where recycled worker threads created unbounded entries
    (#10200).
    """
    current_loop = _current_event_loop() if async_mode else None
    runtime = _aux._normalize_main_runtime(main_runtime)
    cache_key = _client_cache_key(
        provider, async_mode=async_mode, base_url=base_url, api_key=api_key, api_mode=api_mode,
        main_runtime=main_runtime, is_vision=is_vision, task=task, model=model,
    )
    with _client_cache_lock:
        if cache_key in _aux._client_cache:
            cached_client, cached_default, cached_loop = _aux._client_cache[cache_key]
            loop_ok = not async_mode or (
                cached_loop is not None and cached_loop is current_loop and not cached_loop.is_closed()
            )
            if loop_ok:
                return cached_client, _compat_model(cached_client, model, cached_default)
            # Stale async entry — evict. Only a closed owner loop may be awaited here; a live
            # foreign loop stays force-neutered.
            _close_cached_client(cached_client, close_async=cached_loop is not None and cached_loop.is_closed())
            del _aux._client_cache[cache_key]
    # Build outside the lock. For pool-backed providers derive the key from the pool entry:
    # resolve_api_key_provider_credentials prefers env vars, which would bypass pool rotation
    # and retry an exhausted key.
    effective_api_key = api_key
    if not effective_api_key:
        _pe = _aux._peek_pool_entry(_aux._normalize_aux_provider(provider))
        if _pe is not None:
            effective_api_key = _aux._pool_runtime_api_key(_pe) or api_key
    client, default_model = _aux.resolve_provider_client(
        provider, model, async_mode, explicit_base_url=base_url, explicit_api_key=effective_api_key,
        api_mode=api_mode, main_runtime=runtime, is_vision=is_vision, task=task,
    )
    if client is not None and _aux._aux_probe_active():
        # Availability probes answer "resolvable?" and must leave the cache untouched: the
        # probe stub (bare, or wrapped in a Codex/Anthropic adapter whose leaf is the stub)
        # shares the runtime key, and a cached one is served to every later caller — the
        # next probe dies in _compat_model() on stub attribute access, so check_fns flip to
        # False and vision tools vanish for the process lifetime (#87654).
        return client, model or default_model
    if client is not None:
        with _client_cache_lock:
            if cache_key not in _aux._client_cache:
                # FIFO safety-belt eviction. Do NOT close evicted clients: another caller may be
                # mid-request on one; refcount/GC handles it.
                while len(_aux._client_cache) >= _CLIENT_CACHE_MAX_SIZE:
                    del _aux._client_cache[next(iter(_aux._client_cache))]
                _aux._client_cache[cache_key] = (client, default_model, current_loop)
            else:
                built_client = client
                client, default_model, _ = _aux._client_cache[cache_key]
                # Race loser was never exposed to a caller — safe to close now.
                _close_cached_client(built_client, close_async=async_mode)
    return client, _compat_model(client, model, default_model)


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
