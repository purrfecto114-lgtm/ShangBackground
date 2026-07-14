"""HTML wallpaper adapter for Linux platform.

This module provides an abstraction for launching and controlling an
interactive HTML wallpaper on Linux.  It spawns an external process to host
the WebEngine view and writes its PID to a file for later control.  Local
HTML files and remote http/https URLs are supported.  When dependencies
such as the Qt WebEngine modules are missing, callers should handle the raised
exceptions gracefully.

The implementation is intentionally simple: it launches a borderless full
screen window using Qt WebEngine.  Advanced integrations such as using
xwinwrap to embed the window behind desktop icons are outside the scope of
this adapter.  Users on desktop environments that support xwinwrap may
choose to modify `run_html_wallpaper.py` accordingly.
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

# Track the currently running child process for the HTML wallpaper.  When a new
# wallpaper is launched the previous process will be explicitly terminated via
# stop_html_wallpaper to avoid orphaned Python/QWebEngine processes that can
# accumulate and consume CPU/memory.
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
    """Best-effort: keep runtime PID/state files readable only by current user."""
    try:
        os.chmod(path, 0o600)
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)


def validate_html_path(path: str | None) -> bool:
    """Return True when *path* refers to a supported HTML resource.

    Accepts remote URLs (http/https), ``file://`` URIs and plain local
    file paths.  Some desktop environments and file pickers return
    ``file://`` URIs; older versions of ShangBackground rejected these
    even when the underlying file existed.  This helper normalises
    and validates each form.
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
    # Accept file:// URIs
    if lower.startswith("file://"):
        try:
            from urllib.parse import urlparse, unquote
            parsed = urlparse(path_str)
            local_path = unquote(parsed.path)
            # On Windows remove the leading slash before the drive letter;
            # on Linux/Unix leave the path as-is.
            if os.name == 'nt' and local_path.startswith('/') and len(local_path) > 3 and local_path[2] == ':':
                local_path = local_path.lstrip('/')
            return os.path.isfile(local_path) and local_path.lower().endswith(('.html', '.htm'))
        except Exception:
            return False
    # Accept plain local file paths with .html or .htm extension
    return os.path.isfile(path_str) and lower.endswith(('.html', '.htm'))


def _read_state() -> dict[str, object]:
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
    try:
        Path(PID_FILE).write_text(
            json.dumps({"pid": int(pid), "path": str(path or "")}, ensure_ascii=False),
            encoding="utf-8",
        )
        _secure_pid_file_permissions(PID_FILE)
    except Exception:
        try:
            Path(PID_FILE).write_text(str(int(pid)), encoding="utf-8")
            _secure_pid_file_permissions(PID_FILE)
        except Exception:
            pass


def _read_pid() -> int | None:
    try:
        pid = _read_state().get("pid")
        return int(pid) if pid else None
    except Exception:
        return None


def _is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except Exception:
            pass
    # Fallback: check /proc
    try:
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        return False


