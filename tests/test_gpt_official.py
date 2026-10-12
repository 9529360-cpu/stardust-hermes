"""GPT official integration: fake external credentials, no login/refresh/inference."""
import base64
import importlib
import json
import time

import pytest


def token(exp=None, account="account-fixture"):
    payload = {"exp": exp or time.time() + 3600,
               "https://api.openai.com/auth": {"chatgpt_account_id": account}}
    return "eyJfixture." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".fixture"


def auth_file(tmp_path, monkeypatch, access=None):
    home = tmp_path / "codex"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = home / "auth.json"
    path.write_text(json.dumps({"tokens": {"access_token": access or token(),
                                          "refresh_token": "DO_NOT_IMPORT_REFRESH",
                                          "id_token": "DO_NOT_IMPORT_ID",
                                          "account_id": "account-fixture"}}))
    return path


def module():
    from hermes_cli import runtime_provider
    # Fail with an assertion (feature absent), not an import collection error.
    assert importlib.util.find_spec("hermes_cli.gpt_official") is not None
    return importlib.import_module("hermes_cli.gpt_official")


def test_external_access_is_readonly_and_reread(tmp_path, monkeypatch):
    access = token()
    path = auth_file(tmp_path, monkeypatch, access)
    before = path.read_bytes()
    mod = module()
    creds = mod.read_credentials()
    assert creds["access_token"] == access
    assert set(creds) == {"access_token", "account_id"}
    assert "DO_NOT_IMPORT" not in repr(creds)
    replacement = token(account="replacement")
    path.write_text(json.dumps({"tokens": {"access_token": replacement, "account_id": "replacement"}}))
    assert mod.read_credentials()["access_token"] == replacement
    path.write_bytes(before)
    assert path.read_bytes() == before


def test_catalog_only_live_models_luna_first_and_safe_errors(tmp_path, monkeypatch):
    import httpx
    path = auth_file(tmp_path, monkeypatch)
    before = path.read_bytes()
    mod = module()
    requests = []
    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["originator"] == "hermes-agent"
        assert request.headers["User-Agent"].startswith("HermesAgent/")
        assert request.headers["ChatGPT-Account-ID"] == "account-fixture"
        return httpx.Response(200, json={"models": [{"slug": "gpt-6-astra"},
            {"slug": "gpt-5.6-luna"}, {"slug": "account-only-hidden", "visibility": "hide"},
            {"slug": "gpt-6-astra"}]})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(mod, "_http_client", lambda: httpx.Client(transport=transport))
    assert mod.model_ids() == ["gpt-5.6-luna", "gpt-6-astra", "account-only-hidden"]
    assert len(requests) == 1
    assert path.read_bytes() == before
    monkeypatch.setattr(mod, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, text="DO_NOT_IMPORT_REFRESH"))))
    from hermes_cli.auth import AuthError
    with pytest.raises(AuthError) as caught:
        mod.model_ids()
    assert "原" in str(caught.value) and "DO_NOT_IMPORT" not in str(caught.value)


def test_expired_access_never_refreshes_or_leaks(tmp_path, monkeypatch):
    path = auth_file(tmp_path, monkeypatch, token(exp=1))
    before = path.read_bytes()
    mod = module()
    from hermes_cli.auth import AuthError
    with pytest.raises(AuthError, match="原 ChatGPT/Codex"):
        mod.read_credentials()
    assert path.read_bytes() == before


