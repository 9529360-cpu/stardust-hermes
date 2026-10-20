"""Which shell commands write files, for the verification ledger's terminal classifier.

``agent/verification_evidence.py`` decides whether a terminal command may have changed the workspace
after the last verifier ran. This module holds the knowledge that does not depend on how a command line
is tokenized: normalising a command word (``python.exe``, ``python3.11``, ``C:/x/powershell.exe``) and the
table of commands whose effect depends on their arguments (``tar -x`` writes, ``tar -t`` does not). It
only classifies text; nothing here executes anything.
"""
from __future__ import annotations

import re
import shlex
from typing import Callable, Optional

_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
_PYTHON_NAME = re.compile(r"pythonw?(?:\d+(?:\.\d+)*)?|pyw?")
_OTHER_INTERPRETERS = {
    "node": "node", "nodejs": "node", "ruby": "ruby", "perl": "perl", "php": "php",
    "bash": "shell", "sh": "shell", "zsh": "shell", "dash": "shell", "ksh": "shell",
    "pwsh": "powershell", "powershell": "powershell", "cmd": "cmd",
}


def command_words(raw: str) -> list[str]:
    """Words of a shell segment as typed: quotes stripped, backslashes (Windows paths) kept.

    ``shlex`` in POSIX mode treats a backslash as an escape and silently turns ``.venv\\Scripts\\python.exe``
    into ``.venvScriptspython.exe``; this is the tokenization to use when only the command name matters."""
    try:
        words = shlex.split(raw, posix=False)
    except ValueError:
        return []
    return [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "'\"" else w for w in words]


def executable_name(word: str) -> str:
    """Lowercase basename of a command word, either separator, without a Windows executable suffix."""
    name = re.split(r"[\\/]", word.strip("\"'"))[-1].lower()
    for suffix in _WINDOWS_EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def interpreter_kind(name: str) -> Optional[str]:
    """``python`` / ``node`` / ``ruby`` / ``perl`` / ``php`` / ``shell`` / ``powershell`` / ``cmd``, else None."""
    return "python" if _PYTHON_NAME.fullmatch(name) else _OTHER_INTERPRETERS.get(name)


def _has_any(args: list[str], *flags: str) -> bool:
    """Exact flag, or ``--flag=value`` for a long flag."""
    return any(a in flags or (a.startswith("--") and a.split("=", 1)[0] in flags) for a in args)


def _bundle_has(args: list[str], letters: str) -> bool:
    """A single-dash flag bundle such as ``-avn`` that contains one of ``letters``."""
    return any(len(a) > 1 and a[0] == "-" and a[1] != "-" and any(ch in a[1:] for ch in letters) for a in args)


def _first_operand(args: list[str]) -> str:
    return next((a for a in args if not a.startswith("-")), "")


def _tar(args: list[str]) -> bool:
    """Extract, create, append, update or delete write; list and diff do not. ``tar xzf`` has no dash."""
    modes: set[str] = set()
    for index, arg in enumerate(args):
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if name in {"--extract", "--get", "--create", "--append", "--update", "--delete", "--concatenate", "--catenate"}:
                return True
        elif arg.startswith("-"):
            modes.update(arg[1:])
        elif index == 0:
            modes.update(arg)
    return bool(modes & set("xcruA"))


def _curl(args: list[str]) -> bool:
    """Writes a file for -o/-O (unless the target is stdout)."""
    for index, arg in enumerate(args):
        if arg in {"-o", "--output"}:
            if not (index + 1 < len(args) and args[index + 1] == "-"):
                return True
        elif arg.startswith("--output=") and arg != "--output=-":
            return True
        elif arg in {"-O", "--remote-name", "--remote-name-all", "-J", "--remote-header-name", "--output-dir"}:
            return True
        elif re.fullmatch(r"-[sSLfkvgiI#]*[oO]", arg) and not (arg.endswith("o") and index + 1 < len(args) and args[index + 1] == "-"):
            return True
    return False


def _wget(args: list[str]) -> bool:
    """Saves to a file by default; stdout (-O -) and --spider do not."""
    if _has_any(args, "--spider"):
        return False
    for index, arg in enumerate(args):
        if arg in {"-O", "--output-document"} and index + 1 < len(args) and args[index + 1] == "-":
            return False
        if arg == "--output-document=-" or re.fullmatch(r"-[a-zA-Z]*O-", arg):
            return False
    return True


def _rsync(args: list[str]) -> bool:
    return not (_has_any(args, "-n", "--dry-run", "--list-only") or _bundle_has(args, "n"))


def _seven_zip(args: list[str]) -> bool:
    return _first_operand(args) in {"x", "e", "a", "u", "d", "rn"}


def _powershell_outfile(args: list[str]) -> bool:
    return any(a.lower() in {"-outfile", "-out"} or a.lower().startswith("-outfile:") for a in args)


