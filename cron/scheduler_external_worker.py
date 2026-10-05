"""Restart-safe external cron workers: launching a job outside the gateway process, waiting on
it, and adopting a dispatched payload inside the worker.

Split out of ``cron.scheduler``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_sched`` (import-cycle breaking), so
monkeypatching ``cron.scheduler.<name>`` keeps working.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger("cron.scheduler")  # log-record parity with the origin module


def _wait_for_external_cron_worker_body(
    process: subprocess.Popen,
    *,
    execution_id: str,
) -> bool:
    """Preserve ``run_one_job``'s synchronous contract after handoff.

    The worker owns the durable execution and survives this gateway process.
    The caller nevertheless waits while it remains alive so manual/background
    callers do not release their in-process guard or report stale job state.
    A gateway replacement may kill this waiter; it does not kill the scoped
    worker or change its ledger ownership.
    """
    def _is_terminal() -> bool:
        current = _sched.get_execution(execution_id)
        return bool(current and current.get("status") in _sched._TERMINAL_STATES)

    # The worker commits its terminal row before its process exits, so exit is
    # the correct wakeup.  Each ledger read opens a connection and re-runs
    # schema init; polling it at 50ms for an hours-long agent run is ~72k
    # opens/hour of pure contention with the worker's own writes.
    while True:
        try:
            returncode = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if _is_terminal():
                return True
            continue
        # The worker can commit its terminal row and exit between the first
        # read and wait(). Re-read the exact attempt before declaring that
        # it died without terminalizing.
        if _is_terminal():
            return True
        # If the adopted worker died without terminalizing, its owner is
        # now provably gone. Recover to ``unknown`` rather than routing the
        # exception through the pre-handoff dispatch-failure path, which
        # would falsely assert that no side effect could have happened.
        _sched.recover_interrupted_executions()
        if _is_terminal():
            return True
        raise RuntimeError(
            "cron external worker exited before durable recovery could "
            f"terminalize its execution state (exit {returncode})"
        )


def _wait_for_external_cron_worker(
    process: subprocess.Popen,
    *,
    execution_id: str,
    job_id: Optional[str] = None,
    handoff_files: tuple[_sched.Path, ...] = (),
) -> bool:
    try:
        return _wait_for_external_cron_worker_body(
            process, execution_id=execution_id
        )
    finally:
        if job_id is not None:
            with _sched._running_lock:
                _sched._restart_safe_waiter_job_ids.discard(job_id)
        # The execution is terminal or its worker is dead: nobody will read a
        # payload or acknowledgement left behind by a late/unread handoff.
        for stale in handoff_files:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass


def _launch_external_cron_worker(job: dict) -> bool:
    """Launch *job* outside the managed gateway process when required.

    Returns ``False`` outside a managed systemd gateway (in-process path).  In
    managed topology the job always goes to an external worker with the #101940
    ownership handoff: in a transient user scope, or — when no user D-Bus
    session exists and ``cron.require_restart_safe_scope`` is false — as a
    direct subprocess (process separation kept, cgroup isolation lost).
    """
    execution_id = str(job["execution_id"])
    job_id = str(job["id"])
    handoff_dir = _sched._get_hermes_home() / "cron" / "external-workers"
    payload_path = handoff_dir / f"{execution_id}.json"
    ack_path = handoff_dir / f"{execution_id}.ready"
    command = [
        sys.executable,
        "-m",
        "cron.scheduler",
        "--external-worker-file",
        str(payload_path),
        "--ack-file",
        str(ack_path),
    ]

    from agent.secret_scope import (
        build_profile_secret_scope,
        is_multiplex_active,
        reset_secret_scope,
        set_secret_scope,
    )
    from hermes_cli.env_loader import hydrate_profile_secret_sources
    from tools.environments.local import build_subprocess_env, strip_launch_profile_env
    from tools.process_registry import (
        restart_safe_gateway_child_argv,
        scoped_spawn_lost_user_bus,
        systemd_user_bus_env,
    )

    try:
        require_restart_safe_scope = bool(
            (_sched.load_config_readonly().get("cron") or {}).get("require_restart_safe_scope", False)
        )
    except Exception:
        require_restart_safe_scope = False
    multiplex_active = is_multiplex_active()
    dispatch = restart_safe_gateway_child_argv(
        command,
        unit_suffix=f"cron-{job_id}-exec-{execution_id}",
        require_restart_safe_scope=require_restart_safe_scope,
    )
    if dispatch.mode == "in_process":
        return False

    if _sched.mark_execution_handoff_pending(execution_id) is None:
        raise RuntimeError(
            "cron execution claim changed before external worker handoff"
        )

    _sched._ensure_cron_dir(handoff_dir)
    try:
        handoff_dir.chmod(0o700)
    except OSError:
        pass
    fd = os.open(payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as payload_file:
            json.dump(
                {
                    "job": job,
                    "profile_home": str(_sched._get_hermes_home().resolve()),
                    "multiplex_active": multiplex_active,
                },
                payload_file,
            )
            payload_file.flush()
            os.fsync(payload_file.fileno())
    except BaseException:
        payload_path.unlink(missing_ok=True)
        raise

    profile_home = _sched._get_hermes_home().resolve()
    hydrate_profile_secret_sources(profile_home)
    secret_token = set_secret_scope(build_profile_secret_scope(profile_home))
    try:
        worker_env = strip_launch_profile_env(build_subprocess_env(
            scrub_secrets=multiplex_active,
            inherit_profile_home=True,
            extra={"HERMES_HOME": str(profile_home)},
        ))
    finally:
        reset_secret_scope(secret_token)
    worker_env = systemd_user_bus_env(worker_env)
    # Unattended worker: the gateway sets HERMES_EXEC_ASK at startup (interactive launches set
    # the other two), and an inherited presence var makes every env-fallback consumer in the
    # child (`_is_interactive_cli`, sudo prompting, `check_cronjob_requirements`) believe a
    # human is present to answer (#110932).
    for _presence_var in (
        "HERMES_INTERACTIVE",
        "HERMES_GATEWAY_SESSION",
        "HERMES_EXEC_ASK",
    ):
        worker_env.pop(_presence_var, None)
    try:
        process = subprocess.Popen(
            dispatch.argv,
            cwd=str(_sched.Path(__file__).resolve().parent.parent),
            env=worker_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=_sched.windows_hide_flags(),
        )
    except BaseException:
        payload_path.unlink(missing_ok=True)
        raise

    with _sched._running_lock:
        _sched._restart_safe_waiter_job_ids.add(job_id)

    # Same window the dead-owner recovery ledger grants a pending handoff: a cold
    # worker start (imports + secret hydration) measures ~10-12s in the field, and
    # a dispatch deadline shorter than the adoption grace made the two guards
    # around one handoff disagree.
    deadline = time.monotonic() + _sched.HANDOFF_ADOPTION_GRACE_SECONDS
    while time.monotonic() < deadline:
        if ack_path.exists():
            try:
                acknowledgement = json.loads(ack_path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception(
                    "Cron external worker %s published an unreadable acknowledgement; "
                    "treating handoff as ownership-uncertain",
                    execution_id,
                )
                return _wait_for_external_cron_worker(
                    process,
                    execution_id=execution_id,
                    job_id=job_id,
                    handoff_files=(payload_path,),
                )
            finally:
                ack_path.unlink(missing_ok=True)
            if (
                not isinstance(acknowledgement, dict)
                or acknowledgement.get("execution_id") != execution_id
            ):
                logger.error(
                    "Cron external worker acknowledgement mismatch for %s; "
                    "treating handoff as ownership-uncertain",
                    execution_id,
                )
                return _wait_for_external_cron_worker(
                    process,
                    execution_id=execution_id,
                    job_id=job_id,
                    handoff_files=(payload_path,),
                )
            logger.info(
                "Cron job '%s' handed to restart-safe worker pid=%s execution=%s",
                job_id,
                acknowledgement.get("pid"),
                execution_id,
            )
            with _sched._running_lock, contextlib.suppress(TypeError, ValueError):
                _sched._running_worker_pids[job_id] = int(acknowledgement.get("pid") or process.pid)
            return _wait_for_external_cron_worker(
                process,
                execution_id=execution_id,
                job_id=job_id,
                handoff_files=(payload_path,),
            )
        returncode = process.poll()
        if returncode is not None:
            with _sched._running_lock:
                _sched._restart_safe_waiter_job_ids.discard(job_id)
            payload_path.unlink(missing_ok=True)
            if dispatch.mode == "scoped" and scoped_spawn_lost_user_bus(worker_env):
                # systemd-run itself failed (stderr is DEVNULL): name the cause, not the exit code.
                raise RuntimeError(
                    "restart-safe systemd scope could not be created: the user D-Bus session at "
                    f"/run/user/{os.getuid()}/bus disappeared after the gateway started. On a "  # windows-footgun: ok — scoped dispatch exists only on Linux
                    "system-level service install, run `sudo loginctl enable-linger <gateway-user>`; "
                    "the next fire dispatches without scope isolation."
                )
            raise RuntimeError(
                f"cron external worker exited before ownership acknowledgement "
                f"(exit {returncode})"
            )
        time.sleep(0.05)

    # The child may have adopted the durable row just before publishing its
    # acknowledgement.  Never fall back to in-process execution on an uncertain
    # handoff: that could duplicate side effects.  The execution owner/dead-owner
    # recovery ledger remains the authority.
    logger.warning(
        "Cron external worker for job '%s' did not acknowledge within %.0fs; "
        "leaving the durable execution claim untouched",
        job_id,
        _sched.HANDOFF_ADOPTION_GRACE_SECONDS,
    )
    return _wait_for_external_cron_worker(
        process,
        execution_id=execution_id,
        job_id=job_id,
        handoff_files=(payload_path, ack_path),
    )


def _run_external_worker_payload(payload_path: _sched.Path, ack_path: _sched.Path) -> bool:
    """Adopt and execute one gateway-dispatched cron payload.

    The execution row is created by the gateway before spawn, then transferred
    here before the ready acknowledgement is published.  No side effect runs
    unless that durable ownership transfer succeeds.
    """
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        job = payload["job"]
        profile_home = _sched.Path(payload["profile_home"]).resolve()
        execution_id = str(job["execution_id"])
    except Exception:
        logger.exception("Cron external worker could not load payload %s", payload_path)
        return False
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except OSError:
            pass

    from agent.secret_scope import (
        build_profile_secret_scope,
        is_multiplex_active,
        reset_secret_scope,
        set_multiplex_active,
        set_secret_scope,
    )
    from cron.executions import adopt_claimed_execution
    from hermes_cli.env_loader import hydrate_profile_secret_sources
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    home_token = set_hermes_home_override(profile_home)
    previous_multiplex = is_multiplex_active()
    multiplex_active = bool(payload.get("multiplex_active", False))
    set_multiplex_active(multiplex_active)
    hydrate_profile_secret_sources(profile_home)
    secret_token = set_secret_scope(build_profile_secret_scope(profile_home))
    try:
        with _sched.use_cron_store(profile_home):
            if adopt_claimed_execution(execution_id) is None:
                logger.error(
                    "Cron external worker refused execution %s: durable ownership "
                    "could not be established",
                    execution_id,
                )
                return False
            try:
                ack_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(ack_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as ack_file:
                    json.dump({"pid": os.getpid(), "execution_id": execution_id}, ack_file)
                    ack_file.flush()
                    os.fsync(ack_file.fileno())
            except Exception:
                logger.exception(
                    "Cron external worker could not publish ready acknowledgement for %s",
                    execution_id,
                )
                return False
            old_external_execution = os.environ.get("_HERMES_CRON_EXTERNAL_WORKER")
            os.environ["_HERMES_CRON_EXTERNAL_WORKER"] = execution_id
            try:
                return _sched.run_one_job(job, adapters=None, loop=None, verbose=False)
            finally:
                if old_external_execution is None:
                    os.environ.pop("_HERMES_CRON_EXTERNAL_WORKER", None)
                else:
                    os.environ["_HERMES_CRON_EXTERNAL_WORKER"] = old_external_execution
    finally:
        reset_secret_scope(secret_token)
        set_multiplex_active(previous_multiplex)
        reset_hermes_home_override(home_token)


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.scheduler`` re-exports from it.
from cron import scheduler as _sched  # noqa: E402
