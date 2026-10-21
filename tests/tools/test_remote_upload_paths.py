"""Remote POSIX upload paths must not inherit the controller's path syntax."""

import base64
import shlex
import subprocess
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from tools.environments.daytona import DaytonaEnvironment
from tools.environments.local import _find_bash
from tools.environments.modal import ModalEnvironment
from tools.environments.ssh import SSHEnvironment


@pytest.mark.parametrize("backend", ["daytona", "modal", "ssh"])
def test_single_upload_parent_is_posix_and_quoted(backend, tmp_path, monkeypatch):
    host = tmp_path / "payload.txt"
    host.write_bytes(b"upload payload\n")
    remote = "/root/.stardust/skills/space dir; echo injected/file.txt"
    commands = []
    if backend == "daytona":
        env = object.__new__(DaytonaEnvironment)
        uploads = []
        env._sandbox = SimpleNamespace(
            process=SimpleNamespace(exec=commands.append),
            fs=SimpleNamespace(upload_file=lambda *args: uploads.append(args)),
        )
        env._daytona_upload(str(host), remote)
        assert uploads == [(str(host), remote)]
    elif backend == "modal":
        env = object.__new__(ModalEnvironment)
        def execute(cmd, **kwargs):
            commands.append(cmd)
            assert base64.b64decode(kwargs["stdin"]) == host.read_bytes()
        env._exec = execute
        env._modal_upload(str(host), remote)
    else:
        env = object.__new__(SSHEnvironment)
        env.user, env.host, env.port, env.key_path = "user", "host", 22, ""
        env._run_ssh = lambda cmd, **kwargs: commands.append(cmd)
        with monkeypatch.context() as scoped:
            scoped.setattr("tools.environments.ssh._SSH_MULTIPLEX", False)
            scoped.setattr("tools.environments.ssh.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0))
            env._scp_upload(str(host), remote)
    mkdir = commands[0].split(" && base64", 1)[0]
    assert mkdir == "mkdir -p " + shlex.quote(str(PurePosixPath(remote).parent))
    # Execute the generated argv against a test-owned mkdir function: exercise
    # real bash quoting without writing to /root or contacting a service.
    script = 'mkdir() { printf "%s\\n" "$#" "$1" "$2"; }; ' + mkdir
    result = subprocess.run([_find_bash(), "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["2", "-p", str(PurePosixPath(remote).parent)]