def test_picker_and_runtime_are_separate_from_siwc_and_keep_all_models(tmp_path, monkeypatch):
    auth_file(tmp_path, monkeypatch)
    mod = module()
    monkeypatch.setattr(mod, "model_ids", lambda: ["gpt-5.6-luna", "gpt-6-astra", "account-only"])
    import agent.models_dev as md
    monkeypatch.setattr(md, "fetch_models_dev", lambda **kw: {})
    from hermes_cli.inventory import ConfigContext, build_models_payload
    from hermes_cli import model_switch_providers as picker
    monkeypatch.setattr(picker, "_collect_authed_provider_slugs", lambda *a, **kw: [])
    monkeypatch.setattr(picker, "_lap_overlay_rows", lambda *a: None)
    monkeypatch.setattr(picker, "_lap_canonical_rows", lambda *a: None)
    ctx = ConfigContext("custom", "existing", "", {}, [])
    result = build_models_payload(ctx, explicit_only=True, max_models=1)
    official = [r for r in result["providers"] if r["slug"] == "gpt-official"]
    assert len(official) == 1
    assert official[0]["name"] == "GPT官方"
    assert official[0]["models"] == ["gpt-5.6-luna", "gpt-6-astra", "account-only"]
    assert official[0]["total_models"] == 3
    from hermes_cli.runtime_provider import resolve_runtime_provider
    rt = resolve_runtime_provider(requested="gpt-official", target_model="gpt-5.6-luna")
    assert rt["provider"] == "gpt-official"
    assert rt["api_mode"] == "codex_responses"
    assert rt["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert rt["api_key"] == mod.read_credentials()["access_token"]
    assert not rt.get("credential_pool")
    assert "refresh_token" not in rt
    from hermes_cli.models import get_default_model_for_provider, provider_model_ids
    assert get_default_model_for_provider("gpt-official") == "gpt-5.6-luna"
    assert provider_model_ids("gpt-official") == official[0]["models"]
    from hermes_cli.providers import get_label, get_provider
    assert get_label("gpt-official") == "GPT官方"
    assert get_provider("gpt-official").auth_type == "external_access_readonly"
    from hermes_cli.auth import resolve_provider
    assert resolve_provider("gpt-official") == "gpt-official"
    from agent.auxiliary_client import resolve_provider_client
    client, model = resolve_provider_client("gpt-official", model="gpt-5.6-luna", raw_codex=True)
    assert client is not None and model == "gpt-5.6-luna"
    client.close()
    client, model = resolve_provider_client("gpt-official", raw_codex=True)
    assert model == "gpt-5.6-luna"
    client.close()


def test_switch_manual_models_persists_without_credentials(tmp_path, monkeypatch):
    import yaml
    auth_file(tmp_path, monkeypatch)
    mod = module()
    monkeypatch.setattr(mod, "model_ids", lambda: ["gpt-5.6-luna", "gpt-5.6-sol", "account-only"])
    from hermes_cli.models_validate import validate_requested_model
    assert validate_requested_model("account-only", "gpt-official")["recognized"]
    assert not validate_requested_model("gpt-6-astra", "gpt-official")["accepted"]
    from hermes_cli.model_switch import switch_model, persist_model_selection
    for model in ["gpt-5.6-sol", "gpt-5.6-luna", "account-only"]:
        result = switch_model(model, current_provider="custom", current_model="old",
                              explicit_provider="gpt-official", is_global=True)
        assert result.success, result
        assert result.new_model == model and result.target_provider == "gpt-official"
        config = tmp_path / "config.yaml"
        config.write_text("model:\n  provider: custom\n  default: old\n  api_key: OLD_SECRET\n")
        persist_model_selection(result, config)
        text = config.read_text()
        saved = yaml.safe_load(text)["model"]
        assert saved["provider"] == "gpt-official" and saved["default"] == model
        assert "api_key" not in saved
        assert "fixture" not in text and "DO_NOT_IMPORT" not in text
        from hermes_cli.runtime_provider import resolve_runtime_provider
        assert resolve_runtime_provider(requested=saved["provider"], target_model=saved["default"])["provider"] == "gpt-official"


def test_runtime_fails_closed_no_override_no_fallback(tmp_path, monkeypatch):
    path = auth_file(tmp_path, monkeypatch)
    from hermes_cli.auth import AuthError
    from hermes_cli.runtime_provider import resolve_runtime_provider
    with pytest.raises(AuthError):
        resolve_runtime_provider(requested="gpt-official", explicit_base_url="https://evil.test")
    with pytest.raises(AuthError):
        resolve_runtime_provider(requested="gpt-official", explicit_api_key="OVERRIDE")
    path.unlink()
    with pytest.raises(AuthError, match="原 ChatGPT/Codex"):
        resolve_runtime_provider(requested="gpt-official")
    from types import SimpleNamespace
    from agent.chat_completion_helpers import try_activate_fallback
    agent = SimpleNamespace(provider="gpt-official")
    assert try_activate_fallback(agent) is False


def test_transport_tools_are_codex_compatible_and_identity_is_honest():
    from agent.transports.codex import ResponsesApiTransport
    transport = ResponsesApiTransport()
    kwargs = transport.build_kwargs("gpt-5.6-luna", [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read_file", "description": "read",
               "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}],
        instructions="Stardust", provider="gpt-official",
        base_url="https://chatgpt.com/backend-api/codex", is_codex_backend=True)
    kwargs = transport.preflight_kwargs(kwargs, allow_stream=True, sanitize_harmony_tokens=True)
    assert kwargs["model"] == "gpt-5.6-luna" and kwargs["store"] is False
    assert kwargs["tools"][0]["name"] == "read_file"
    from agent.codex_responses_adapter import classify_responses_route
    from types import SimpleNamespace
    assert classify_responses_route(SimpleNamespace(provider="gpt-official", base_url="https://chatgpt.com/backend-api/codex"))[0]
    from agent.agent_init import _resolve_api_mode
    agent = SimpleNamespace(provider="gpt-official", _base_url_hostname="chatgpt.com",
                            _base_url_lower="https://chatgpt.com/backend-api/codex")
    _resolve_api_mode(agent, None, "gpt-official", agent._base_url_lower)
    assert agent.api_mode == "codex_responses"


def test_request_boundary_rereads_external_access_without_refresh(tmp_path, monkeypatch):
    auth_file(tmp_path, monkeypatch)
    mod = module()
    from types import SimpleNamespace
    agent = SimpleNamespace(provider="gpt-official", api_key="old", _client_kwargs={"api_key": "old"},
        _replace_primary_openai_client=lambda **kw: True)
    assert hasattr(mod, "prepare_request")
    mod.prepare_request(agent)
    assert agent.api_key == mod.read_credentials()["access_token"]
    assert agent._client_kwargs["default_headers"]["ChatGPT-Account-ID"] == "account-fixture"
    from agent.client_lifecycle import ClientLifecycleMixin
    assert ClientLifecycleMixin._try_refresh_codex_client_credentials(SimpleNamespace(
        api_mode="codex_responses", provider="gpt-official")) is False




