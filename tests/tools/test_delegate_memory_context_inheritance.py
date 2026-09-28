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
        acp_command=None,
        acp_args=[],
        _memory_store=_SnapshotStore(),
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
    parent.fallback_model = None
    parent.request_overrides = {}
    parent._memory_store = _SnapshotStore(
        user="USER PROFILE: prefers quiet restaurants",
        memory="MEMORY: usually avoids late appointments",
    )

    with patch("tools.delegate_tool._load_config", return_value={}), patch(
        "run_agent.AIAgent"
    ) as mock_agent:
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
