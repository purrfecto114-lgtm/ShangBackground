"""HTML wallpaper adapter for Windows platform.

This module provides functions to launch an interactive HTML wallpaper on the Windows
desktop using Qt WebEngine.  It spawns an external process to host the WebEngine
view, writes its PID to a file for later control, and exposes helpers to stop
and query the running state.  Remote URLs (http/https) and local HTML files are
accepted.  When the feature is unavailable (missing dependencies), callers
should handle the raised exceptions gracefully.

Because the WebEngine integration is isolated in a separate process, this module
does not import PySide6 at import time.  The runtime script is `run_html_wallpaper.py`
which must reside alongside this module.  The script creates a full‑screen
borderless window and loads the requested URL or file.

NOTE: Embedding a WebEngine view directly into the desktop layer is complex.  The
implementation here positions the window behind most windows but above the
desktop background.  Depending on Windows Explorer versions and system
configuration the behaviour may vary.  Users should test in their environment.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from pathlib import Path
from typing import Tuple

from app.paths import APP_DATA_DIR, app_data_path, is_packaged_runtime, app_executable_path
from app.log_setup import get_html_wallpaper_logger as _get_hw_logger

# Lazy logger accessor: log_setup.configure_logging() may not have been
# called yet at import time (this module is imported by core.engine which
# is imported very early). The first log call will trigger configuration.
def _hw_logger():
    try:
        return _get_hw_logger()
    except Exception:
        import logging
        return logging.getLogger('platform_adapters.html_wallpaper')

try:
    import psutil  # optional dependency for robust process termination
except ImportError:  # pragma: no cover - psutil optional
    psutil = None


_DATA_DIR = os.fspath(APP_DATA_DIR)
os.makedirs(_DATA_DIR, exist_ok=True)
PID_FILE = app_data_path("html_wallpaper.pid")
_OPTIONS_FILE = app_data_path("html_wallpaper_options.json")
_SUBPROCESS_LOG = app_data_path("html_wallpaper_subprocess.log")

# Track the currently running child process.  When a new HTML wallpaper is
# launched the previous subprocess will be explicitly terminated via
# stop_html_wallpaper.  Without this, old WebEngine processes can linger
# and continue using CPU/GPU resources.
_CURRENT_PROC: subprocess.Popen | None = None  # type: ignore


def _child_env(role: str) -> dict[str, str]:
    env = os.environ.copy()
    env["SHANGBACKGROUND_PROCESS_ROLE"] = role
    env["SHANGBACKGROUND_PARENT_PID"] = str(os.getpid())
    return env


def _source_python_executable() -> str:
    exe = sys.executable or sys.argv[0]
    if sys.platform.startswith("win"):
        try:
            path = Path(exe)
            if path.name.lower() == "python.exe":
                pythonw = path.with_name("pythonw.exe")
                if pythonw.is_file():
                    return os.fspath(pythonw)
        except Exception:
            pass
    return exe


def _secure_pid_file_permissions(path: str = PID_FILE) -> None:
    """Best-effort: keep runtime PID/state/options files readable only by current user where supported."""
    try:
        os.chmod(path, 0o600)
    except (AttributeError, OSError):
        pass


def validate_html_path(path: str | None) -> bool:
    """Return True when *path* refers to a supported HTML resource.

    This helper accepts remote URLs (http/https), file URIs (``file://``)
    and plain local file paths.  Historically only http/https and bare
    file paths were accepted, which caused the validation to reject
    ``file://`` URIs returned by some file pickers or pasted from
    browsers.  The caller should pass the original string on to the
    runtime process; ``run_html_wallpaper.py`` handles both ``file://``
    and local paths via QUrl.
    """
    if not path:
        return False
    path_str: str = str(path).strip()
    if not path_str:
        return False
    lower = path_str.lower()
    # Accept remote http/https URLs verbatim
    if lower.startswith(("http://", "https://")):
        return True
    # Accept file:// URIs by parsing out the local path and verifying
    # that it points at an existing HTML file.  Use urllib.parse to
    # handle Windows drive letters and percent-encoded paths.
    if lower.startswith("file://"):
        try:
            from urllib.parse import urlparse, unquote
            parsed = urlparse(path_str)
            local_path = unquote(parsed.path)
            # On Windows, urlparse leaves a leading slash before the
            # drive letter (e.g. '/C:/folder/file.html'); strip it.
            if os.name == 'nt' and local_path.startswith('/') and len(local_path) > 3 and local_path[2] == ':':
                local_path = local_path.lstrip('/')
            return os.path.isfile(local_path) and local_path.lower().endswith(('.html', '.htm'))
        except Exception:
            return False
    # Accept plain local file paths with .html or .htm extension
    return os.path.isfile(path_str) and lower.endswith(('.html', '.htm'))


def _read_state() -> dict[str, object]:
    """Read the persisted state from the PID file."""
    try:
        raw = Path(PID_FILE).read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        return {"pid": int(raw)}
    except Exception:
        return {}


def _write_state(pid: int, path: str = "") -> None:
    """Persist the running PID for later lookup.  ``path`` is optionally
    recorded so the GUI can restart the wallpaper with the same URL/file
    after a non-hot-reloadable setting (e.g. GPU) is toggled.
    """
    try:
        Path(PID_FILE).write_text(
            json.dumps({"pid": int(pid), "path": str(path or "")}, ensure_ascii=False),
            encoding="utf-8",
        )
        _secure_pid_file_permissions(PID_FILE)
        _secure_pid_file_permissions()
    except Exception:
        # fallback: write plain pid number
        try:
            Path(PID_FILE).write_text(str(int(pid)), encoding="utf-8")
            _secure_pid_file_permissions(PID_FILE)
            _secure_pid_file_permissions()
        except Exception:
            pass


def _read_pid() -> int | None:
    """Return the stored PID if present and valid."""
    try:
        pid = _read_state().get("pid")
        return int(pid) if pid else None
    except Exception:
        return None


def _is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    # Prefer psutil when available
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except Exception:
            pass
    # Fallback: on Windows use OpenProcess/CloseHandle via ctypes
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)
    return False


def _terminate_pid(pid: int | None) -> None:
    if not pid:
        return
    if not _is_process_alive(pid):
        return
    # Terminate using psutil when available
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            # Terminate children first
            children = proc.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            proc.terminate()
            _gone, alive = psutil.wait_procs(children + [proc], timeout=3)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
            return
        except psutil.NoSuchProcess:
            return
        except Exception:
            pass
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    except Exception:
        pass

    # Fallback: use ctypes TerminateProcess
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)



def _tail_subprocess_log(max_chars: int = 3500) -> str:
    """Return the tail of the HTML renderer log for actionable startup errors."""
    try:
        text = Path(_SUBPROCESS_LOG).read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:].strip()
    except Exception:
        return ""


def get_subprocess_log_path() -> str:
    """Return the persistent log path used by the HTML renderer subprocess."""
    return str(_SUBPROCESS_LOG)


def _qt_webengine_available() -> bool:
    """Check the two Qt WebEngine modules required by the renderer."""
    try:
        import importlib.util

        return (
            importlib.util.find_spec("PySide6.QtWebEngineCore") is not None
            and importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
        )
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False

def stop_html_wallpaper() -> None:
    """
    Stop the running HTML wallpaper process, if any, and clean up state files.

    This implementation first attempts to terminate any stored subprocess
    object (_CURRENT_PROC) and wait for it to exit.  Afterwards it falls
    back to terminating the process using the recorded PID.  Clearing both
    the in-memory reference and the PID file ensures that stale
    WebEngine/Chromium processes do not accumulate.
    """
    _hw_logger().info("stop_html_wallpaper called, current pid=%s", _read_pid())
    global _CURRENT_PROC
    pid = _read_pid()
    # Prefer to terminate the stored Popen instance, if present.
    if _CURRENT_PROC is not None:
        try:
            _CURRENT_PROC.terminate()
            try:
                _CURRENT_PROC.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass
        _CURRENT_PROC = None
    # Fall back to killing via PID if necessary
    if pid:
        _terminate_pid(pid)
    # Remove the PID/state file
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)
    # Clear runtime control options to avoid stale settings on next launch
    try:
        clear_options()
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)


def is_html_wallpaper_running() -> bool:
    """Return True if the HTML wallpaper process is alive."""
    return _is_process_alive(_read_pid())


def start_html_wallpaper(path: str) -> Tuple[bool, str]:
    """Launch the HTML wallpaper.  Returns (success, message).

    If a wallpaper is already running the previous process will be terminated.
    The provided path may be a local file or a remote http/https URL.  The
    caller is responsible for validating the path via `validate_html_path`.

    The following options are read from the persisted runtime options file
    and forwarded to the child script:

    * ``auto_pause``  (default True)  — freeze the page when desktop is unfocused.
    * ``gpu_enabled`` (default True)  — toggle Qt WebEngine GPU acceleration.
      GPU can't be hot-reloaded, so changing it from the GUI triggers a
      restart of the wallpaper subprocess.
    """
    if not _qt_webengine_available():
        return False, "当前构建不包含 Qt WebEngine；请安装 PySide6-Addons 或使用 full 构建。"
    _hw_logger().info("start_html_wallpaper path=%s", path)
    if not validate_html_path(path):
        _hw_logger().warning("start_html_wallpaper rejected invalid path=%s", path)
        return False, "无效的 HTML 文件或 URL"
    options_snapshot = _read_options_all()
    # Stop any existing wallpaper
    stop_html_wallpaper()
    for _key, _default in {"auto_pause": True, "gpu_enabled": True, "mouse_through": True}.items():
        runtime_set_option(_key, options_snapshot.get(_key, _default))
    # Determine child command.  In Nuitka/PyInstaller packaged mode there may
    # be no loose run_html_wallpaper.py file beside the executable.  Re-enter the
    # bundled EXE with an internal dispatch flag instead.
    if is_packaged_runtime():
        base_cmd = [app_executable_path(), "--internal-html-wallpaper-runner"]
        script_path = "<bundled:platform_adapters.run_html_wallpaper>"
    else:
        python_exe = _source_python_executable()
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "run_html_wallpaper.py")
        if not os.path.exists(script_path):
            _hw_logger().error("run_html_wallpaper.py not found at %s", script_path)
            return False, f"无法找到运行脚本: {script_path}"
        base_cmd = [python_exe, script_path]
    # 读取运行时选项：默认开启自动暂停 + GPU 加速
    auto_pause = bool(options_snapshot.get("auto_pause", True))
    gpu_enabled = bool(options_snapshot.get("gpu_enabled", True))
    mouse_through = bool(options_snapshot.get("mouse_through", True))
    # Launch the script as a detached subprocess
    creationflags = 0
    try:
        # Windows-specific: detach from console and avoid showing a window
        import subprocess as sp
        creationflags = sp.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    except Exception:
        creationflags = 0
    cmd = [
        *base_cmd,
        "--path",
        path,
    ]
    if auto_pause:
        cmd.append("--auto-pause")
    # 二选一：默认 Chromium 启用 GPU，仅当用户显式禁用时传 --disable-gpu。
    if gpu_enabled:
        cmd.append("--enable-gpu")
    else:
        cmd.append("--disable-gpu")
    # 启动参数必须和运行时控制文件保持一致，否则子进程首轮轮询前
    # 会短暂使用默认 click-through 状态，交互式 HTML 壁纸体验不稳定。
    cmd.append("--mouse-through" if mouse_through else "--no-mouse-through")
    global _CURRENT_PROC
    # v1.4.8: 把子进程 stderr 重定向到日志文件。
    # 自动删除：日志文件超过 512KB 时截断保留最后 128KB，避免无限增长。
    log_file = None
    try:
        log_path = _SUBPROCESS_LOG
        # 自动删除/截断：超过 512KB 保留最后 128KB
        try:
            if os.path.exists(log_path):
                sz = os.path.getsize(log_path)
                if sz > 512 * 1024:
                    with open(log_path, "rb") as _f:
                        _f.seek(max(0, sz - 128 * 1024))
                        _tail = _f.read()
                    with open(log_path, "wb") as _f:
                        _f.write(b"[... truncated ...]\n")
                        _f.write(_tail)
        except Exception:
            pass
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        log_file.write(f"\n{'='*60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting HTML wallpaper subprocess\n")
        log_file.write(f"auto_pause={auto_pause} gpu_enabled={gpu_enabled} mouse_through={mouse_through}\n")
        log_file.write(f"runner={script_path}\n")
        log_file.flush()
    except Exception:
        log_file = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file if log_file is not None else subprocess.DEVNULL,
            stderr=log_file if log_file is not None else subprocess.DEVNULL,
            creationflags=creationflags,
            env=_child_env("html-wallpaper"),
        )
    except Exception as exc:
        _hw_logger().error("failed to spawn html wallpaper subprocess: %s", exc, exc_info=True)
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        return False, str(exc)
    # Store the process for proper termination later and write its PID to disk.
    _CURRENT_PROC = proc
    _write_state(proc.pid, path=str(path))
    # Keep the parent-side handle open briefly so startup failures are captured,
    # then close it to avoid leaking a descriptor for the lifetime of the app.
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    exit_code = proc.poll()
    if log_file is not None:
        try:
            log_file.close()
        except Exception:
            pass
    if exit_code is not None:
        _CURRENT_PROC = None
        try:
            Path(PID_FILE).unlink(missing_ok=True)
        except OSError:
            pass
        tail = _tail_subprocess_log()
        detail = f"HTML 渲染进程启动失败（退出码 {exit_code}）。日志：{_SUBPROCESS_LOG}"
        if tail:
            detail += "\n\n" + tail
        _hw_logger().error(detail)
        return False, detail
    _hw_logger().info("html wallpaper started pid=%s path=%s", proc.pid, path)
    return True, ""


# ---------- 运行时控制：父进程通过控制文件热更新子进程选项 ----------


def _read_runtime_option(key: str, default):
    """Read a single option from the options file (used at child startup)."""
    data = _read_options_all()
    try:
        return data.get(key, default)
    except Exception:
        return default


def _read_options_all() -> dict:
    try:
        if not os.path.exists(_OPTIONS_FILE):
            return {}
        with open(_OPTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def runtime_set_option(key: str, value) -> bool:
    """Write an option to the options file.

    The child process polls this file every ~1.5s and applies changes
    dynamically (currently supports ``auto_pause``).  Returns True if the
    file was successfully written.
    """
    try:
        data = _read_options_all()
        data[str(key)] = value
        # stamp 写入时间，子进程可借此判断是否有新变更
        data["_updated_at"] = time.time() if globals().get("time") else 0
        tmp = _OPTIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _OPTIONS_FILE)
        _secure_pid_file_permissions(_OPTIONS_FILE)
        return True
    except Exception:
        return False


def get_last_path() -> str:
    """Return the path the wallpaper was last started with, if recorded.

    Used by the GUI to restart the wallpaper (e.g. after toggling GPU) when
    the caller doesn't have the original path handy.
    """
    try:
        data = _read_state()
        if isinstance(data, dict):
            return str(data.get("path") or "")
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)
    return ""


def restart_html_wallpaper(path: str | None = None) -> Tuple[bool, str]:
    """Stop and re-launch the wallpaper with the same or a new path.

    Used by the GUI when a setting that can't be hot-reloaded (e.g. GPU
    acceleration) is changed.  Falls back to the last-known path when
    ``path`` is None.
    """
    target = path or get_last_path()
    if not target:
        return False, "无可用的 HTML 路径以重启"
    return start_html_wallpaper(target)


def clear_options() -> None:
    """Remove the options file (called on stop)."""
    try:
        if os.path.exists(_OPTIONS_FILE):
            os.remove(_OPTIONS_FILE)
    except Exception:
        pass