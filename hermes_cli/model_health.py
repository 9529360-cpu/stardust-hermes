"""Small opt-in OpenAI-compatible health probe; never expose remote bodies or exceptions."""

import math
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


def redact(value, api_key):
    """Remove the resolved credential even from route/model metadata."""
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]") if api_key else value
    if isinstance(value, dict):
        return {redact(k, api_key): redact(v, api_key) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, api_key) for v in value]
    return value


class _NoRedirect(HTTPRedirectHandler):
    # Never forward authorization to another endpoint, even on same-host redirects.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_model_health(runtime, timeout_s=8):
    """Probe /models endpoint reachability only; never claim model inference health.

    ``ok`` at the RPC layer remains credential readiness; ``live_ok`` is reachability.
    Error messages are deliberately local constants: remote bodies, reason phrases,
    and exception text can all echo credentials (including encoded variants).
    """
    if (runtime.get("command") or runtime.get("provider") == "bedrock"
            or runtime.get("api_mode") in {"anthropic_messages", "bedrock_converse", "codex_app_server"}
            or (runtime.get("provider") == "anthropic" and not runtime.get("api_mode"))):
        return {"live_ok": None, "reason": "live probe unsupported for provider"}
    started = time.monotonic()

    def result(ok, kind=None, message=None):
        return {"live_ok": ok, "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error_kind": kind, "error": message,
                "probe_kind": "endpoint_reachability", "inference_ok": None,
                "reason": "HTTP reachability only; model inference was not tested."}

    try:
        timeout = float(timeout_s)
        if not math.isfinite(timeout) or timeout <= 0:
            return result(False, "unknown", "timeout_s must be a positive finite number.")
        timeout = min(timeout, 30)
        key = runtime.get("api_key")
        key = key() if callable(key) else key
        headers = dict(runtime.get("extra_headers") or {})
        if key and key not in {"no-key-required", "aws-sdk"}:
            headers["Authorization"] = f"Bearer {key}"
        base_url = str(runtime.get("base_url") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            return result(False, "unknown", "No HTTP base URL is configured.")
        opener = build_opener(_NoRedirect())

        def request(path, data=None):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError()
            req = Request(base_url + path, data=data, headers=headers)
            try:
                with opener.open(req, timeout=remaining) as response:
                    return response.status
            except HTTPError as exc:
                status = exc.code
                exc.close()
                return status

        status = request("/models")
        if 200 <= status < 300:
            return result(True)
        kind = ({401: "auth", 403: "auth", 404: "not_found", 429: "rate_limit"}.get(status)
                or ("server" if status >= 500 else "unknown"))
        return result(False, kind, f"Model health request failed (HTTP {status}).")
    except (TimeoutError, socket.timeout):
        return result(False, "timeout", "Model health request timed out.")
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return result(False, "timeout", "Model health request timed out.")
        return result(False, "network", "Could not connect to the model endpoint.")
    except OSError:
        return result(False, "network", "Could not connect to the model endpoint.")
    except Exception:
        return result(False, "unknown", "Model health request could not be completed.")
