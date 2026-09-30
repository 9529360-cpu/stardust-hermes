"""Read-only personal-context inheritance for delegated children."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.tools.test_delegate import _make_mock_parent
from tools.delegate_tool import (
    _build_child_agent,
    _read_only_parent_memory_snapshot,
    _same_inference_privacy_boundary,
)


class _SnapshotStore:
    def __init__(self, user="USER SNAPSHOT", memory="MEMORY SNAPSHOT"):
        self.values = {"user": user, "memory": memory}

    def format_for_system_prompt(self, target):
        return self.values.get(target)


def _parent():
    return SimpleNamespace(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="anthropic/claude-sonnet-4",
        api_key="same-key",
        _fallback_chain=None,
        acp_command=None,
        acp_args=[],
        _memory_store=_SnapshotStore(),
        _memory_persistence_enabled=True,
        _memory_enabled=True,
        _user_profile_enabled=True,
        _credential_pool=None,
    )


def test_same_inference_boundary_inherits_frozen_snapshot():
    parent = _parent()
    runtime = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1/",
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }

    assert _same_inference_privacy_boundary(parent, runtime) is True
    snapshot = _read_only_parent_memory_snapshot(parent, runtime)

    assert snapshot == "USER SNAPSHOT\n\nMEMORY SNAPSHOT"


def test_same_canonical_fallback_chain_still_inherits_personal_context():
    parent = _parent()
    parent._fallback_chain = [
        {"provider": "openrouter", "model": "anthropic/claude-haiku-4.5"}
    ]
    runtime = {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "fallback_model": list(parent._fallback_chain),
        "acp_command": None,
        "acp_args": [],
    }

    assert _same_inference_privacy_boundary(parent, runtime) is True
    assert "USER SNAPSHOT" in _read_only_parent_memory_snapshot(parent, runtime)


def test_different_provider_or_endpoint_does_not_inherit_personal_context():
    parent = _parent()

    assert _read_only_parent_memory_snapshot(parent, {
        "provider": "custom",
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }) is None

    assert _read_only_parent_memory_snapshot(parent, {
        "provider": parent.provider,
        "base_url": "https://other.example/v1",
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }) is None


def test_different_model_or_credential_does_not_inherit_personal_context():
    parent = _parent()

    assert _read_only_parent_memory_snapshot(parent, {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": "google/gemini-2.5-pro",
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }) is None

    assert _read_only_parent_memory_snapshot(parent, {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": "different-key",
        "acp_command": None,
        "acp_args": [],
    }) is None


def test_different_fallback_or_request_routing_does_not_inherit_personal_context():
    parent = _parent()

    assert _read_only_parent_memory_snapshot(parent, {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "fallback_model": ["other-provider/other-model"],
        "acp_command": None,
        "acp_args": [],
    }) is None

    parent.request_overrides = {"extra_body": {"provider": {"sort": "price"}}}
    runtime = {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }
    assert _read_only_parent_memory_snapshot(
        parent,
        runtime,
        child_request_overrides={"extra_body": {"provider": {"sort": "throughput"}}},
    ) is None


def test_different_acp_transport_does_not_inherit_personal_context():
    parent = _parent()
    parent.acp_command = "parent-acp"
    parent.acp_args = ["--profile", "personal"]

    assert _read_only_parent_memory_snapshot(parent, {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": "parent-acp",
        "acp_args": ["--profile", "other"],
    }) is None


def test_child_gets_snapshot_as_read_only_prompt_but_memory_runtime_stays_disabled():
    parent = _make_mock_parent(depth=0)
    parent.acp_command = None
    parent.acp_args = []
    parent._fallback_chain = None
    parent.request_overrides = {}
    parent._memory_store = _SnapshotStore(
        user="USER PROFILE: prefers quiet restaurants",
        memory="MEMORY: usually avoids late appointments",
    )

    parent._credential_pool = None
    parent._memory_persistence_enabled = True
    parent._memory_enabled = True
    parent._user_profile_enabled = True

    with patch("tools.delegate_tool._load_config", return_value={}), patch(
        "tools.delegate_tool._resolve_child_credential_pool", return_value=None
    ), patch("run_agent.AIAgent") as mock_agent:
        mock_agent.return_value = MagicMock()
        _build_child_agent(
            task_index=0,
            goal="Find a suitable dinner reservation.",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            task_count=1,
            parent_agent=parent,
        )

    kwargs = mock_agent.call_args.kwargs
    prompt = kwargs["ephemeral_system_prompt"]

    assert "READ-ONLY USER CONTEXT FROM PARENT SESSION" in prompt
    assert "prefers quiet restaurants" in prompt
    assert "avoids late appointments" in prompt
    assert kwargs["skip_memory"] is True
    assert "memory" in kwargs["disabled_toolsets"]


def test_missing_or_broken_parent_store_is_non_blocking():
    parent = _parent()
    parent._memory_store = None
    runtime = {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }
    assert _read_only_parent_memory_snapshot(parent, runtime) is None

    broken = MagicMock()
    broken.format_for_system_prompt.side_effect = RuntimeError("broken snapshot")
    parent._memory_store = broken
    assert _read_only_parent_memory_snapshot(parent, runtime) is None

def test_disabled_parent_memory_surfaces_are_not_forwarded():
    parent = _parent()
    parent._memory_enabled = False
    runtime = {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }
    assert _read_only_parent_memory_snapshot(parent, runtime) == "USER SNAPSHOT"
    parent._user_profile_enabled = False
    assert _read_only_parent_memory_snapshot(parent, runtime) is None


def test_multi_credential_pool_fails_closed():
    class _Pool:
        def entries(self):
            return [object(), object()]

    parent = _parent()
    pool = _Pool()
    parent._credential_pool = pool
    runtime = {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
    }
    assert _read_only_parent_memory_snapshot(
        parent, runtime, child_credential_pool=pool
    ) is None


def test_different_provider_routing_filter_fails_closed():
    parent = _parent()
    parent.providers_allowed = ["Anthropic"]
    runtime = {
        "provider": parent.provider,
        "base_url": parent.base_url,
        "model": parent.model,
        "api_key": parent.api_key,
        "acp_command": None,
        "acp_args": [],
        "providers_allowed": ["OpenAI"],
    }
    assert _read_only_parent_memory_snapshot(parent, runtime) is None

