"""Transient-transport retry count + per-model client-cache isolation.

Two related hardening behaviors for auxiliary calls (which include MoA
reference advisors, a pinned-model path where provider fallback is not a
meaningful recovery):

1. A transient transport blip (connection reset / timeout / 5xx) is retried
   on the SAME provider several times with backoff before giving up — a single
   upstream blip should not silently lose a pinned auxiliary call (root of the
   run2 double-advisor "Connection error" collapse).
2. Two auxiliary calls to the same provider/base_url/key but DIFFERENT models
   get DISTINCT client-cache keys, so a concurrent fan-out (e.g. opus + gpt-5.5
   advisors) never shares one client entry.
"""

from __future__ import annotations

import os
import types
from unittest.mock import patch

import pytest




def test_transient_retry_count_default(monkeypatch):
    from agent import auxiliary_client as ac

    # No config value -> default.
    monkeypatch.setattr(ac, "load_config", lambda: {}, raising=False)
    with patch("hermes_cli.config.load_config", return_value={}), \
         patch("hermes_cli.config.cfg_get", return_value=None):
        assert ac._transient_retry_count() == ac._DEFAULT_TRANSIENT_RETRIES




class _StatusError(Exception):
    def __init__(self, message, status_code, body):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def test_relay_model_unavailable_503_skips_same_provider_retry():
    """A relay's 503 "no channel serves this model" is deterministic for the route: the
    auxiliary call goes straight to fallback instead of spending its same-provider retries,
    while an ordinary 503 blip keeps them."""
    from agent import auxiliary_client as ac

    unservable = _StatusError(
        "Error code: 503", 503,
        {"code": "model_not_found", "message": "No available channel for model m under group g (distributor)"},
    )
    assert ac._is_transient_transport_error(unservable) is True
    assert ac._should_retry_same_provider("title_generation", unservable, "") is False
    blip = _StatusError("Service Unavailable", 503, {})
    assert ac._should_retry_same_provider("title_generation", blip, "") is True


def test_model_participates_in_client_cache_key():
    """Same provider/base_url/key, different model -> different cache key.

    This is what stops two concurrent advisors from sharing (and racing on)
    one cached client entry."""
    from agent.auxiliary_client import _client_cache_key

    k_opus = _client_cache_key(
        "openrouter", async_mode=False, base_url="https://openrouter.ai/api/v1",
        api_key="K", model="anthropic/claude-opus-4.8",
    )
    k_gpt = _client_cache_key(
        "openrouter", async_mode=False, base_url="https://openrouter.ai/api/v1",
        api_key="K", model="openai/gpt-5.5",
    )
    assert k_opus != k_gpt
    # Same model still collides (cache still works for reuse).
    k_opus2 = _client_cache_key(
        "openrouter", async_mode=False, base_url="https://openrouter.ai/api/v1",
        api_key="K", model="anthropic/claude-opus-4.8",
    )
    assert k_opus == k_opus2


