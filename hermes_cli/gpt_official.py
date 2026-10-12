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
DEFAULT_MODEL = "gpt-5.6-luna"


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


def model_ids() -> list[str]:
    """Authoritative account catalog only. No synthetic models, disk cache or fallback."""
    from hermes_cli.auth import AuthError
    from agent.model_metadata import CODEX_MODELS_CATALOG_URL
    import httpx
    creds = read_credentials()
    try:
        with _http_client() as client:
            response = client.get(CODEX_MODELS_CATALOG_URL, headers={
                **request_headers(creds), "Authorization": "Bearer " + creds["access_token"]})
        if response.status_code in {401, 403}:
            raise AuthError("GPT官方需在原 ChatGPT/Codex 客户端重新登录或确认账户权限；星尘不会刷新凭据。")
        if response.status_code != 200:
            raise AuthError("GPT官方账户模型目录暂不可用；未切换到其他模型或服务。")
        data = response.json()
        rows = data["models"]
        if not isinstance(rows, list):
            raise ValueError()
        models = list(dict.fromkeys(row["slug"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("slug"), str) and row["slug"]))
        if DEFAULT_MODEL in models:
            models.remove(DEFAULT_MODEL)
            models.insert(0, DEFAULT_MODEL)
        return models
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise AuthError("GPT官方账户模型目录读取失败；未切换到其他模型或服务。") from None


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


