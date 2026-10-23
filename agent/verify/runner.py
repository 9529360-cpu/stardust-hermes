"""Verification runner: bootstrap -> build -> test -> start in background ->
readiness loop -> teardown (scoped port of grok-cli's verify flow).

Commands come from the project's own recipe and run with ``shell=True`` on
purpose: a developer tool running the project's own build commands in its own
checkout — the same trust level as the terminal tool.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.verify.recipes import Recipe

DEFAULT_PHASE_TIMEOUT = 600.0
DEFAULT_READY_TIMEOUT = 60.0
_TAIL_CHARS = 2000
PHASE_ORDER = ("bootstrap", "build", "test")
# Project-authored shell commands; see module docstring.
_SUBPROCESS_KW: dict[str, Any] = dict(
    shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, errors="replace",
)


@dataclass
class PhaseResult:
    phase: str
    command: str
    exit_code: int | None
    duration: float
    output_tail: str
    timed_out: bool = False
    executed: bool = True

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase, "command": self.command, "exitCode": self.exit_code,
            "duration": round(self.duration, 3), "ok": self.ok, "timedOut": self.timed_out,
            "outputTail": self.output_tail,
        }


@dataclass
class ReadinessResult:
    url: str
    ready: bool
    status_code: int | None
    duration: float
    error: str | None = None
    output_tail: str = ""
    healthy: bool | None = None

    @property
    def reachable(self) -> bool:
        return self.status_code is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "ready": self.ready, "statusCode": self.status_code,
            "reachable": self.reachable, "healthy": self.healthy,
            "duration": round(self.duration, 3), "error": self.error, "outputTail": self.output_tail,
        }


@dataclass
class VerifyResult:
    recipe_name: str
    phases: list[PhaseResult] = field(default_factory=list)
    readiness: ReadinessResult | None = None

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.phases) and (self.readiness is None or self.readiness.ready)

    def observations(self) -> list[dict[str, Any]]:
        """Facts observed by this runner, never UI interaction.

        HTTP health is only assessed against an opted-in status expectation.
        A recipe's test command is test-tier evidence; it need not be a unit suite.
        Bootstrap and the aggregate result establish no acceptance tier.
        """
        observations = [
            {
                "source": ("agent.verify.runner._run_phase_command" if p.executed
                           else "agent.verify.runner._compose_live_state_reason"),
                "recipe": self.recipe_name, "phase": p.phase, "index": index,
                "tier": ({"test": "test", "build": "build"}.get(p.phase, "unknown")
                         if p.executed else "unknown"),
                "executed": p.executed,
                "status": "passed" if p.ok else "failed",
                "exit_code": p.exit_code, "timed_out": p.timed_out,
                "duration": round(p.duration, 3),
            }
            for index, p in enumerate(self.phases)
        ]
        if self.readiness is not None:
            r = self.readiness
            observations.append({
                "source": "agent.verify.runner._poll_readiness",
                "recipe": self.recipe_name, "phase": "start", "tier": "runtime",
                "status": "passed" if r.ready else "failed",
                "observation": "http-health" if r.healthy is not None else "http-reachability",
                "ready": r.ready, "reachable": r.reachable, "healthy": r.healthy,
                "status_code": r.status_code, "duration": round(r.duration, 3),
            })
        return observations

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe": self.recipe_name, "ok": self.ok,
            "phases": [p.to_dict() for p in self.phases],
            "readiness": self.readiness.to_dict() if self.readiness else None,
        }


def _tail(text: str, limit: int = _TAIL_CHARS) -> str:
    return text[-limit:] if len(text) > limit else text


def _run_phase_command(
    phase: str, command: str, root: Path, timeout: float,
    on_output: Callable[[str], None] | None = None,
) -> PhaseResult:
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=str(root), timeout=timeout, **_SUBPROCESS_KW)
        output, exit_code, timed_out = proc.stdout or "", proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        raw = exc.output
        output = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
        exit_code, timed_out = None, True
    duration = time.monotonic() - started
    if on_output and output:
        on_output(output)
    return PhaseResult(phase, command, exit_code, duration, _tail(output), timed_out)


def _poll_readiness(
    url: str, timeout: float, interval: float = 1.0,
    accepted_statuses: list[int] | None = None,
) -> tuple[bool, int | None, str | None]:
    """Keep the last HTTP observation; only gate health when explicitly requested."""
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    last_status: int | None = None
    while (remaining := deadline - time.monotonic()) > 0:
        status = None
        try:
            with urllib.request.urlopen(url, timeout=min(5, remaining)) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = str(exc)
        if status is not None:
            last_status = status
            if accepted_statuses is None or status in accepted_statuses:
                return True, status, None
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    if last_status is not None:
        last_error = f"reachable but unhealthy ({last_status})"
    return False, last_status, last_error


def _terminate_process_group(proc: subprocess.Popen) -> None:
    """Stop the owned app tree on Windows or its process group on POSIX."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # shell=True owns a cmd.exe child; terminating only it leaves the app
        # alive with our stdout pipe open. Scope taskkill to this exact PID tree.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
        proc.wait(timeout=5)
        return
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    pgid = None
    if killpg is not None and getpgid is not None:
        try:
            pgid = getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(sig: int, fallback: Callable[[], None]) -> None:
        if pgid is not None:
            killpg(pgid, sig)  # windows-footgun: ok — POSIX-only branch (pgid only set when killpg exists)
        else:
            fallback()

    try:
        stop(signal.SIGTERM, proc.terminate)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            stop(getattr(signal, "SIGKILL", signal.SIGTERM), proc.kill)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _run_start_phase(
    recipe: Recipe, root: Path, ready_timeout: float, port_override: int | None = None
) -> ReadinessResult:
    assert recipe.start is not None
    port = port_override or recipe.port or 8000
    url = f"http://127.0.0.1:{port}{recipe.readiness_path}"
    started = time.monotonic()
    # start_new_session: own process group for clean teardown.
    proc = subprocess.Popen(recipe.start, cwd=str(root), start_new_session=True, **_SUBPROCESS_KW)
    output = ""
    try:
        ready, status, error = _poll_readiness(
            url, ready_timeout, accepted_statuses=recipe.readiness_accepted_statuses,
        )
    finally:
        _terminate_process_group(proc)
        try:
            output = proc.stdout.read() or "" if proc.stdout is not None else ""
        except (OSError, ValueError):
            output = ""
    return ReadinessResult(
        url, ready, status, time.monotonic() - started, error, _tail(output),
        healthy=ready if recipe.readiness_accepted_statuses is not None else None,
    )


