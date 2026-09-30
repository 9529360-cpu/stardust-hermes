"""Harness: dashboard opt-in via HERMES_DASHBOARD.

Today (tini): dashboard starts once when HERMES_DASHBOARD=1; if it crashes
it stays dead. After Phase 2 (s6): dashboard starts once; if it crashes
it is restarted under supervision. The restart-after-crash test lives in
Phase 2 Task 2.5; this file only locks the opt-in surface (which must
not change between tini and s6).

Every ``docker exec`` here runs as the unprivileged ``hermes`` user
(via :func:`docker_exec`/:func:`docker_exec_sh` in conftest), matching
the realistic runtime context. See the conftest module docstring.
"""
from __future__ import annotations

import json
import time

from tests.docker.conftest import docker_exec, docker_exec_sh, start_container, poll_container


def test_dashboard_not_running_by_default(
    built_image: str, container_name: str,
) -> None:
    """Without HERMES_DASHBOARD, no dashboard process should be running."""
    start_container(built_image, container_name, cmd="sleep 60")
    r = docker_exec(container_name, "pgrep", "-f", "hermes dashboard")
    # pgrep exits non-zero when no match found
    assert r.returncode != 0, (
        "Dashboard should not be running without HERMES_DASHBOARD"
    )












# ---------------------------------------------------------------------------
# OAuth auth-gate behaviour — regression guard for the dashboard-insecure
# auto-injection bug. Pre-fix, the s6 run script appended `--insecure`
# whenever `HERMES_DASHBOARD_HOST` was non-loopback, silently disabling
# the OAuth gate on every container-deployed dashboard. The matching
# static-text guard lives in tests/test_docker_home_override_scripts.py;
# this is the behavioural end-to-end check.
# ---------------------------------------------------------------------------


def _http_probe(
    container: str,
    path: str,
    *,
    deadline_s: float = 60.0,
) -> tuple[int, str]:
    """Poll ``http://127.0.0.1:9119<path>`` from inside the container.

    Returns ``(status_code, body)`` as soon as the dashboard answers any
    HTTP response — 200, 401, 503, anything. The image doesn't ship
    ``curl`` but the venv's stdlib ``urllib`` is good enough; we use a
    proper ``try``/``except`` to intercept ``HTTPError`` because
    ``urlopen`` raises on 4xx/5xx, and we treat those as legitimate
    responses (the OAuth gate's 401 IS the success signal for the
    gate-engaged test).

    Connection errors (uvicorn still starting, fail-closed exited) keep
    the poll loop running until ``deadline_s`` elapses.

    The probe Python program is fed over stdin (``python -``) rather
    than ``python -c`` so we can use proper multi-line syntax with
    ``try``/``except`` blocks without escaping hell.

    Raises ``AssertionError`` on timeout.
    """
    py_program = f"""\
import urllib.request, urllib.error
req = urllib.request.Request("http://127.0.0.1:9119{path}")
try:
    r = urllib.request.urlopen(req, timeout=5)
    print(r.status)
    print(r.read().decode(), end="")
except urllib.error.HTTPError as h:
    print(h.code)
    print(h.read().decode(), end="")
"""
    # Feed the program over stdin via a heredoc so docker_exec_sh's
    # single bash string stays clean. The 'PY' delimiter is quoted to
    # disable shell expansion inside the heredoc body.
    probe = (
        "/opt/hermes/.venv/bin/python - <<'PY'\n"
        f"{py_program}"
        "PY"
    )
    end = time.monotonic() + deadline_s
    last_err = ""
    while time.monotonic() < end:
        r = docker_exec_sh(container, probe, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            lines = r.stdout.split("\n", 1)
            try:
                status = int(lines[0].strip())
                body = lines[1] if len(lines) > 1 else ""
                return status, body
            except (ValueError, IndexError) as exc:
                last_err = f"parse: {exc!r} / stdout={r.stdout!r}"
        else:
            last_err = f"rc={r.returncode} stderr={r.stderr!r}"
        time.sleep(0.5)
    raise AssertionError(
        f"Probe of {path} never returned HTTP within {deadline_s}s; "
        f"last error: {last_err}"
    )


def test_dashboard_auth_gate_engages_on_non_loopback_bind(
    built_image: str, container_name: str,
) -> None:
    """A public bind remains fail-closed behind a bundled Stardust auth provider.

    The test deliberately uses username/password rather than the retired Nous
    dashboard OAuth integration. This preserves the security invariant while
    proving Stardust no longer depends on an upstream account service to gate a
    public dashboard.
    """
    start_container(
        built_image, container_name,
        "HERMES_DASHBOARD=1",
        "HERMES_DASHBOARD_HOST=0.0.0.0",
        "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin",
        "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=test-dashboard-password",
        "HERMES_DASHBOARD_BASIC_AUTH_SECRET=0123456789abcdef0123456789abcdef",
        cmd="sleep 120",
    )

    # Provider registry is visible through the public bootstrap endpoint.
    status_code, body = _http_probe(container_name, "/api/auth/providers")
    assert status_code == 200, (
        f"/api/auth/providers should return 200 when a provider is "
        f"registered; got {status_code} body={body!r}"
    )
    payload = json.loads(body)
    provider_names = [p.get("name") for p in payload.get("providers", [])]
    assert "basic" in provider_names, (
        "Bundled dashboard_auth/basic provider should register from explicit "
        f"credentials. Got: {payload!r}"
    )
    assert "nous" not in provider_names

    # A protected route is intercepted by the auth gate.
    status_code, body = _http_probe(container_name, "/api/sessions")
    assert status_code == 401, (
        "Auth gate must intercept gated /api/* routes on 0.0.0.0. "
        f"Got: status={status_code} body={body!r}"
    )

    # Liveness stays public while the dashboard reports that auth is required.
    status_code, body = _http_probe(container_name, "/api/status")
    assert status_code == 200, (
        "/api/status must remain publicly reachable under the auth gate. "
        f"Got: status={status_code} body={body!r}"
    )
    status = json.loads(body)
    assert status.get("auth_required") is True, (
        "/api/status must report auth_required=True when the gate is engaged. "
        f"Got: {status!r}"
    )


def test_dashboard_insecure_env_var_no_longer_bypasses_gate(
    built_image: str, container_name: str,
) -> None:
    """``HERMES_DASHBOARD_INSECURE=1`` NO LONGER disables the auth gate
    (June 2026 hardening). With insecure set on a 0.0.0.0 bind and NO auth
    provider registered, start_server fails closed — the dashboard never
    binds, so ``/api/status`` is unreachable. This proves the unauthenticated
    public-dashboard escape hatch is gone: there is no env that serves the
    dashboard on a public bind without an auth provider.
    """
    start_container(
        built_image, container_name,
        "HERMES_DASHBOARD=1",
        "HERMES_DASHBOARD_HOST=0.0.0.0",
        "HERMES_DASHBOARD_INSECURE=1",
        cmd="sleep 120",
    )
    # Fail-closed: the dashboard process must NOT successfully serve. Probe
    # for a few seconds; /api/status should never become reachable because
    # start_server raised SystemExit before binding.
    ok, _ = poll_container(
        container_name,
        "curl -fsS -m 2 http://127.0.0.1:9119/api/status >/dev/null 2>&1",
        deadline_s=12.0,
    )
    assert not ok, (
        "Dashboard must NOT serve on a public bind with --insecure and no "
        "auth provider — the gate fails closed. /api/status became reachable, "
        "meaning the unauthenticated escape hatch is still open."
    )
