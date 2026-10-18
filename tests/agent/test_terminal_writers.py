"""Command-name normalisation and the conditional-writer table behind the verification ledger's terminal classifier."""

import pytest

from agent.terminal_writers import (
    CONDITIONAL_WRITERS,
    command_words,
    conditional_write,
    executable_name,
    interpreter_kind,
)


@pytest.mark.parametrize(("word", "expected"), [
    ("python", "python"),
    ("python.exe", "python"),
    ("PYTHON.EXE", "python"),
    ("/usr/bin/python3.11", "python3.11"),
    (".venv/Scripts/python.exe", "python"),
    (".venv\\Scripts\\python.exe", "python"),
    ("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", "powershell"),
    ('"C:/Program Files/PowerShell/7/pwsh.exe"', "pwsh"),
    ("npm.cmd", "npm"),
    ("tool.bat", "tool"),
    ("Set-Content", "set-content"),
])
def test_executable_name_ignores_directories_suffixes_and_case(word, expected):
    assert executable_name(word) == expected


@pytest.mark.parametrize(("name", "kind"), [
    ("python", "python"), ("python3", "python"), ("python3.11", "python"), ("pythonw", "python"), ("py", "python"),
    ("node", "node"), ("nodejs", "node"), ("ruby", "ruby"), ("perl", "perl"), ("php", "php"),
    ("bash", "shell"), ("sh", "shell"), ("zsh", "shell"), ("dash", "shell"), ("ksh", "shell"),
    ("pwsh", "powershell"), ("powershell", "powershell"), ("cmd", "cmd"),
])
def test_interpreters_are_recognised_in_every_common_spelling(name, kind):
    assert interpreter_kind(name) == kind


@pytest.mark.parametrize("name", ["pytest", "pip", "pyright", "pyenv", "python-config", "nodemon", "bashful", "git", ""])
def test_lookalike_commands_are_not_interpreters(name):
    assert interpreter_kind(name) is None


def test_command_words_keep_windows_backslashes_that_posix_splitting_eats():
    assert command_words(r'.venv\Scripts\python.exe do_stuff.py') == [r".venv\Scripts\python.exe", "do_stuff.py"]
    assert command_words('"C:\\Program Files\\Git\\bin\\bash.exe" -c "echo hi"') == [
        "C:\\Program Files\\Git\\bin\\bash.exe", "-c", "echo hi",
    ]
    assert command_words("echo 'unterminated") == []


@pytest.mark.parametrize(("name", "args"), [
    ("tar", ["-xf", "a.tar"]), ("tar", ["xzf", "a.tgz"]), ("tar", ["-cf", "a.tar", "src"]),
    ("tar", ["--extract", "--file=a.tar"]), ("tar", ["-C", "out", "-xzf", "a.tgz"]),
    ("unzip", ["a.zip"]), ("unzip", ["-o", "a.zip", "-d", "out"]),
    ("7z", ["x", "a.7z"]), ("7z", ["a", "out.7z", "src"]),
    ("expand-archive", ["-path", "a.zip"]), ("compress-archive", ["-path", "src", "-destinationpath", "a.zip"]),
    ("curl", ["-o", "out.json", "https://example.invalid/x"]), ("curl", ["-O", "https://example.invalid/x"]),
    ("curl", ["-fsSLo", "out", "https://example.invalid/x"]), ("curl", ["--output", "out", "https://example.invalid/x"]),
    ("wget", ["https://example.invalid/x"]), ("wget", ["-O", "out.json", "https://example.invalid/x"]),
    ("invoke-webrequest", ["https://example.invalid/x", "-OutFile", "x.bin"]),
    ("iwr", ["https://example.invalid/x", "-outfile", "x.bin"]),
    ("rsync", ["-a", "src/", "dst/"]), ("chmod", ["+x", "run.sh"]),
    ("dos2unix", ["a.sh"]), ("clang-format", ["-i", "a.c"]), ("rubocop", ["-a"]), ("rubocop", ["--auto-correct-all"]),
    ("shfmt", ["-w", "a.sh"]), ("terraform", ["fmt"]), ("dotnet", ["format"]), ("swiftformat", ["."]),
    ("php-cs-fixer", ["fix"]),
])
def test_conditional_writers_that_write(name, args):
    assert conditional_write(name, args) is True


@pytest.mark.parametrize(("name", "args"), [
    ("tar", ["-tf", "a.tar"]), ("tar", ["tzf", "a.tgz"]), ("tar", ["--list", "--file=a.tar"]), ("tar", ["-df", "a.tar"]),
    ("unzip", ["-l", "a.zip"]), ("unzip", ["-t", "a.zip"]), ("unzip", ["-p", "a.zip", "x.txt"]),
    ("7z", ["l", "a.7z"]), ("7z", ["t", "a.7z"]),
    ("curl", ["https://example.invalid/x"]), ("curl", ["-s", "https://example.invalid/x"]),
    ("curl", ["-o", "-", "https://example.invalid/x"]), ("curl", ["-I", "https://example.invalid/x"]),
    ("wget", ["-O", "-", "https://example.invalid/x"]), ("wget", ["-qO-", "https://example.invalid/x"]),
    ("wget", ["--spider", "https://example.invalid/x"]),
    ("invoke-webrequest", ["https://example.invalid/x"]), ("irm", ["https://example.invalid/x"]),
    ("rsync", ["-an", "src/", "dst/"]), ("rsync", ["--dry-run", "src/", "dst/"]), ("rsync", ["--list-only", "src/"]),
    ("clang-format", ["a.c"]), ("rubocop", []), ("rubocop", ["lib/"]), ("shfmt", ["a.sh"]), ("shfmt", ["-d", "a.sh"]),
    ("terraform", ["fmt", "-check"]), ("terraform", ["plan"]), ("dotnet", ["format", "--verify-no-changes"]),
    ("dotnet", ["build"]), ("swiftformat", [".", "--lint"]), ("php-cs-fixer", ["fix", "--dry-run"]),
])
def test_conditional_writers_that_only_read(name, args):
    assert conditional_write(name, args) is False


def test_unknown_commands_are_not_decided_here():
    assert conditional_write("ls", ["-la"]) is None
    assert conditional_write("pytest", []) is None
    assert set(CONDITIONAL_WRITERS) >= {"tar", "unzip", "curl", "wget", "rsync", "clang-format", "terraform"}
