"""Windows job-object containment for the commands the terminal tools run through Git Bash.

Git Bash starts a command with fork+exec emulation, so the program that finally runs (``sleep``, ``git``, ``curl``,
``tail``...) can outlive every ancestor in the Windows process tree: its recorded parent has already exited.
``taskkill /T`` and psutil both walk parent links, so ending "the tree" of a timed-out or stopped command left that
program running and still holding the output pipe. A job object keeps every descendant whatever its parent links say,
and terminating the job ends them all at once.

The job has no ``KILL_ON_JOB_CLOSE``: dropping it never kills anything, so ``cmd &`` outlives a command that finished
normally exactly as it does on POSIX. Off Windows, and wherever no job can hold the process, :func:`spawn_contained`
is a plain ``subprocess.Popen`` and :func:`terminate_contained` reports that it did nothing, so callers fall back to
their ordinary kill.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import logging
import subprocess
import sys
import threading
import weakref
from typing import Any

import psutil

logger = logging.getLogger(__name__)

_CREATE_SUSPENDED = 0x00000004
_TERMINATED_EXIT_CODE = 1


@functools.lru_cache(maxsize=1)
def _kernel32():
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, argtypes, restype in (
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = argtypes, restype
    return api


class ProcessJob:
    """An unnamed job object that holds one spawned command and everything it starts."""

    def __init__(self) -> None:
        self._api = _kernel32()
        self._lock = threading.Lock()
        handle = self._api.CreateJobObjectW(None, None)  # no security attributes: the handle is not inheritable
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle: int | None = handle

    def assign(self, proc: subprocess.Popen) -> None:
        """Put ``proc`` in the job. ``Popen`` keeps its process handle, so a recycled PID cannot be mistaken for it."""
        with self._lock:
            if self._handle is None:
                raise OSError("job object already released")
            if not self._api.AssignProcessToJobObject(self._handle, int(proc._handle)):
                raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> bool:
        """End every process in the job. False once the job has been released."""
        with self._lock:
            if self._handle is None:
                return False
            return bool(self._api.TerminateJobObject(self._handle, _TERMINATED_EXIT_CODE))

    def close(self) -> None:
        """Drop our handle. The processes in the job keep running; repeated calls are harmless."""
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            self._api.CloseHandle(handle)


def _popen_with_stdin(argv: Any, stdin: Any, **kwargs: Any) -> subprocess.Popen:
    """Keep stdin explicit for the repository guard while preserving Popen semantics."""
    return subprocess.Popen(argv, stdin=stdin, **kwargs)


def _owns_process_handle(proc: Any) -> bool:
    """True for a ``Popen`` that started a real Windows process, False for the doubles tests swap in for one.

    ``subprocess`` gives every process it creates a ``Handle``. A class check against ``subprocess.Popen`` cannot tell the
    two apart: the class a wrapper installs changes (``tests/conftest.py`` guards every test with a new subclass), and a
    process made by one wrapper is no instance of the class an earlier import saw.
    """
    if sys.platform != "win32":
        return False
    return isinstance(getattr(proc, "_handle", None), subprocess.Handle)


def spawn_contained(argv: Any, **popen_kwargs: Any) -> subprocess.Popen:
    """``subprocess.Popen`` whose whole process tree :func:`terminate_contained` can later end.

    The process is created suspended, put in its job, then resumed, so nothing it starts can escape the job. If no job
    can hold it (for example the host already runs inside a job that forbids nesting) the command still runs, as a
    plain ``Popen`` would. Every way out either returns a running process or leaves none behind.
    """
    stdin = popen_kwargs.pop("stdin", None)
    if sys.platform != "win32":
        return _popen_with_stdin(argv, stdin, **popen_kwargs)
    try:
        job = ProcessJob()
    except OSError:
        logger.debug("job object unavailable; spawning without containment", exc_info=True)
        return _popen_with_stdin(argv, stdin, **popen_kwargs)
    popen_kwargs["creationflags"] = popen_kwargs.get("creationflags", 0) | _CREATE_SUSPENDED
    try:
        proc = _popen_with_stdin(argv, stdin, **popen_kwargs)
    except BaseException:
        job.close()
        raise
    if not _owns_process_handle(proc):
        # A test double standing in for subprocess.Popen: it made no real process, so there is nothing to put in
        # a job or to resume. Keep the long-standing subprocess.Popen monkeypatch seam usable on Windows too.
        job.close()
        return proc
    try:
        try:
            job.assign(proc)
        except OSError:
            logger.debug("process could not join its job; running without containment", exc_info=True)
            job.close()
            job = None
        try:
            psutil.Process(proc.pid).resume()
        except psutil.NoSuchProcess:
            pass  # it already ended; the caller sees the exit status
    except BaseException:
        # It was created suspended: end it rather than leave a frozen process nobody holds a reference to.
        with contextlib.suppress(OSError):
            proc.kill()
        if job is not None:
            job.close()
        raise
    if job is not None:
        proc._hermes_job = job
        weakref.finalize(proc, job.close)
    return proc


def terminate_contained(proc: Any) -> bool:
    """End the whole tree :func:`spawn_contained` put in a job.

    False when ``proc`` was not contained, or its job was released, so the caller falls back to its ordinary kill.
    """
    job = getattr(proc, "_hermes_job", None)
    return isinstance(job, ProcessJob) and job.terminate()