def _compose_live_state_reason(root: Path) -> str | None:
    """Why ``docker compose build``/``up`` must not run at *root*, or ``None`` to proceed.

    Read-only ``docker compose ps`` probe. Only a missing docker binary proceeds -- the
    build phase would fail the same way, so there is nothing to protect. A hung daemon
    or a non-zero probe refuses: containers may be live and unobservable, which is
    exactly the #103567 loss window.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--status", "running", "--format", "{{.Name}}"],
            cwd=root, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return "docker compose ps timed out after 15s; live containers cannot be ruled out"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return f"docker compose ps failed (exit {result.returncode}): {detail[-1] if detail else 'no output'}"
    names = [line for line in result.stdout.splitlines() if line.strip()]
    return f"this compose project already has running container(s): {', '.join(names)}" if names else None


def run_verify(
    root: Path, recipe: Recipe, phases: tuple[str, ...] | list[str] | None = None,
    phase_timeout: float = DEFAULT_PHASE_TIMEOUT, ready_timeout: float = DEFAULT_READY_TIMEOUT,
    skip_start: bool = False, port_override: int | None = None, stop_on_failure: bool = True,
    on_output: Callable[[str], None] | None = None,
) -> VerifyResult:
    """Run the selected command phases sequentially, then (unless ``skip_start`` or a
    phase failed) boot ``recipe.start``, poll readiness, and tear the process group down.

    A ``compose`` recipe refuses outright when the project already has running
    containers: ``docker compose build`` + ``up`` replaces them on an image-hash
    change, destroying any container-local state they carry -- this has caused a
    real state-loss incident (#103567). The check is best-effort and read-only
    (``docker compose ps``); when it cannot run at all, verify proceeds rather than
    blocking on an unrelated environment gap, matching every other recipe kind's
    behavior when its own tooling is unavailable."""
    root = Path(root)
    selected = tuple(phases) if phases else PHASE_ORDER + ("start",)
    result = VerifyResult(recipe_name=recipe.name)

    mutating = ("build" in selected) or ("start" in selected and not skip_start)
    if recipe.kind == "compose" and mutating:
        reason = _compose_live_state_reason(root)
        if reason:
            result.phases.append(PhaseResult(
                phase="build", command=recipe.build[0] if recipe.build else "docker compose build",
                exit_code=1, duration=0.0, executed=False, output_tail=(
                    f"Refusing to run: {reason}. `docker compose build` + `up` would replace "
                    "live containers on an image-hash change, destroying any container-local "
                    "state they carry. If you intend to rebuild this live deployment, run "
                    "`docker compose build`/`up` yourself."
                ),
            ))
            return result

    for phase in PHASE_ORDER:
        if phase not in selected:
            continue
        for command in getattr(recipe, phase):
            phase_result = _run_phase_command(phase, command, root, phase_timeout, on_output)
            result.phases.append(phase_result)
            if not phase_result.ok and stop_on_failure:
                return result

    if not skip_start and "start" in selected and recipe.start and all(p.ok for p in result.phases):
        result.readiness = _run_start_phase(recipe, root, ready_timeout, port_override)
    return result
