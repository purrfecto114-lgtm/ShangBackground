# single_instance.py
"""普通权限单实例守卫 - Windows 版。

设计目标：
- 不依赖管理员权限，不使用 Global\\ 命名对象。
- 主锁使用当前用户运行目录中的系统文件锁；进程退出或崩溃后由系统自动释放。
- Windows 优先使用 Local 命名互斥体：系统级自动释放、普通权限可用、VSCode/python.exe/打包 exe 表现一致。
- 回环端口只作为辅助锁；仅绑定 127.0.0.1，不暴露到局域网。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

APP_LOCK_ID = "ShangBackground"
APP_MUTEX_NAME = APP_LOCK_ID
_LOCK_FILE_NAME = "single_instance.lock"
_lock_file_handle = None
_loopback_socket = None
_lock_path: Path | None = None
_win_mutex_handle = None
_win_mutex_name = None


def _windows_mutex_name() -> str:
    """当前用户可用的 Local 命名互斥体名称；不需要管理员权限。"""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    sid = user
    try:
        import ctypes
        import ctypes.wintypes
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32
        token = ctypes.wintypes.HANDLE()
        TOKEN_QUERY = 0x0008
        if advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            try:
                TokenUser = 1
                needed = ctypes.wintypes.DWORD(0)
                advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(needed))
                buf = ctypes.create_string_buffer(max(needed.value, 1))
                if advapi32.GetTokenInformation(token, TokenUser, buf, needed, ctypes.byref(needed)):
                    class SID_AND_ATTRIBUTES(ctypes.Structure):
                        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.wintypes.DWORD)]
                    sid_attr = ctypes.cast(buf, ctypes.POINTER(SID_AND_ATTRIBUTES)).contents
                    string_sid = ctypes.wintypes.LPWSTR()
                    if advapi32.ConvertSidToStringSidW(sid_attr.Sid, ctypes.byref(string_sid)):
                        try:
                            sid = string_sid.value or user
                        finally:
                            kernel32.LocalFree(string_sid)
            finally:
                kernel32.CloseHandle(token)
    except Exception:
        pass
    digest = hashlib.sha1(str(sid).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"Local\\{APP_LOCK_ID}_{digest}_SingleInstance"


def _try_windows_mutex() -> bool | None:
    """True=获得锁；False=已有实例；None=Win32 不可用，交给文件锁兜底。"""
    global _win_mutex_handle, _win_mutex_name
    if _win_mutex_handle is not None:
        return True
    try:
        import ctypes
        import ctypes.wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = ctypes.wintypes.DWORD
        name = _windows_mutex_name()
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return None
        ERROR_ALREADY_EXISTS = 183
        if int(kernel32.GetLastError()) == ERROR_ALREADY_EXISTS:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return False
        _win_mutex_handle = handle
        _win_mutex_name = name
        return True
    except Exception:
        return None


def _user_lock_suffix() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "default"


def _runtime_dir() -> Path:
    """返回当前用户私有且可写的运行时目录。"""
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or tempfile.gettempdir()
    dirname = APP_LOCK_ID
    path = Path(root) / dirname
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except Exception:
        path = Path(tempfile.gettempdir()) / dirname
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _current_identity() -> dict:
    return {
        "pid": os.getpid(),
        "created_at": time.time(),
        "executable": os.path.abspath(sys.executable or ""),
        "argv0": os.path.abspath(sys.argv[0]) if sys.argv else "",
    }


def _lock_file_region(file_obj) -> bool:
    """对锁文件前 1 字节加非阻塞独占锁；成功返回 True。"""
    try:
        file_obj.seek(0)
        import msvcrt
        try:
            msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    except Exception:
        return False


def _unlock_file_region(file_obj) -> None:
    try:
        file_obj.seek(0)
        import msvcrt
        try:
            msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    except Exception:
        pass


def _write_identity(file_obj) -> None:
    try:
        file_obj.seek(0)
        file_obj.truncate(0)
        json.dump(_current_identity(), file_obj, ensure_ascii=False)
        file_obj.write("\n")
        file_obj.flush()
        try:
            os.fsync(file_obj.fileno())
        except Exception:
            pass
    except Exception:
        pass


def _lock_port() -> int:
    user = _user_lock_suffix()
    digest = hashlib.sha1(f"{APP_LOCK_ID}:{user}".encode("utf-8", errors="ignore")).digest()
    return 42000 + (int.from_bytes(digest[:2], "big") % 12000)


def _try_loopback_lock() -> bool | None:
    """尝试绑定本机回环端口。True=已锁定，False=已有监听者，None=不可用但不阻塞启动。"""
    global _loopback_socket
    if _loopback_socket is not None:
        return True
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", _lock_port()))
        sock.listen(1)
        sock.setblocking(False)
        _loopback_socket = sock
        return True
    except OSError as exc:
        if getattr(exc, "errno", None) in {48, 98, 10048}:
            return False
        return None
    except Exception:
        return None


def acquire() -> bool:
    """尝试获取单实例锁；成功返回 True，已有实例返回 False。"""
    global _lock_file_handle, _lock_path
    if _win_mutex_handle is not None or _lock_file_handle is not None:
        return True

    mutex_result = _try_windows_mutex()
    if mutex_result is False:
        return False

    path = _runtime_dir() / _LOCK_FILE_NAME
    _lock_path = path
    try:
        flags = os.O_RDWR | os.O_CREAT
        fd = os.open(path, flags, 0o600)
        try:
            fh = os.fdopen(fd, "r+", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise
    except Exception:
        fh = None

    if fh is not None:
        if not _lock_file_region(fh):
            try:
                fh.close()
            except Exception:
                pass
            if mutex_result is True:
                return True
            return False
        _lock_file_handle = fh
        _write_identity(fh)
        loopback = _try_loopback_lock()
        if loopback is False:
            release()
            return False
        return True

    loopback = _try_loopback_lock()
    return loopback is not False


def release() -> None:
    """释放单实例锁。"""
    global _lock_file_handle, _loopback_socket, _lock_path, _win_mutex_handle, _win_mutex_name
    if _win_mutex_handle is not None:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_win_mutex_handle)
        except Exception:
            pass
        _win_mutex_handle = None
        _win_mutex_name = None
    if _loopback_socket is not None:
        try:
            _loopback_socket.close()
        except Exception:
            pass
        _loopback_socket = None
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
