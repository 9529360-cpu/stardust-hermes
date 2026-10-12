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


def test_catalog_local_official_visible_models_and_provenance(tmp_path, monkeypatch):
    path = auth_file(tmp_path, monkeypatch)
    before = path.read_bytes()
    mod = module()
    slugs = ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
             "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
    cache = {"fetched_at": "2026-10-12T06:21:55.249793500Z", "client_version": "0.162.0",
             "identity": "opaque-client-identity", "models": [
                 {"slug": slug, "display_name": slug.upper(), "visibility": "list",
                  "supported_in_api": False} for slug in slugs] + [
                 {"slug": "hidden", "visibility": "hide"}]}
    (path.parent / "models_cache.json").write_text(json.dumps(cache))
    monkeypatch.setattr(mod, "_http_client", lambda: pytest.fail("legacy live URL must not mask official cache"))
    assert mod.model_ids() == ["gpt-6-luna"] + [s for s in slugs if s != "gpt-6-luna"]
    catalog = mod.model_catalog()
    assert catalog["source"] == "codex-models-cache-readonly"
    assert catalog["fetched_at"] == cache["fetched_at"]
    assert catalog["client_version"] == cache["client_version"]
    assert catalog["model_names"]["gpt-6-luna"] == "GPT-6-LUNA"
    assert catalog["account_binding"] == "unverified"
    assert path.read_bytes() == before
    assert json.loads((path.parent / "models_cache.json").read_text()) == cache



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
    monkeypatch.setattr(mod, "model_catalog", lambda: {"models": ["gpt-6-luna", "gpt-6-astra", "account-only"], "model_names": {}, "source": "codex-models-cache-readonly", "fetched_at": "fixture", "client_version": "fixture", "account_binding": "unverified"})
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
    assert official[0]["models"] == ["gpt-6-luna", "gpt-6-astra", "account-only"]
    assert official[0]["total_models"] == 3
    from hermes_cli.runtime_provider import resolve_runtime_provider
    rt = resolve_runtime_provider(requested="gpt-official", target_model="gpt-6-luna")
    assert rt["provider"] == "gpt-official"
    assert rt["api_mode"] == "codex_responses"
    assert rt["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert rt["api_key"] == mod.read_credentials()["access_token"]
    assert not rt.get("credential_pool")
    assert "refresh_token" not in rt
    from hermes_cli.models import get_default_model_for_provider, provider_model_ids
    assert get_default_model_for_provider("gpt-official") == "gpt-6-luna"
    assert provider_model_ids("gpt-official") == official[0]["models"]
    from hermes_cli.providers import get_label, get_provider
    assert get_label("gpt-official") == "GPT官方"
    assert get_provider("gpt-official").auth_type == "external_access_readonly"
    from hermes_cli.auth import resolve_provider
    assert resolve_provider("gpt-official") == "gpt-official"
    from agent.auxiliary_client import resolve_provider_client
    client, model = resolve_provider_client("gpt-official", model="gpt-6-luna", raw_codex=True)
    assert client is not None and model == "gpt-6-luna"
    client.close()
    client, model = resolve_provider_client("gpt-official", raw_codex=True)
    assert model == "gpt-6-luna"
    client.close()


def test_switch_manual_models_persists_without_credentials(tmp_path, monkeypatch):
    import yaml
    auth_file(tmp_path, monkeypatch)
    mod = module()
    monkeypatch.setattr(mod, "model_ids", lambda: ["gpt-6-luna", "gpt-5.6-sol", "account-only"])
    from hermes_cli.models_validate import validate_requested_model
    assert validate_requested_model("account-only", "gpt-official")["recognized"]
    assert not validate_requested_model("gpt-6-astra", "gpt-official")["accepted"]
    from hermes_cli.model_switch import switch_model, persist_model_selection
    for model in ["gpt-5.6-sol", "gpt-6-luna", "account-only"]:
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
    kwargs = transport.build_kwargs("gpt-6-luna", [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read_file", "description": "read",
               "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}],
        instructions="Stardust", provider="gpt-official",
        base_url="https://chatgpt.com/backend-api/codex", is_codex_backend=True)
    kwargs = transport.preflight_kwargs(kwargs, allow_stream=True, sanitize_harmony_tokens=True)
    assert kwargs["model"] == "gpt-6-luna" and kwargs["store"] is False
    assert kwargs["tools"][0]["name"] == "read_file"
    from agent.codex_responses_adapter import classify_responses_route
    from types import SimpleNamespace
    assert classify_responses_route(SimpleNamespace(provider="gpt-official", base_url="https://chatgpt.com/backend-api/codex"))[0]
    from agent.agent_init import _resolve_api_mode
    agent = SimpleNamespace(provider="gpt-official", _base_url_hostname="chatgpt.com",
                            _base_url_lower="https://chatgpt.com/backend-api/codex")
    _resolve_api_mode(agent, None, "gpt-official", agent._base_url_lower)
    assert agent.api_mode == "codex_responses"


def test_cached_aux_request_rereads_account_and_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = auth_file(tmp_path, monkeypatch)
    mod = module()
    seen = []
    client = SimpleNamespace(api_key="old", responses=SimpleNamespace(create=lambda **kw: seen.append(kw)))
    mod.bind_readonly_client(client)
    client.responses.create(extra_headers={"originator": "fake"})
    replacement = token(account="replacement")
    path.write_text(json.dumps({"tokens": {"access_token": replacement, "account_id": "replacement"}}))
    client.responses.create()
    assert client.api_key == replacement
    assert seen[-1]["extra_headers"]["ChatGPT-Account-ID"] == "replacement"
    assert seen[0]["extra_headers"]["originator"] == "hermes-agent"
    path.unlink()
    from hermes_cli.auth import AuthError
    with pytest.raises(AuthError):
        client.responses.create()
    assert len(seen) == 2


@pytest.mark.parametrize("async_mode", [False, True])
def test_aux_errors_never_enter_fallback(monkeypatch, async_mode):
    import asyncio
    from types import SimpleNamespace
    from agent import auxiliary_client as aux
    req = SimpleNamespace(client=object(), kwargs={}, request_provider="gpt-official",
                          resolved_api_mode="codex_responses", base_info="", resolved_base_url="")
    monkeypatch.setattr(aux, "_plan_aux_call", lambda *a, **kw: (req, {}, {}))
    monkeypatch.setattr(aux, "_should_retry_same_provider", lambda *a: False)
    monkeypatch.setattr(aux, "_provider_requires_stream", lambda *a: False)
    def fail(*a, **kw):
        raise ValueError("expected account failure")
    async def afail(*a, **kw):
        fail()
    monkeypatch.setattr(aux, "_relay_sync_completion", fail)
    monkeypatch.setattr(aux, "_relay_async_completion", afail)
    monkeypatch.setattr(aux, "_start_recovery_ladder", lambda *a, **kw: pytest.fail("fallback entered"))
    with pytest.raises(ValueError, match="expected account failure"):
        if async_mode:
            asyncio.run(aux._async_call_llm_impl(provider="gpt-official", messages=[]))
        else:
            aux.call_llm(provider="gpt-official", messages=[])


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





@pytest.mark.parametrize("payload", [None, {}, {"models": []},
    {"models": "bad", "fetched_at": "bad", "client_version": "0.162.0"}])
def test_catalog_failure_is_explicit_no_fallback(tmp_path, monkeypatch, payload):
    path = auth_file(tmp_path, monkeypatch)
    before = path.read_bytes()
    if payload is not None:
        (path.parent / "models_cache.json").write_text(json.dumps(payload))
    from hermes_cli.auth import AuthError
    with pytest.raises(AuthError, match="本地官方模型目录"):
        module().model_ids()
    assert path.read_bytes() == before