# command name (lowercase, no suffix) -> does THIS invocation write files? Receives the arguments as typed.
CONDITIONAL_WRITERS: dict[str, Callable[[list[str]], bool]] = {
    "tar": _tar, "bsdtar": _tar,
    "unzip": lambda a: not _bundle_has(a, "lvtpzZ"),
    "7z": _seven_zip, "7za": _seven_zip, "7zr": _seven_zip,
    "expand-archive": lambda a: True, "compress-archive": lambda a: True,
    "curl": _curl, "wget": _wget,
    "invoke-webrequest": _powershell_outfile, "iwr": _powershell_outfile,
    "invoke-restmethod": _powershell_outfile, "irm": _powershell_outfile,
    "rsync": _rsync,
    "chmod": lambda a: True, "chown": lambda a: True, "chgrp": lambda a: True,
    "dos2unix": lambda a: True, "unix2dos": lambda a: True, "mac2unix": lambda a: True, "unix2mac": lambda a: True,
    "clang-format": lambda a: _has_any(a, "-i", "--inplace"),
    "rubocop": lambda a: _has_any(a, "-a", "-A", "--auto-correct", "--auto-correct-all", "--autocorrect", "--autocorrect-all"),
    "shfmt": lambda a: _has_any(a, "-w", "--write"),
    "terraform": lambda a: _first_operand(a) == "fmt" and not _has_any(a, "-check", "-write=false"),
    "tofu": lambda a: _first_operand(a) == "fmt" and not _has_any(a, "-check", "-write=false"),
    "dotnet": lambda a: _first_operand(a) == "format" and not _has_any(a, "--verify-no-changes"),
    "swiftformat": lambda a: not _has_any(a, "--lint", "--dryrun"),
    "php-cs-fixer": lambda a: _first_operand(a) == "fix" and not _has_any(a, "--dry-run"),
}


def python_module_writes(module: str, args: list[str]) -> bool:
    """Classify known ``python -m`` checkers; unknown modules remain effect-capable."""
    if module in {"pytest", "unittest", "nose", "nose2", "mypy", "pyright"}:
        return False
    if module == "ruff":
        subcommand = _first_operand(args)
        if subcommand == "check":
            return _has_any(args, "--fix", "--fix-only", "--output-file")
        if subcommand == "format":
            return not _has_any(args, "--check", "--diff")
    if module == "black":
        return not _has_any(args, "--check", "--diff")
    if module == "isort":
        return not _has_any(args, "--check", "--check-only", "--diff")
    return True


def conditional_write(name: str, args: list[str]) -> Optional[bool]:
    """True/False when ``name`` is a known conditional writer, None when it is not in the table."""
    predicate = CONDITIONAL_WRITERS.get(name)
    return None if predicate is None else bool(predicate(args))


def _updates_snapshots(args: list[str]) -> bool:
    return _has_any(args, "-u", "--update", "--updateSnapshot", "--update-snapshots")


# Test runners, linters and format checkers that only read the workspace, by tool name -> does THIS invocation
# write (a snapshot update, ``eslint --fix``, ``prettier --write``)? Started through ``node`` their entry point is
# a script file (``vitest.mjs``, ``eslint.js``, ``cli.js``), which would otherwise count as a custom script.
VERIFIER_TOOLS: dict[str, Callable[[list[str]], bool]] = {
    "vitest": _updates_snapshots, "jest": _updates_snapshots, "playwright": _updates_snapshots,
    "mocha": lambda a: False,
    "eslint": lambda a: _has_any(a, "--fix"),
    "prettier": lambda a: _has_any(a, "--write", "-w"),
}

# npm package -> tool, for the script a ``node`` command runs from ``node_modules``.
_NODE_PACKAGE_TOOLS = {
    "vitest": "vitest", "jest": "jest", "jest-cli": "jest", "mocha": "mocha", "eslint": "eslint",
    "prettier": "prettier", "playwright": "playwright", "@playwright/test": "playwright",
}


def _node_verifier(script: str) -> Optional[str]:
    """Known tool a ``node`` script belongs to: ``node_modules/vitest/vitest.mjs`` is vitest,
    ``node_modules/@playwright/test/cli.js`` is playwright, ``node_modules/.bin/eslint`` is eslint. A script of the
    project itself is never a known tool, however it is named."""
    parts = [p for p in script.replace("\\", "/").lower().split("/") if p and p != "."]
    if "node_modules" not in parts:
        return None
    tail = parts[len(parts) - parts[::-1].index("node_modules"):]  # what follows the LAST node_modules
    if not tail:
        return None
    if tail[0] == ".bin":
        return _NODE_PACKAGE_TOOLS.get(executable_name(tail[1])) if len(tail) > 1 else None
    return _NODE_PACKAGE_TOOLS.get("/".join(tail[:2]) if tail[0].startswith("@") else tail[0])


def node_verifier_writes(script: str, args: list[str]) -> Optional[bool]:
    """True/False when ``script`` is a known verifier's entry point (does this invocation write?), None otherwise."""
    tool = _node_verifier(script)
    return None if tool is None else bool(VERIFIER_TOOLS[tool](args))
