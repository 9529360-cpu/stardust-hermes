"""The recovery ladder after a failed auxiliary call: parameter, Nous and credential rungs,
provider fallback, the sync/async drivers and how a call path starts and steps it.

Split out of ``agent.auxiliary_client``, which re-exports every name here.
Names this module does not define are reached late-bound via ``_aux``, so
``patch("agent.auxiliary_client.<name>")`` keeps reaching every caller.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

logger = logging.getLogger("agent.auxiliary_client")  # log-record parity with the origin module


class _LadderStep(NamedTuple):
    """A provider request the ladder asks its driver to perform. kind: "call" (client, kwargs) |
    "retry_same_provider" (provider, model) | "fallback" (fb_client, fb_model, fb_label)."""
    kind: str
    args: tuple


def _rung(step: "_LadderStep", accept: Callable[[Exception], bool]):
    """One ladder rung: perform ``step``; yields ``(response, None)`` on success,
    ``(None, exc)`` when ``accept(exc)`` lets the next rung handle it, else re-raises."""
    try:
        result = yield step
    except Exception as exc:
        if not accept(exc):
            raise
        return None, exc
    return result, None


def _param_rung_accepts(exc: Exception) -> bool:
    """After a parameter-strip retry: fall through to the max_tokens/payment/auth
    chains with the stripped kwargs; re-raise anything those chains won't handle."""
    return (_aux._is_payment_error(exc) or _aux._is_connection_error(exc) or _aux._is_auth_error(exc)
            or "max_tokens" in str(exc) or "unsupported_parameter" in str(exc))


def _credential_rung_accepts(exc: Exception) -> bool:
    return _aux._is_auth_error(exc) or _aux._is_payment_error(exc) or _aux._is_rate_limit_error(exc)


# Immutable route context shared by the recovery rungs.
_LadderRoute = NamedTuple("_LadderRoute", [
    ("client", Any), ("task", Optional[str]), ("tag", str), ("async_mode", bool), ("base_info", str),
    ("resolved_provider", str), ("resolved_model", Optional[str]), ("resolved_base_url", Optional[str]),
    ("resolved_api_key", Optional[str]), ("resolved_api_mode", Optional[str]),
    ("final_model", Optional[str]), ("main_runtime", Optional[Dict[str, Any]]),
    ("route_info", Optional[Dict[str, str]]),
])


def _ladder_parameter_rungs(
    first_err: Exception, route: _LadderRoute, kwargs: Dict[str, Any], max_tokens: Optional[int],
):
    """Rungs 1-3: retry without temperature / structured-output format / max_tokens.
    Returns ``(response, None, kwargs)`` or ``(None, narrowed_err, stripped_kwargs)``."""
    client, task, tag = route.client, route.task, route.tag
    if "temperature" in kwargs and _aux._is_unsupported_parameter_error(first_err, "temperature"):
        retry_kwargs = {k: v for k, v in kwargs.items() if k != "temperature"}
        logger.info("Auxiliary %s%s: provider rejected temperature; retrying once without it",
                    task or "call", tag)
        resp, first_err = yield from _rung(
            _LadderStep("call", (client, retry_kwargs)), _param_rung_accepts)
        if first_err is None:
            return resp, None, retry_kwargs
        kwargs = retry_kwargs
    if _aux._is_structured_output_rejection(first_err):
        retry_kwargs = _aux._without_structured_output_format(kwargs)
        if retry_kwargs is not None:
            logger.info("Auxiliary %s%s: provider rejected the structured-output "
                        "format field; retrying once without it (schema "
                        "enforcement degrades to prompt compliance): %s", task or "call", tag, first_err)
            resp, first_err = yield from _rung(
                _LadderStep("call", (client, retry_kwargs)), _param_rung_accepts)
            if first_err is None:
                return resp, None, retry_kwargs
            kwargs = retry_kwargs
    err_str = str(first_err)
    # ZAI vision models reject max_tokens with code 1210 and a message that never
    # mentions "max_tokens", so detect it explicitly.
    _is_zai_param_error = "1210" in err_str and "bigmodel" in str(getattr(client, "base_url", ""))
    if max_tokens is not None and (
        "max_tokens" in err_str or "unsupported_parameter" in err_str
        or _aux._is_unsupported_parameter_error(first_err, "max_tokens") or _is_zai_param_error
    ):
        kwargs.pop("max_tokens", None)
        kwargs.pop("max_completion_tokens", None)
        resp, first_err = yield from _rung(
            _LadderStep("call", (client, kwargs)),
            lambda exc: _aux._is_payment_error(exc) or _aux._is_connection_error(exc) or _aux._is_rate_limit_error(exc),
        )
        if first_err is None:
            return resp, None, kwargs
    return None, first_err, kwargs


