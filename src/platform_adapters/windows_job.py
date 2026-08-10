"""Best-effort Windows Job Object containment for owned renderer processes.

Normal shutdown still uses verified PID/create-time process cleanup.  The Job
Object is an additional crash/forced-parent-exit boundary: when the owning
ShangBackground process loses the final job handle, Windows terminates every
associated renderer process (and inherited descendants) when assignment is
supported by the host environment.
"""
from __future__ import annotations

from collections.abc import Callable
import ctypes
import os
from typing import Any


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class KillOnCloseJob:
    """Own one native job handle and close it exactly once."""

    def __init__(self, handle: int, close_handle: Callable[[Any], Any]) -> None:
        self._handle = int(handle)
        self._close_handle = close_handle

    @property
    def handle(self) -> int:
        return self._handle

    def close(self) -> None:
        handle = self._handle
        self._handle = 0
        if not handle:
            return
        try:
            self._close_handle(ctypes.c_void_p(handle))
        except Exception:
            pass

    def __enter__(self) -> "KillOnCloseJob":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter-shutdown best effort
        self.close()


def attach_process_kill_on_close(
    process: Any,
    *,
    log: Callable[[str], None] | None = None,
) -> KillOnCloseJob | None:
    """Assign *process* to a kill-on-close Job Object when Windows permits it.

    Assignment is intentionally best-effort.  Some launch hosts may already
    place the process in a job whose restrictions reject nested assignment.  In
    that case callers keep their existing verified-PID cleanup path.
    """
    if os.name != "nt":
        return None

    def _log(message: str) -> None:
        if log is None:
            return
        try:
            log(message)
        except Exception:
            pass

    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        raw_job = kernel32.CreateJobObjectW(None, None)
        if not raw_job:
            _log(f"CreateJobObjectW failed: {ctypes.get_last_error()}")
            return None
        job_value = int(raw_job)
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            raw_job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            err = ctypes.get_last_error()
            kernel32.CloseHandle(raw_job)
            _log(f"SetInformationJobObject failed: {err}")
            return None

        process_handle = int(getattr(process, "_handle", 0) or 0)
        if not process_handle:
            kernel32.CloseHandle(raw_job)
            _log("Popen process handle is unavailable for Job Object assignment")
            return None
        if not kernel32.AssignProcessToJobObject(raw_job, wintypes.HANDLE(process_handle)):
            err = ctypes.get_last_error()
            kernel32.CloseHandle(raw_job)
            _log(f"AssignProcessToJobObject failed: {err}")
            return None
        return KillOnCloseJob(job_value, kernel32.CloseHandle)
    except Exception as exc:
        _log(f"Windows Job Object unavailable: {exc}")
        return None
