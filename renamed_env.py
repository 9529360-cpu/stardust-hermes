"""Desktop environment variables renamed from ``HERMES_DESKTOP_*`` to ``STARDUST_DESKTOP_*``.

The Stardust rename dropped the old names with no fallback, so a shell or startup file that still set one
was silently ignored. Every renamed name resolves the same way as ``apps/desktop/electron/renamed-env.ts``:
the current name wins when it is set and non-empty; otherwise the legacy name is honoured, with one
deprecation warning per name per process. Warnings name the variables only, never their values. The two
tables must list the same pairs.

The warning goes straight to stderr rather than through ``logging``. The packaged backend's stderr is piped
into ``desktop.log``, and these readers can run before logging is configured. Where stderr is absent
(``pythonw``), the warning is skipped; the Electron side reports deprecated names at startup regardless.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Final

RENAMED_DESKTOP_ENV: Final[Mapping[str, str]] = {
    "STARDUST_DESKTOP_BOOT_FAKE": "HERMES_DESKTOP_BOOT_FAKE",
    "STARDUST_DESKTOP_CHILD_PID": "HERMES_DESKTOP_CHILD_PID",
    "STARDUST_DESKTOP_CWD": "HERMES_DESKTOP_CWD",
    "STARDUST_DESKTOP_DISABLE_GPU": "HERMES_DESKTOP_DISABLE_GPU",
    "STARDUST_DESKTOP_HERMES_ROOT": "HERMES_DESKTOP_HERMES_ROOT",
    "STARDUST_DESKTOP_IGNORE_EXISTING": "HERMES_DESKTOP_IGNORE_EXISTING",
    "STARDUST_DESKTOP_MANAGED": "HERMES_DESKTOP_MANAGED",
    "STARDUST_DESKTOP_PASSWORD_STORE": "HERMES_DESKTOP_PASSWORD_STORE",
    "STARDUST_DESKTOP_READY_FILE": "HERMES_DESKTOP_READY_FILE",
    "STARDUST_DESKTOP_TERMINAL": "HERMES_DESKTOP_TERMINAL",
}

_warned: set[str] = set()


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def read_renamed_env(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """Effective value of a renamed desktop variable, or None when neither name is set."""
    source = _environment(env)
    current = source.get(name)
    if current:
        return current
    legacy_name = RENAMED_DESKTOP_ENV[name]
    legacy = source.get(legacy_name)
    if not legacy:
        return None
    if name not in _warned:
        _warned.add(name)
        _warn(f"{legacy_name} is deprecated; set {name} instead.")
    return legacy


def renamed_env_is_set(name: str, env: Mapping[str, str] | None = None) -> bool:
    """True when either name is present, even if empty. Used where an explicit setting must win over config."""
    source = _environment(env)
    return name in source or RENAMED_DESKTOP_ENV[name] in source


def reset_renamed_env_warnings_for_tests() -> None:
    """Test seam: forget the warnings already emitted in this process."""
    _warned.clear()


def _warn(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    stream.write(message + "\n")
    stream.flush()
