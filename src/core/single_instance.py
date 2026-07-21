"""Cross-platform, per-user single-instance guard.

The lock is a real operating-system file lock.  It does not depend on a fixed
TCP port, so an unrelated local service cannot make the application believe it
is already running.  The locked file also carries a short-lived IPC identity
(token + endpoint name) used by :mod:`core.local_ipc`.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from typing import IO, Any

from app.config import IS_WINDOWS

APP_LOCK_ID = "ShangBackground"
APP_MUTEX_NAME = APP_LOCK_ID
_LOCK_FILE_NAME = "single_instance.lock"
_lock_file_handle: IO[str] | None = None
_lock_path: Path | None = None
_win_mutex_handle = None
_identity: dict[str, Any] = {}


def _user_lock_suffix() -> str:
    if not IS_WINDOWS:
        try:
            return str(os.getuid())
        except (AttributeError, OSError):
            pass
    return os.environ.get("USERNAME") or os.environ.get("USER") or "default"


def _runtime_dir() -> Path:
    if IS_WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or tempfile.gettempdir()
    else:
        root = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or tempfile.gettempdir()
    path = Path(root) / f"{APP_LOCK_ID}-{_user_lock_suffix()}"
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not IS_WINDOWS:
            os.chmod(path, 0o700)
    except Exception:
        path = Path(tempfile.gettempdir()) / f"{APP_LOCK_ID}-{_user_lock_suffix()}"
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not IS_WINDOWS:
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
    return path


def lock_path() -> Path:
    return _runtime_dir() / _LOCK_FILE_NAME


def endpoint_name() -> str:
    digest = hashlib.sha256(
        f"{APP_LOCK_ID}:{_user_lock_suffix()}".encode("utf-8", errors="ignore")
    ).hexdigest()[:24]
    return f"{APP_LOCK_ID}-{digest}"


def _windows_mutex_name() -> str:
    digest = hashlib.sha256(_user_lock_suffix().encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"Local\\{APP_LOCK_ID}_{digest}_SingleInstance"


def _try_windows_mutex() -> bool | None:
    global _win_mutex_handle
    if not IS_WINDOWS:
        return None
    if _win_mutex_handle is not None:
        return True
    try:
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, _windows_mutex_name())
        if not handle:
            return None
        if int(kernel32.GetLastError()) == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _win_mutex_handle = handle
        return True
    except Exception:
        return None


def _lock_file_region(file_obj: IO[str]) -> bool:
    try:
        file_obj.seek(0)
        if IS_WINDOWS:
            import msvcrt

            try:
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl

        try:
            fcntl.lockf(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1)
            return True
        except OSError:
            return False
    except Exception:
        return False


def _unlock_file_region(file_obj: IO[str]) -> None:
    try:
        file_obj.seek(0)
        if IS_WINDOWS:
            import msvcrt

            try:
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        import fcntl

        try:
            fcntl.lockf(file_obj.fileno(), fcntl.LOCK_UN, 1)
        except OSError:
            pass
    except Exception:
        pass


def _current_identity() -> dict[str, Any]:
    created_at = time.time()
    try:
        import psutil

        created_at = float(psutil.Process(os.getpid()).create_time())
    except Exception:
        pass
    return {
        "schema": 2,
        "pid": os.getpid(),
        "created_at": created_at,
        "executable": os.path.abspath(sys.executable or ""),
        "argv0": os.path.abspath(sys.argv[0]) if sys.argv else "",
        "endpoint": endpoint_name(),
        "ipc_token": secrets.token_urlsafe(32),
    }


def _write_identity(file_obj: IO[str], identity: dict[str, Any]) -> None:
    file_obj.seek(0)
    file_obj.truncate(0)
    json.dump(identity, file_obj, ensure_ascii=False)
    file_obj.write("\n")
    file_obj.flush()
    try:
        os.fsync(file_obj.fileno())
    except OSError:
        pass
    try:
        os.chmod(lock_path(), 0o600)
    except OSError:
        pass


def read_identity() -> dict[str, Any]:
    path = lock_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def current_identity() -> dict[str, Any]:
    return dict(_identity)


def acquire() -> bool:
    """Acquire the current user's singleton lock.

    Returns ``True`` for the primary process and ``False`` when another primary
    process already owns the lock.
    """
    global _lock_file_handle, _lock_path, _identity
    if _lock_file_handle is not None or _win_mutex_handle is not None:
        return True

    mutex_result = _try_windows_mutex()
    if mutex_result is False:
        return False

    path = lock_path()
    _lock_path = path
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        file_obj = os.fdopen(fd, "r+", encoding="utf-8")
        if path.stat().st_size == 0:
            file_obj.write(" ")
            file_obj.flush()
    except Exception:
        file_obj = None

    if file_obj is None:
        # A successfully-created Windows mutex is still a valid kernel-owned
        # singleton.  On POSIX, fail open rather than blocking every launch due
        # to an unwritable temporary directory.
        if mutex_result is True:
            _identity = _current_identity()
            return True
        return True

    if not _lock_file_region(file_obj):
        file_obj.close()
        if mutex_result is True:
            # The mutex proves this process is primary; a stale/unlockable file
            # should not defeat it.
            _identity = _current_identity()
            return True
        return False

    _lock_file_handle = file_obj
    _identity = _current_identity()
    try:
        _write_identity(file_obj, _identity)
    except Exception:
        # The lock itself remains authoritative even if metadata writing fails.
        pass
    return True


def release() -> None:
    global _lock_file_handle, _lock_path, _win_mutex_handle, _identity
    if _win_mutex_handle is not None:
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(_win_mutex_handle)
        except Exception:
            pass
        _win_mutex_handle = None
    if _lock_file_handle is not None:
        try:
            _unlock_file_region(_lock_file_handle)
        finally:
            try:
                _lock_file_handle.close()
            except Exception:
                pass
        _lock_file_handle = None
    _lock_path = None
    _identity = {}
