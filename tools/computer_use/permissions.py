"""Cross-platform Computer Use readiness + macOS permission helpers. "Ready to drive" differs per platform: macOS
needs explicit TCC grants (Accessibility + Screen Recording) via cua-driver ``permissions status`` / ``permissions
grant``; Windows/Linux have no TCC toggles, so readiness == driver health. The grants attach to cua-driver's OWN
identity (``com.trycua.driver``), not Hermes, so ``grant`` launches CuaDriver via LaunchServices for correct dialog
attribution. ``cua-driver doctor --json`` is the universal signal; ``computer_use_status`` folds it with the macOS
detail into one payload for the desktop card, the ``permissions`` CLI and ``/api/tools/computer-use/status``."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import suppress
from typing import Any, Dict, Optional

from hermes_cli._subprocess_compat import windows_hide_flags

_RUNTIME_PLATFORMS = frozenset({"darwin", "win32", "linux"})  # mirrors the toolset platform_gate
_BOOLS = ("accessibility", "screen_recording", "screen_recording_capturable")

def _child_env() -> Dict[str, str]:
    """cua-driver child env (telemetry policy + provider secrets stripped).

    cua-driver is a third-party binary — it must never inherit provider API keys (#53503/#55709/#58889
    lineage). If the sanitizer is unavailable, fail closed and do not launch the driver.
    """
    from tools.computer_use.cua_backend import sanitized_cua_driver_env
    return sanitized_cua_driver_env()

def _run(binary: str, *args: str, timeout: float, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run([binary, *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, env=_child_env() if env is None else env, stdin=subprocess.DEVNULL,
                          creationflags=windows_hide_flags())

def _json_out(binary: str, *args: str, timeout: float, env: Optional[Dict[str, str]] = None) -> Any:
    """Run ``binary args`` and parse stdout as JSON (``None`` on empty output)."""
    raw = (_run(binary, *args, timeout=timeout, env=env).stdout or "").strip()
    return json.loads(raw) if raw else None

def _doctor(binary: str, env: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """``cua-driver doctor --json`` → ``{ok, checks:[{label,status,message}]}`` (None on any failure)."""
    try:
        data = _json_out(binary, "doctor", "--json", timeout=12, env=env)
    except Exception:
        data = None
    if not isinstance(data, dict):
        return None
    checks = [{k: str(p.get(k, "")) for k in ("label", "status", "message")} for p in data.get("probes", []) if isinstance(p, dict)]
    return {"ok": bool(data.get("ok")), "checks": checks}

def _mac_permissions(binary: str, out: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> None:
    """Fold ``cua-driver permissions status --json`` booleans (+ ``source``) into ``out``."""
    try:
        data = _json_out(binary, "permissions", "status", "--json", timeout=10, env=env)
    except subprocess.TimeoutExpired:
        out["error"] = "cua-driver permissions status timed out"
    except Exception as exc:  # spawn failure or malformed JSON
        out["error"] = f"cua-driver permissions status failed: {exc}"
    else:
        if isinstance(data, dict):
            out.update({k: data[k] for k in _BOOLS if isinstance(data.get(k), bool)})
            if isinstance(data.get("source"), dict):
                out["source"] = data["source"]

def computer_use_status(driver_cmd: Optional[str] = None) -> Dict[str, Any]:
    """OS-aware readiness for the desktop card; key order is an API payload contract. ``ready`` is the single signal the
    UI keys off: macOS = both TCC grants, elsewhere = driver health (no TCC model); ``None`` = unknown (binary missing /
    probe failed). ``can_grant`` is macOS-only."""
    from tools.computer_use.cua_backend_driver import resolve_cua_driver_cmd  # same resolver as the tool itself
    plat, binary = sys.platform, resolve_cua_driver_cmd(driver_cmd)
    out: Dict[str, Any] = {"platform": plat, "platform_supported": plat in _RUNTIME_PLATFORMS,
                           "installed": bool(binary), "version": None, "ready": None, "can_grant": plat == "darwin",
                           "checks": [], "source": None, "error": None, **{k: None for k in _BOOLS}}
    if not binary:
        return out
    try:
        child_env = _child_env()
    except Exception:
        out["error"] = "Could not prepare a sanitized environment; cua-driver was not launched"
        return out
    with suppress(Exception):
        out["version"] = (_run(binary, "--version", timeout=5, env=child_env).stdout or "").strip() or None
    doctor = _doctor(binary, child_env)
    if doctor is not None:
        out["checks"] = doctor["checks"]
    if plat == "darwin":
        _mac_permissions(binary, out, child_env)
        if out["error"] is None:
            out["ready"] = out["accessibility"] is True and out["screen_recording"] is True
    elif doctor is not None:
        out["ready"] = doctor["ok"]  # no TCC model off macOS
    return out

def request_permissions_grant(driver_cmd: Optional[str] = None) -> int:
    """Run ``cua-driver permissions grant`` (macOS), streaming its output. Returns the driver's exit code (0 ok), 2 if
    the binary is missing, 64 on a non-macOS platform (no TCC model to grant)."""
    if sys.platform != "darwin":
        print("Computer Use permissions are a macOS concept; nothing to grant here.")
        return 64
    from tools.computer_use.cua_backend_driver import resolve_cua_driver_cmd
    binary = resolve_cua_driver_cmd(driver_cmd)
    if not binary:
        print("cua-driver: not installed. Run: hermes computer-use install")
        return 2
    try:
        env = _child_env()
        print("Requesting Accessibility + Screen Recording for CuaDriver.\n"
              "macOS will show a dialog attributed to CuaDriver (com.trycua.driver) — approve it, then return here.")
        return int(subprocess.run([binary, "permissions", "grant"], env=env, stdin=subprocess.DEVNULL).returncode)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130
    except Exception as exc:  # pragma: no cover - defensive
        print(f"cua-driver permissions grant failed: {exc}", file=sys.stderr)
        return 2
