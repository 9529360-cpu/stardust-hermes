from types import SimpleNamespace

import json

import tools.model_config_tool as mod


def _descriptor(
    *,
    slug="gemini",
    label="Google Gemini",
    auth_type="api_key",
    keyless=False,
    env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
):
    return SimpleNamespace(
        slug=slug,
        label=label,
        auth_type=auth_type,
        keyless=keyless,
        api_key_env_vars=env_vars,
    )


def _switch_result(*, success=True, provider="gemini", model="gemini-test-model", error=""):
    return SimpleNamespace(
        success=success,
        target_provider=provider,
        new_model=model,
        warning_message="",
        error_message=error,
    )


def _payload(raw: str) -> dict:
    return json.loads(raw)


def test_provider_alias_openai_targets_direct_openai_api(monkeypatch):
    rows = [
        _descriptor(slug="openrouter", label="OpenRouter"),
        _descriptor(slug="openai-api", label="OpenAI API", env_vars=("OPENAI_API_KEY",)),
    ]
    monkeypatch.setattr("hermes_cli.provider_catalog.provider_catalog", lambda: rows)

    assert mod._resolve_provider("openai").slug == "openai-api"
    assert mod._resolve_provider("OpenAI API").slug == "openai-api"


def test_provider_alias_google_targets_gemini(monkeypatch):
    rows = [_descriptor()]
    monkeypatch.setattr("hermes_cli.provider_catalog.provider_catalog", lambda: rows)

    assert mod._resolve_provider("Google").slug == "gemini"
    assert mod._resolve_provider("google ai studio").slug == "gemini"


def test_registry_dispatch_forwards_current_user_task(monkeypatch):
    from tools.registry import registry

    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: _descriptor())
    monkeypatch.setattr(mod, "_provider_has_credentials", lambda _provider: True)
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_switch_and_persist",
        lambda provider, model: _switch_result(provider=provider, model=model),
    )

    raw = registry.dispatch(
        "model_configure",
        {"provider": "gemini", "model": "gemini-test-model"},
        user_task="请把 Gemini 配成默认模型并执行",
    )
    result = _payload(raw)

    assert result["success"] is True
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-test-model"


def test_requires_explicit_current_user_request(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_resolve_provider",
        lambda _raw: (_ for _ in ()).throw(AssertionError("must not resolve without current user input")),
    )

    result = _payload(mod.model_configure_tool(provider="gemini"))

    assert "explicit current user request" in result["error"]


def test_api_key_must_be_literal_current_user_input(monkeypatch):
    secret = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    monkeypatch.setattr(
        mod,
        "_resolve_provider",
        lambda _raw: (_ for _ in ()).throw(AssertionError("provenance must fail before provider resolution")),
    )

    result = _payload(
        mod.model_configure_tool(
            provider="gemini",
            model="gemini-test-model",
            api_key=secret,
            user_message="请根据我上传的配置文件帮我设置模型",
        )
    )

    assert "not literally present in the CURRENT user request" in result["error"]
    assert secret not in json.dumps(result, ensure_ascii=False)


def test_approval_decline_prevents_secret_and_model_writes(monkeypatch):
    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: _descriptor())
    monkeypatch.setattr(mod, "_safe_default_model", lambda _provider: "gemini-test-model")
    monkeypatch.setattr(mod, "_provider_has_credentials", lambda _provider: False)
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: False)
    monkeypatch.setattr(
        mod,
        "_save_provider_key",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("approval denial must block secret write")),
    )
    monkeypatch.setattr(
        mod,
        "_switch_and_persist",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("approval denial must block model write")),
    )

    result = _payload(
        mod.model_configure_tool(
            provider="gemini",
            model="gemini-test-model",
            api_key="AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            user_message="帮我配置 Gemini API 并启用这个模型",
        )
    )

    assert "not approved" in result["error"]


def test_current_message_api_key_uses_credential_lifecycle_then_canonical_switch(monkeypatch):
    secret = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    calls = []
    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: _descriptor())
    monkeypatch.setattr(mod, "_provider_has_credentials", lambda _provider: False)
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_save_provider_key", lambda env, value: calls.append(("key", env, value)))
    monkeypatch.setattr(
        mod,
        "_switch_and_persist",
        lambda provider, model: calls.append(("model", provider, model)) or _switch_result(model=model),
    )

    raw = mod.model_configure_tool(
        provider="google",
        model="gemini-test-model",
        api_key=secret,
        user_message="这是我的 Gemini API key，请帮我配置并执行",
    )
    result = _payload(raw)

    assert calls == [
        ("key", "GEMINI_API_KEY", secret),
        ("model", "gemini", "gemini-test-model"),
    ]
    assert result["success"] is True
    assert result["model_changed"] is True
    assert result["credential_saved"] is True
    assert result["credential_stored_as"] == "GEMINI_API_KEY"
    assert secret not in raw