def _terminate_pid(pid: int | None) -> None:
    """Terminate the given PID and its children if possible.

    This helper tries to gracefully terminate the process using psutil when
    available, falling back to signals.  If the process is still alive
    after SIGTERM it will attempt to force kill it using SIGKILL.
    """
    if not pid:
        return
    if not _is_process_alive(pid):
        return
    # Prefer using psutil when present to terminate the process tree
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            proc.terminate()
            _gone, alive = psutil.wait_procs([proc] + children, timeout=2)
            # Force kill any survivors
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
        os.killpg(os.getpgid(pid), 15)
        time.sleep(0.5)
        if not _is_process_alive(pid):
            return
        os.killpg(os.getpgid(pid), 9)
        return
    except Exception:
        pass
    # Fallback: send SIGTERM then SIGKILL if still alive
    try:
        os.kill(pid, 15)
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)
    # If still alive after a short delay, send SIGKILL
    try:
        time.sleep(0.5)
        if _is_process_alive(pid):
            os.kill(pid, 9)
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
    Terminate any running HTML wallpaper process and clear state files.

    This helper terminates both the tracked subprocess (if any) and the process
    whose PID is recorded in the PID file.  After termination the PID/state
    and runtime option files are removed.  Explicitly cleaning up the
    subprocess helps avoid stray Python and QWebEngine processes persisting in
    the background when the wallpaper is stopped or restarted.
    """
    _hw_logger().info("stop_html_wallpaper called, current pid=%s", _read_pid())
    global _CURRENT_PROC
    pid = _read_pid()
    # If we have a stored Popen instance, terminate it gracefully and wait
    # for it to exit. Afterwards set it to None so future calls won't try to
    # kill an already-dead process. Use a short timeout to avoid blocking
    # indefinitely; if the process refuses to die, fall back to killing by
    # PID below.
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
    # Also kill the PID recorded on disk (may be the same as _CURRENT_PROC.pid).
    if pid:
        _terminate_pid(pid)
    # Remove state and options files.
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)
    try:
        clear_options()
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)


def is_html_wallpaper_running() -> bool:
    return _is_process_alive(_read_pid())


def start_html_wallpaper(path: str) -> Tuple[bool, str]:
    """Launch the HTML wallpaper process.  Returns (success, message).

    Reads ``auto_pause`` and ``gpu_enabled`` from the runtime options file
    and forwards them to the child script via ``--auto-pause`` /
    ``--enable-gpu`` (or ``--disable-gpu``).
    """
    if not _qt_webengine_available():
        return False, "当前构建不包含 Qt WebEngine；请安装 PySide6-Addons 或使用 full 构建。"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" and os.environ.get("SHANGBACKGROUND_ALLOW_UNSAFE_WAYLAND_HTML", "") != "1":
        return False, (
            "当前 HTML 后端只是 Qt 顶层窗口，无法在通用 Wayland/XDG Shell 下可靠定位到桌面背景层。"
            "请切换 X11；或等待针对 GNOME/KDE/layer-shell 的专用后端。"
        )
    _hw_logger().info("start_html_wallpaper path=%s", path)
    if not validate_html_path(path):
        _hw_logger().warning("start_html_wallpaper rejected invalid path=%s", path)
        return False, "无效的 HTML 文件或 URL"
    options_snapshot = _read_options_all()
    # Stop any existing wallpaper before launching a new one.  Use the helper
    # rather than killing the PID directly so the tracked Popen object is also
    # cleaned up.
    stop_html_wallpaper()
    for _key, _default in {"auto_pause": True, "gpu_enabled": True, "mouse_through": True}.items():
        runtime_set_option(_key, options_snapshot.get(_key, _default))
    # Determine child command.  In packaged mode there may be no loose
    # run_html_wallpaper.py beside the executable, so re-enter the bundled app
    # through main.py's internal dispatcher.  In source mode, keep the simple
    # python + script path flow for developer runs.
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
    auto_pause = bool(options_snapshot.get("auto_pause", True))
    gpu_enabled = bool(options_snapshot.get("gpu_enabled", True))
    mouse_through = bool(options_snapshot.get("mouse_through", True))
    cmd = [*base_cmd, "--path", path]
    if auto_pause:
        cmd.append("--auto-pause")
    cmd.append("--enable-gpu" if gpu_enabled else "--disable-gpu")
    cmd.append("--mouse-through" if mouse_through else "--no-mouse-through")
    global _CURRENT_PROC
    # v1.4.8: 子进程 stderr → 日志文件，自动截断（>512KB 保留最后 128KB）。
    log_file = None
    try:
        _log_path = _SUBPROCESS_LOG
        try:
            if os.path.exists(_log_path):
                sz = os.path.getsize(_log_path)
                if sz > 512 * 1024:
                    with open(_log_path, "rb") as _f:
                        _f.seek(max(0, sz - 128 * 1024))
                        _tail = _f.read()
                    with open(_log_path, "wb") as _f:
                        _f.write(b"[... truncated ...]\n")
                        _f.write(_tail)
        except Exception:
            pass
        log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
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
            start_new_session=True,
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
    # Persist the Popen object for later termination; also write its PID for
    # backwards compatibility with older control files.
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


def _read_options_all() -> dict:
    try:
        if not os.path.exists(_OPTIONS_FILE):
            return {}
        with open(_OPTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_runtime_option(key: str, default):
    try:
        return _read_options_all().get(key, default)
    except Exception:
        return default


def runtime_set_option(key: str, value) -> bool:
    """Write an option to the options file (polled by the child process)."""
    try:
        data = _read_options_all()
        data[str(key)] = value
        data["_updated_at"] = time.time()
        tmp = _OPTIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _OPTIONS_FILE)
        _secure_pid_file_permissions(_OPTIONS_FILE)
        return True
    except Exception:
        return False


def get_last_path() -> str:
    """Return the path the wallpaper was last started with, if recorded."""
    try:
        data = _read_state()
        if isinstance(data, dict):
            return str(data.get("path") or "")
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)
    return ""


def restart_html_wallpaper(path: str | None = None) -> Tuple[bool, str]:
    """Stop and re-launch the wallpaper with the same or a new path."""
    target = path or get_last_path()
    if not target:
        return False, "无可用的 HTML 路径以重启"
    return start_html_wallpaper(target)


def clear_options() -> None:
    try:
        if os.path.exists(_OPTIONS_FILE):
            os.remove(_OPTIONS_FILE)
    except Exception:
        pass