def _refreshed_nous_step(route: _LadderRoute, kwargs: Dict[str, Any], message: str) -> Optional[_LadderStep]:
    """Rebuild the Nous client after a credential event; None when nothing refreshed."""
    refreshed_client, refreshed_model = _aux._refresh_nous_auxiliary_client(
        cache_provider=route.resolved_provider or "nous", model=route.final_model,
        lookup_model=route.resolved_model, lookup_task=route.task, async_mode=route.async_mode,
        base_url=route.resolved_base_url, api_key=route.resolved_api_key,
        api_mode=route.resolved_api_mode, main_runtime=route.main_runtime,
        is_vision=(route.task == "vision"),
    )
    if refreshed_client is None:
        return None
    logger.info(message, route.task or "call", route.tag)
    if refreshed_model and refreshed_model != kwargs.get("model"):
        kwargs["model"] = refreshed_model
    return _LadderStep("call", (refreshed_client, kwargs))


def _ladder_nous_rungs(
    first_err: Exception, route: _LadderRoute, kwargs: Dict[str, Any], client_is_nous: bool,
):
    """Nous-only rungs: stale-model self-heal, paid-account refresh, 401 refresh.
    Returns ``(response, None)`` or ``(None, first_err)`` to fall through."""
    client, task, tag = route.client, route.task, route.tag
    # A long-lived process can pin a Portal model since dropped from the catalog (every call
    # 404s); force a fresh Portal fetch and retry once.
    if _aux._is_model_not_found_error(first_err) and client_is_nous:
        healed_model = _aux._refresh_nous_recommended_model(
            vision=(task == "vision"), stale_model=kwargs.get("model"))
        if healed_model and healed_model != kwargs.get("model"):
            logger.warning("Auxiliary %s%s: model %r no longer in Nous catalog; "
                           "retrying with refreshed recommendation %r",
                           task or "call", tag, kwargs.get("model"), healed_model)
            kwargs["model"] = healed_model
            resp, first_err = yield from _rung(_LadderStep("call", (client, kwargs)), lambda exc: True)
            if first_err is None:
                return resp, None
    # Auth refresh parity with the main agent.
    if _aux._is_payment_error(first_err) and client_is_nous and _aux._nous_portal_account_has_fresh_paid_access():
        step = _refreshed_nous_step(
            route, kwargs,
            "Auxiliary %s%s: refreshed Nous runtime credentials after paid account check, retrying")
        if step is not None:
            resp, first_err = yield from _rung(
                step, lambda exc: _credential_rung_accepts(exc) or _aux._is_connection_error(exc))
            if first_err is None:
                return resp, None
    if _aux._is_auth_error(first_err) and client_is_nous:
        step = _refreshed_nous_step(
            route, kwargs, "Auxiliary %s%s: refreshed Nous runtime credentials after 401, retrying")
        if step is not None:
            resp, first_err = yield from _rung(
                step, lambda exc: _credential_rung_accepts(exc) or _aux._is_connection_error(exc))
            if first_err is None:
                return resp, None
    return None, first_err


