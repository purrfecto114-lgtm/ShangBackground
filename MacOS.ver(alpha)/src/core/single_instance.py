# single_instance.py
"""普通权限单实例守卫 - macOS 版。

设计目标：
- 不依赖管理员权限。
- 主锁使用当前用户运行目录中的系统文件锁（fcntl）；进程退出或崩溃后由系统自动释放。
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


def _user_lock_suffix() -> str:
    try:
        return str(os.getuid())
    except OSError:
        pass
    return os.environ.get("USER") or "default"


def _runtime_dir() -> Path:
    """返回当前用户私有且可写的运行时目录。"""
    root = os.environ.get("TMPDIR") or tempfile.gettempdir()
    dirname = f"{APP_LOCK_ID}-{_user_lock_suffix()}"
    path = Path(root) / dirname
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    except Exception:
        path = Path(tempfile.gettempdir()) / dirname
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
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
        import fcntl
        try:
            fcntl.lockf(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1)
            return True
        except OSError:
            return False
    except Exception:
        return False


def _unlock_file_region(file_obj) -> None:
    try:
        file_obj.seek(0)
        import fcntl
        try:
            fcntl.lockf(file_obj.fileno(), fcntl.LOCK_UN, 1)
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
    digest = hashlib.sha256(f"{APP_LOCK_ID}:{user}".encode("utf-8", errors="ignore")).digest()
    return 42000 + (int.from_bytes(digest[:2], "big") % 12000)


def _try_loopback_lock() -> bool | None:
    """尝试绑定本机回环端口。True=已锁定，False=已有监听者，None=不可用但不阻塞启动。"""
    global _loopback_socket
    if _loopback_socket is not None:
        return True
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    if _lock_file_handle is not None:
        return True

    path = _runtime_dir() / _LOCK_FILE_NAME
    _lock_path = path
    try:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
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
    global _lock_file_handle, _loopback_socket, _lock_path
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