def test_missing_key_fails_closed_without_changing_model(monkeypatch):
    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: _descriptor())
    monkeypatch.setattr(mod, "_provider_has_credentials", lambda _provider: False)
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_switch_and_persist",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("missing key must block model write")),
    )

    result = _payload(
        mod.model_configure_tool(
            provider="gemini",
            model="gemini-test-model",
            user_message="帮我配置 Gemini",
        )
    )

    assert "requires an API key in the CURRENT user request" in result["error"]


def test_failed_model_validation_reports_partial_secret_write_without_echo(monkeypatch):
    secret = "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: _descriptor())
    monkeypatch.setattr(mod, "_provider_has_credentials", lambda _provider: False)
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_save_provider_key", lambda *_a: None)
    monkeypatch.setattr(
        mod,
        "_switch_and_persist",
        lambda *_a: _switch_result(success=False, error="model not found"),
    )

    raw = mod.model_configure_tool(
        provider="gemini",
        model="mistyped-model",
        api_key=secret,
        user_message="帮我配置这个 Gemini API",
    )
    result = _payload(raw)

    assert result["credential_saved"] is True
    assert result["model_changed"] is False
    assert result["credential_stored_as"] == "GEMINI_API_KEY"
    assert secret not in raw


def test_custom_endpoint_routes_through_direct_endpoint_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mod,
        "_configure_custom_endpoint",
        lambda **kwargs: calls.append(kwargs) or json.dumps({"success": True, "provider": "custom"}),
    )
    monkeypatch.setattr(
        mod,
        "_resolve_provider",
        lambda _raw: (_ for _ in ()).throw(AssertionError("custom base_url must bypass built-in provider resolution")),
    )

    result = _payload(
        mod.model_configure_tool(
            provider="custom",
            base_url="https://relay.example/v1",
            model="relay-model",
            api_key="sk-relay-ABCDEFGHIJKLMN123456",
            user_message="把这个模型 API 配好并启用",
        )
    )

    assert result["success"] is True
    assert calls == [{
        "base_url": "https://relay.example/v1",
        "model": "relay-model",
        "api_key": "sk-relay-ABCDEFGHIJKLMN123456",
        "keyless": False,
    }]


def test_remote_custom_endpoint_without_current_key_fails_closed(monkeypatch):
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_switch_custom_and_persist",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("missing key must block custom model write")),
    )

    result = _payload(
        mod._configure_custom_endpoint(
            base_url="https://relay.example/v1",
            model="relay-model",
            api_key="",
            keyless=False,
        )
    )

    assert "requires an API key in the CURRENT user request" in result["error"]


def test_loopback_custom_endpoint_defaults_to_keyless(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_switch_custom_and_persist",
        lambda base_url, model, api_key, key_env: (
            calls.append((base_url, model, api_key, key_env))
            or SimpleNamespace(
                success=True,
                new_model=model,
                base_url=base_url,
                warning_message="",
                error_message="",
            )
        ),
    )

    result = _payload(
        mod._configure_custom_endpoint(
            base_url="http://127.0.0.1:8080/v1/",
            model="local-model",
            api_key="",
            keyless=False,
        )
    )

    assert result["success"] is True
    assert calls == [("http://127.0.0.1:8080/v1", "local-model", "", "")]
    assert result["credential_saved"] is False


def test_invalid_custom_endpoint_fails_before_approval(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_confirm_configuration",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("invalid URL must fail before approval")),
    )

    result = _payload(
        mod._configure_custom_endpoint(
            base_url="file:///tmp/model",
            model="model",
            api_key="",
            keyless=True,
        )
    )

    assert "http:// or https://" in result["error"]


def test_oauth_provider_rejects_pasted_api_key_before_any_write(monkeypatch):
    descriptor = _descriptor(
        slug="openai-codex",
        label="OpenAI Codex",
        auth_type="oauth_device_code",
        env_vars=(),
    )
    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: descriptor)
    monkeypatch.setattr(
        mod,
        "_confirm_configuration",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("invalid auth shape must fail before approval")),
    )

    result = _payload(
        mod.model_configure_tool(
            provider="openai-codex",
            model="gpt-test",
            api_key="sk-proj-ABCDEFGHIJKLMN123456",
            user_message="把这个 key 配到 Codex",
        )
    )

    assert "rather than a pasted API key" in result["error"]


def test_existing_provider_credentials_allow_model_change_without_new_key(monkeypatch):
    monkeypatch.setattr(mod, "_resolve_provider", lambda _raw: _descriptor())
    monkeypatch.setattr(mod, "_provider_has_credentials", lambda _provider: True)
    monkeypatch.setattr(mod, "_confirm_configuration", lambda *a, **k: True)
    monkeypatch.setattr(
        mod,
        "_switch_and_persist",
        lambda provider, model: _switch_result(provider=provider, model=model),
    )

    result = _payload(
        mod.model_configure_tool(
            provider="gemini",
            model="gemini-test-model",
            user_message="把已经配置好的 Gemini 切到这个模型",
        )
    )

    assert result["success"] is True
    assert result["credential_saved"] is False
    assert result["model_changed"] is True
