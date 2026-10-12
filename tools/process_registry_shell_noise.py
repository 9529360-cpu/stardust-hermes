"""Shell start-up noise at the head of a background process's output.

The spawn shell (``bash -lic``) prints job-control warnings before it runs the command. The kernel
hands those warnings to the reader in whatever pieces it likes, so the filter works on the stream,
never on a single read: a warning split across two reads must disappear just like a whole one.
"""

from __future__ import annotations

import os

from tools.environments.local import _SPAWN_COMPATIBLE_SHELLS

SHELL_NOISE_SUBSTRINGS = (
    "no job control in this shell",
    "cannot set terminal process group",
    "tcsetattr: Inappropriate ioctl for device",
)


def is_shell_noise_line(line: str) -> bool:
    return any(noise in line for noise in SHELL_NOISE_SUBSTRINGS)


def _may_still_be_shell_noise(partial: str) -> bool:
    """True while an unterminated line could still complete into shell noise."""
    if is_shell_noise_line(partial):
        return True
    shell, separator, message = partial.partition(": ")
    if separator and os.path.basename(shell) in _SPAWN_COMPATIBLE_SHELLS:
        candidate = message  # "<shell>: <warning>"
    elif separator:
        candidate = partial  # a bare warning such as "tcsetattr: ..."
    elif any(f"{name}: ".startswith(partial) for name in _SPAWN_COMPATIBLE_SHELLS):
        return True  # still inside the "<shell>: " prefix
    else:
        candidate = partial
    return any(noise.startswith(candidate) for noise in SHELL_NOISE_SUBSTRINGS)


def filter_shell_noise_head(
    head_open: bool, pending: str, text: str, *, final: bool = False,
) -> tuple[str, str, bool]:
    """Filter one read of a process's stdout. Returns ``(emit, pending, head_open)``.

    While the head is open, complete shell-noise lines are dropped and the first other line closes
    it. An unterminated first line is held in ``pending`` only while it could still become noise: a
    prompt such as ``Password: `` is emitted at once, while a line that starts like a shell warning
    (``bash: ...``) waits for the rest of the line. ``final`` ends the stream and decides whatever is
    pending.
    """
    if not head_open:
        return text, "", False
    data = pending + text
    while data:
        newline = data.find("\n")
        if newline < 0:
            if not final and _may_still_be_shell_noise(data):
                return "", data, True
            return ("" if is_shell_noise_line(data) else data), "", False
        line_end = newline + 1
        if not is_shell_noise_line(data[:line_end]):
            return data, "", False
        data = data[line_end:]
    return "", "", True
