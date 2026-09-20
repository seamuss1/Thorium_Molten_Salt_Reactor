"""OS-held owner leases survive stale metadata, but never survive process death."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_JOBS: dict[subprocess.Popen, int] = {}
_JOBS_LOCK = threading.Lock()


def start_process(command: list[str], **kwargs) -> subprocess.Popen:
    """Give each phase an OS-owned process tree before any work can start."""
    if sys.platform != "win32":
        return subprocess.Popen(command, start_new_session=True, **kwargs)
    # The gate prevents descendants being launched before Job Object assignment.
    gate = "import subprocess,sys; sys.stdin.readline(); sys.exit(subprocess.call(sys.argv[1:]))"
    process = subprocess.Popen(
        [sys.executable, "-c", gate, *command],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
        **kwargs,
    )
    try:
        job = _assign_windows_job(process)
        with _JOBS_LOCK:
            _JOBS[process] = job
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.close()
    except Exception:
        terminate_process_tree(process)
        process.wait(timeout=10)
        raise
    return process


def _assign_windows_job(process: subprocess.Popen) -> int:
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    ) or not kernel.AssignProcessToJobObject(job, int(process._handle)):  # type: ignore[attr-defined]
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise ctypes.WinError(error)
    return job


def acquire_lease(path: Path) -> BinaryIO | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def terminate_process_tree(process: subprocess.Popen) -> None:
    """Kill the session created for a phase, including descendants holding pipes."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        with _JOBS_LOCK:
            job = _JOBS.pop(process, None)
        if job is not None:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(job)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