def _ladder_credential_rungs(
    first_err: Exception, route: _LadderRoute, kwargs: Dict[str, Any], client_is_nous: bool,
):
    """OAuth credential refresh + same-provider retry, then credential-pool rotation.
    Returns ``(response, None)`` or ``(None, first_err)`` to fall through."""
    client, task, tag, resolved_provider = route.client, route.task, route.tag, route.resolved_provider
    auth_refresh_provider = _aux._auth_refresh_provider_for_route(resolved_provider, route.base_info)
    if (_aux._is_auth_error(first_err) and auth_refresh_provider not in {"auto", "", None}
            and not client_is_nous):
        refresh_kwargs = ({"failed_api_key": getattr(client, "api_key", "")}
                          if auth_refresh_provider == "anthropic" else {})
        if _aux._refresh_provider_credentials(auth_refresh_provider, **refresh_kwargs):
            if auth_refresh_provider != _aux._normalize_aux_provider(resolved_provider):
                # The stale client is cached under the route label (e.g. "auto"), not the
                # concrete backend we refreshed.
                _aux._evict_cached_clients(resolved_provider)
            logger.info("Auxiliary %s%s: refreshed %s credentials after auth error, retrying",
                        task or "call", tag, auth_refresh_provider)
            step = _LadderStep(
                "retry_same_provider",
                (auth_refresh_provider, route.resolved_model or route.final_model))
            resp, first_err = yield from _rung(
                step, lambda exc: _credential_rung_accepts(exc) or _aux._is_connection_error(exc))
            if first_err is None:
                return resp, None
            # ``first_err`` is now the retry's own failure, not the original auth error: the
            # pool gate below and the ladder tail's eviction check both read this narrowed
            # value. An unclaimed failure (e.g. a 500) re-raised out of ``_rung`` above
            # instead, since the provider-fallback rung only acts on ``_FALLBACK_REASONS``.
    pool_provider = _aux._recoverable_pool_provider(resolved_provider, client, main_runtime=route.main_runtime)
    # Capture the exact key used so recovery finds the right pool entry even if another
    # process rotated the pool meanwhile (current() would be None).
    _client_api_key = str(getattr(client, "api_key", "") or "")
    # Gate on the narrowed error: a connection failure from the retry above arrives here
    # unaccepted on purpose (a fresh key cannot fix an unreachable endpoint), so rotation
    # is skipped and ``first_err`` is handed to the provider-fallback chain as-is.
    if pool_provider and _credential_rung_accepts(first_err):
        recovery_err = first_err
        # Skip the extra retry for clear payment/quota errors — the endpoint won't accept
        # another request with the same exhausted key.
        if _aux._is_rate_limit_error(first_err) and not _aux._is_payment_error(first_err):
            resp, recovery_err = yield from _rung(
                _LadderStep("call", (client, kwargs)), _credential_rung_accepts)
            if recovery_err is None:
                return resp, None
        if _aux._recover_provider_pool(pool_provider, recovery_err, failed_api_key=_client_api_key):
            logger.info("Auxiliary %s%s: recovered %s via credential-pool rotation after %s",
                        task or "call", tag, pool_provider, type(recovery_err).__name__)
            try:
                return (yield _LadderStep(
                    "retry_same_provider", (resolved_provider, route.resolved_model))), None
            except Exception as retry2_err:
                # Rotated key also hit a wall: mark it now so concurrent processes skip it,
                # then fall through to the provider fallback.
                if (_aux._is_payment_error(retry2_err) or _aux._is_auth_error(retry2_err)
                        or _aux._is_rate_limit_error(retry2_err)):
                    _aux._recover_provider_pool(pool_provider, retry2_err)
                    first_err = retry2_err
                else:
                    raise
    return None, first_err


def _next_fallback_after_quarantine(
    task: Optional[str], resolved_provider: str, is_auto: bool, route: _LadderRoute,
    failed_model: Optional[str], failure_scope: Any,
) -> Tuple[Optional[Any], Optional[str], str]:
    """Next candidate after a fallback entry was quarantined mid-request: remaining configured
    entries (task chain, then main chain on auto) before the discovery chain."""
    reason = "stale fallback credential"
    fb = _aux._try_configured_fallback_chain(
        task, resolved_provider or "auto", reason=reason, failed_model=failed_model,
        failed_base_url=route.base_info, failure_scope=failure_scope)
    if fb[0] is None and is_auto:
        fb = _aux._try_main_fallback_chain(
            task, resolved_provider or "auto", reason=reason, failed_model=failed_model,
            failed_base_url=route.base_info, failure_scope=failure_scope)
    if fb[0] is None:
        fb = _aux._try_payment_fallback(
            resolved_provider, task, reason=reason, failed_base_url=route.base_info,
            failure_scope=failure_scope, main_runtime=route.main_runtime)
    return fb


