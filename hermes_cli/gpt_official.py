"""Read-only ChatGPT subscription credentials owned/refreshed by the original client.

Never import refresh/id tokens into Stardust auth stores or credential pools.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

PROVIDER = "gpt-official"
LABEL = "GPT官方"
DEFAULT_MODEL = "gpt-6-luna"


def _access_fields(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if k in {"tokens", "access_token", "account_id"}}


def read_credentials() -> dict[str, str]:
    """Re-read external access credentials; no refresh, copying, or write-back."""
    from hermes_cli.auth import AuthError
    try:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        data = json.loads((home / "auth.json").read_text(encoding="utf-8"), object_hook=_access_fields)
        fields = data.get("tokens") or data
        access = fields.get("access_token")
        if not isinstance(access, str) or not access:
            raise ValueError()
        payload = access.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if float(claims["exp"]) <= time.time():
            raise ValueError()
        account = fields.get("account_id") or claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        if not isinstance(account, str) or not account:
            raise ValueError()
        return {"access_token": access, "account_id": account}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise AuthError("GPT官方登录不可用或已过期，请在原 ChatGPT/Codex 客户端重新登录；星尘不会刷新凭据。") from None


def _http_client():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(15, connect=8), follow_redirects=False)


def request_headers(creds: dict[str, str]) -> dict[str, str]:
    from agent.codex_headers import codex_cloudflare_headers
    headers = codex_cloudflare_headers(creds["access_token"])
    headers["ChatGPT-Account-ID"] = creds["account_id"]
    return headers


def model_catalog() -> dict:
    """Read the original client's catalog, not the legacy 0.0.0 live directory.

    Desktop visibility is not public-API support or proof of entitlement. The
    opaque cache identity has no documented account mapping; never claim binding.
    """
    from datetime import datetime, timezone
    from hermes_cli.auth import AuthError
    read_credentials()
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            raise ValueError()
        fetched_at = data["fetched_at"]
        version = data["client_version"]
        if not isinstance(fetched_at, str) or not isinstance(version, str) or not version:
            raise ValueError()
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if fetched.tzinfo is None or fetched.timestamp() > time.time() + 300:
            raise ValueError()
        names = {}
        for row in data["models"]:
            if not isinstance(row, dict) or not isinstance(row.get("slug"), str) or not row["slug"].strip():
                raise ValueError()
            if row.get("visibility") not in {"list", "hide", "hidden"}:
                raise ValueError()
            if row["visibility"] != "list":
                continue
            name = row.get("display_name", row["slug"])
            if not isinstance(name, str) or not name:
                raise ValueError()
            names.setdefault(row["slug"], name)
        if not names:
            raise ValueError()
        models = list(names)
        if DEFAULT_MODEL in models:
            models.remove(DEFAULT_MODEL)
            models.insert(0, DEFAULT_MODEL)
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        return {"models": models, "model_names": names,
                "source": "codex-models-cache-readonly", "fetched_at": fetched_at,
                "client_version": version, "account_binding": "unverified",
                "catalog_stale": age > 86400}
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        raise AuthError("GPT官方本地官方模型目录缺失或无效，请在原 ChatGPT/Codex 客户端更新目录；未使用静态列表或其他服务。") from None


def model_ids() -> list[str]:
    return model_catalog()["models"]



def bind_readonly_client(client):
    """Re-read external access/account at each SDK request, including cached aux clients."""
    create = client.responses.create

    def readonly_create(**kwargs):
        creds = read_credentials()
        client.api_key = creds["access_token"]
        # Request headers win over stale cached client headers and caller identity overrides.
        kwargs["extra_headers"] = {**(kwargs.get("extra_headers") or {}),
                                   **request_headers(creds),
                                   "Authorization": "Bearer " + creds["access_token"]}
        return create(**kwargs)

    client.responses.create = readonly_create
    return client


def prepare_request(agent) -> None:
    """Adopt access credentials refreshed externally before every direct request."""
    creds = read_credentials()
    headers = request_headers(creds)
    if agent.api_key == creds["access_token"] and agent._client_kwargs.get("default_headers") == headers:
        return
    agent.api_key = creds["access_token"]
    agent._client_kwargs.update(api_key=creds["access_token"], default_headers=headers)
    if not agent._replace_primary_openai_client(reason="gpt_official_external_access"):
        from hermes_cli.auth import AuthError
        raise AuthError("GPT官方客户端凭据更新失败；未使用旧凭据或其他服务。")