def _ladder_provider_fallback(first_err: Exception, route: _LadderRoute):
    """Last rung: other providers (per-task chain; then auto: main fallback chain + discovery
    chain, explicit: main-agent-model net). Returns the response or None.
    Capacity errors (payment/quota, connection, exhausted 429, model incompatible, malformed
    response) bypass the explicit-provider gate — the provider cannot serve this request
    regardless of user intent. Auth errors only fall back in auto mode."""
    task, tag, resolved_provider = route.task, route.tag, route.resolved_provider
    # Respect explicit provider choice for transient errors (auth, request validation, etc.) but allow
    # fallback when the provider clearly cannot serve the request due to capacity: payment/quota exhaustion
    # and connection failures are capacity problems, not request constraints. See #26803: daily token quota
    # (429 + "too many tokens per day") must fall back just like a 402 credit error.
    # Rate limits are included: after retries are exhausted, a 429 means the provider is at capacity. See
    # #52228. See #26803: daily token quota must fall back like a 402 credit error.
    is_auto = resolved_provider in {"auto", "", None}
    reason = next((label for predicate, label in _aux._FALLBACK_REASONS if predicate(first_err)), None)
    is_capacity_error = any(
        predicate(first_err) for predicate, label in _aux._FALLBACK_REASONS if label != "auth error")
    if reason is None or not (is_auto or is_capacity_error):
        return None
    if reason == "payment error":
        # Mark the concrete backend (not the "auto" label) unhealthy so later aux calls skip
        # it instead of paying another doomed RTT.
        _aux._mark_provider_unhealthy(
            _aux._recoverable_pool_provider(resolved_provider, route.client, main_runtime=route.main_runtime)
            or resolved_provider, base_url=route.base_info)
    logger.info("Auxiliary %s%s: %s on %s (%s), trying fallback",
                task or "call", tag, reason, resolved_provider, first_err)
    # Skip only the failed model for model-specific failures; 401/402 are provider-wide, so
    # auth keeps skipping the credential surface, while billing is scoped to the endpoint:
    # separate custom URLs can carry separate credentials (or no billing relationship at all).
    _chain_failed_model = None if reason in ("auth error", "payment error") else route.final_model
    from agent.backend_identity import FailureScope
    _chain_failure_scope = (
        FailureScope.ENDPOINT
        if reason == "payment error" and _aux._custom_health_base_url(resolved_provider, route.base_info)
        else None
    )
    fb_client, fb_model, fb_label = _aux._try_configured_fallback_chain(
        task, resolved_provider or "auto", reason=reason, failed_model=_chain_failed_model,
        failed_base_url=route.base_info, failure_scope=_chain_failure_scope)
    if fb_client is None and is_auto:
        fb_client, fb_model, fb_label = _aux._try_main_fallback_chain(
            task, resolved_provider or "auto", reason=reason, failed_model=_chain_failed_model,
            failed_base_url=route.base_info, failure_scope=_chain_failure_scope)
        if fb_client is None:
            fb_client, fb_model, fb_label = _aux._try_payment_fallback(
                resolved_provider, task, reason=reason, failed_base_url=route.base_info,
                failure_scope=_chain_failure_scope, main_runtime=route.main_runtime)
    elif fb_client is None:
        fb_client, fb_model, fb_label = _aux._try_main_agent_model_fallback(
            resolved_provider, task, reason=reason, failed_model=_chain_failed_model,
            failed_base_url=route.base_info, failure_scope=_chain_failure_scope)
    if fb_client is not None:
        # Second pass: the candidate credential was stale and quarantined — re-walk the CONFIGURED
        # chains first (the quarantined entry is now unhealthy and skipped, so later entries get
        # their turn), then discovery where the selection policy allows it.
        for _pass in range(2):
            _aux._record_route_info(route.route_info, _aux._fallback_provider_from_label(fb_label), fb_model)
            fb_resp = yield _LadderStep("fallback", (fb_client, fb_model, fb_label))
            if fb_resp is not None:
                return fb_resp
            if _pass == 0:
                fb_client, fb_model, fb_label = _next_fallback_after_quarantine(
                    task, resolved_provider, is_auto, route, _chain_failed_model, _chain_failure_scope)
                if fb_client is None:
                    break
    # All fallback layers exhausted — one user-visible warning, then re-raise.
    logger.warning("Auxiliary %s%s: %s on %s and all fallbacks exhausted "
                   # All fallback layers exhausted — emit a single user-visible warning so the operator
                   # knows aux task is about to fail. (#26882) The error itself is re-raised below.
                   # (#26882)
                   "(fallback_chain + main agent model). Raising the last error.",
                   task or "call", tag, reason, resolved_provider)
    return None


def _aux_recovery_ladder(
    first_err: Exception, *, client: Any, kwargs: Dict[str, Any], task: Optional[str],
    async_mode: bool, base_info: str, resolved_provider: str, resolved_model: Optional[str],
    resolved_base_url: Optional[str], resolved_api_key: Optional[str],
    resolved_api_mode: Optional[str], final_model: Optional[str], max_tokens: Optional[int],
    main_runtime: Optional[Dict[str, Any]], route_info: Optional[Dict[str, str]],
):
    """Ordered recovery rungs after the primary request failed (generator): parameter
    strips → Nous heal/refresh → credential refresh/pool rotation → provider fallback.
    Each rung returns a response, narrows ``first_err`` and falls through, or re-raises.
    Raises the narrowed ``first_err`` when exhausted (after evicting a connection-poisoned client)."""
    tag = " (async)" if async_mode else ""
    route = _LadderRoute(
        client, task, tag, async_mode, base_info, resolved_provider, resolved_model,
        resolved_base_url, resolved_api_key, resolved_api_mode, final_model, main_runtime, route_info)
    resp, first_err, kwargs = yield from _ladder_parameter_rungs(first_err, route, kwargs, max_tokens)
    if first_err is None:
        return resp
    client_is_nous = (resolved_provider == "nous"
                      or _aux.base_url_host_matches(base_info, "inference-api.nousresearch.com"))
    resp, first_err = yield from _ladder_nous_rungs(first_err, route, kwargs, client_is_nous)
    if first_err is None:
        return resp
    resp, first_err = yield from _ladder_credential_rungs(first_err, route, kwargs, client_is_nous)
    if first_err is None:
        return resp
    resp = yield from _aux._ladder_provider_fallback(first_err, route)
    if resp is not None:
        return resp
    # Connection/timeout errors poison the cached client (closed transport, half-read
    # stream); evict so the next aux call rebuilds a fresh one.
    # Reached only when no fallback answered, so the next auxiliary call rebuilds a fresh
    # client instead of reusing the dead one. ``first_err`` is the narrowed error from the
    # rungs above, not necessarily the original one. See issue #23432.
    # Mirror the sync path: drop poisoned clients on connection/timeout so the next aux call rebuilds. See
    # issue #23432.
    if _aux._is_connection_error(first_err):
        try:
            _aux._evict_cached_client_instance(client)
        except Exception:
            logger.debug("Auxiliary%s: cache eviction after connection error failed",
                         tag, exc_info=True)
    # The narrowed error is the actionable one (e.g. a 404 "requires credits" from the
    # retry after a healed 401), so surface it rather than the original.
    raise first_err


def _drive_ladder(ladder, perform: Callable[[_LadderStep], Any]) -> Any:
    """Run a ladder generator, feeding each step's result (or exception) back in."""
    try:
        step = next(ladder)
        while True:
            try:
                result = perform(step)
            except Exception as exc:
                step = ladder.throw(exc)
            else:
                step = ladder.send(result)
    except StopIteration as stop:
        return stop.value


async def _drive_ladder_async(ladder, perform: Callable[[_LadderStep], Any]) -> Any:
    """Async twin of :func:`_drive_ladder` (``perform`` is awaited)."""
    try:
        step = next(ladder)
        while True:
            try:
                result = await perform(step)
            except Exception as exc:
                step = ladder.throw(exc)
            else:
                step = ladder.send(result)
    except StopIteration as stop:
        return stop.value


def _ladder_step_call(
    step: _LadderStep, req: _aux._PreparedAuxRequest, retry_kwargs: Dict[str, Any], candidate_kwargs: Dict[str, Any],
) -> Tuple[str, tuple, Dict[str, Any]]:
    """Resolve a ladder step into ``(kind, args, kwargs)`` for the sync/async performer."""
    if step.kind == "call":
        return "call", step.args, dict(provider=req.resolved_provider, api_mode=req.resolved_api_mode)
    if step.kind == "retry_same_provider":
        retry_provider, retry_model = step.args
        return "retry", (), dict(retry_kwargs, resolved_provider=retry_provider, resolved_model=retry_model)
    return "fallback", step.args, candidate_kwargs


def _start_recovery_ladder(
    first_err: Exception, req: _aux._PreparedAuxRequest, retry_kwargs: Dict[str, Any], *,
    task: Optional[str], async_mode: bool, route_info: Optional[Dict[str, str]],
):
    """Build the recovery-ladder generator for a failed primary request."""
    return _aux_recovery_ladder(
        first_err, client=req.client, kwargs=req.kwargs, task=task, async_mode=async_mode,
        base_info=req.base_info, resolved_provider=req.resolved_provider,
        resolved_model=req.resolved_model, resolved_base_url=req.resolved_base_url,
        resolved_api_key=req.resolved_api_key, resolved_api_mode=req.resolved_api_mode,
        final_model=req.final_model, max_tokens=retry_kwargs["max_tokens"],
        main_runtime=retry_kwargs["main_runtime"], route_info=route_info)


# Late-bound origin namespace: imported LAST so this module is fully populated before
# ``auxiliary_client`` re-exports from it.
from agent import auxiliary_client as _aux  # noqa: E402
