"""System-native HTML wallpaper process adapter for macOS.

The adapter validates sources, owns the system-native HTML child process, persists
verified process identity, and forwards hot options. The shared
``native_html_runner.py`` delegates AppKit/Quartz desktop-layer placement and
mouse-through behavior to ``backends/macos/native_webview_desktop.py``. Startup is
rejected when the PyObjC bridge is unavailable.
"""

from __future__ import annotations

import importlib.util
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

        return logging.getLogger("platform_adapters.html_wallpaper")


try:
    import psutil  # optional dependency for robust process termination
except ImportError:  # pragma: no cover - optional
    psutil = None


_DATA_DIR = os.fspath(APP_DATA_DIR)
os.makedirs(_DATA_DIR, exist_ok=True)
from platform_adapters import process_state
from platform_adapters.html_runtime import (
    missing_runtime_modules,
    normalize_html_source,
    select_html_runtime,
    source_runtime_path,
)

PROCESS_KIND = "html-wallpaper-macos"
PID_FILE = app_data_path("html_wallpaper.pid")
_OPTIONS_FILE = app_data_path("html_wallpaper_options.json")
_SUBPROCESS_LOG = app_data_path("html_wallpaper_subprocess.log")

# Track the currently running child process.  When launching a new HTML
# wallpaper the previous subprocess will be explicitly terminated via
# stop_html_wallpaper.  Without this, abandoned renderer processes may
# continue consuming CPU/memory resources on macOS.
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
    """Return whether *path* is a supported local or remote HTML source."""
    return normalize_html_source(path) is not None


def _read_state() -> dict[str, object]:
    return process_state.read_state(PID_FILE)


def _write_state(pid: int, path: str = "") -> None:
    process_state.write_state(
        PID_FILE,
        pid,
        kind=PROCESS_KIND,
        extra={"path": str(path or "")},
    )


def _read_pid() -> int | None:
    try:
        pid = _read_state().get("pid")
        return int(pid) if isinstance(pid, (str, int)) and pid else None
    except (TypeError, ValueError):
        return None


def _is_process_alive(pid: int | None) -> bool:
    """Compatibility probe that validates the persisted process identity."""
    if not pid:
        return False
    state = process_state.read_state(PID_FILE)
    try:
        if int(state.get("pid") or 0) != int(pid):
            return False
    except (TypeError, ValueError):
        return False
    return process_state.process_for_state(state, expected_kind=PROCESS_KIND) is not None


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


def stop_html_wallpaper() -> None:
    """Stop only the exact renderer process previously launched by this app."""
    global _CURRENT_PROC
    _hw_logger().info("stop_html_wallpaper called, current pid=%s", _read_pid())
    proc = _CURRENT_PROC
    _CURRENT_PROC = None

    # Persisted identity handles crash recovery and terminates the verified
    # process tree.  Legacy PID-only state is intentionally never destructive.
    process_state.terminate_verified(PID_FILE, expected_kind=PROCESS_KIND)

    # A live Popen object is also an exact process capability owned by this
    # parent, so it is safe as a final fallback when state persistence failed.
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        except Exception:
            pass

    process_state.remove_state(PID_FILE)
    try:
        clear_options()
    except Exception:
        _hw_logger().debug("ignored exception in %s", "html_wallpaper", exc_info=True)


def is_html_wallpaper_running() -> bool:
    if _CURRENT_PROC is not None and _CURRENT_PROC.poll() is None:
        return True
    return process_state.is_running(PID_FILE, expected_kind=PROCESS_KIND)


def start_html_wallpaper(path: str) -> Tuple[bool, str]:
    options_snapshot = _read_options_all()
    selected_runtime = select_html_runtime()
    if selected_runtime.name == "disabled":
        return False, "当前构建未包含 HTML 壁纸功能"
    missing_modules = missing_runtime_modules(selected_runtime)
    if missing_modules:
        return False, (
            f"当前构建缺少 {selected_runtime.name} HTML 运行时依赖："
            + ", ".join(missing_modules)
            + "。请安装所选 HTML 运行时依赖，或使用匹配的 full 构建。"
        )
    missing_native = [name for name in ("objc", "AppKit", "Quartz") if importlib.util.find_spec(name) is None]
    if missing_native:
        return False, (
            "缺少 macOS HTML 桌面层依赖："
            + ", ".join(missing_native)
            + "。普通无边框窗口不能被当作壁纸；请安装 requirements/macos.txt 中的 PyObjC 依赖。"
        )
    if not validate_html_path(path):
        _hw_logger().warning("start_html_wallpaper rejected invalid path=%s", path)
        return False, "无效的 HTML 文件或 URL"
    stop_html_wallpaper()
    for _key, _default in {"auto_pause": True, "frame_rate": 30}.items():
        runtime_set_option(_key, options_snapshot.get(_key, _default))
    # Determine child command.  In packaged mode there may be no loose
    # the selected HTML runner beside the executable, so re-enter the bundled app
    # through main.py's internal dispatcher.  In source mode, keep the simple
    # python + script path flow for developer runs.
    if is_packaged_runtime():
        base_cmd = [app_executable_path(), selected_runtime.internal_flag]
        script_path = f"<bundled:{selected_runtime.name}>"
    else:
        python_exe = _source_python_executable()
        runtime_path = source_runtime_path(__file__, selected_runtime)
        script_path = os.fspath(runtime_path)
        if not runtime_path.is_file():
            _hw_logger().error("HTML runtime not found at %s", script_path)
            return False, f"无法找到运行脚本: {script_path}"
        base_cmd = [python_exe, script_path]
    auto_pause = bool(options_snapshot.get("auto_pause", True))
    try:
        frame_rate = int(options_snapshot.get("frame_rate", 30))
    except (TypeError, ValueError):
        frame_rate = 30
    if frame_rate not in {0, 15, 24, 30, 45, 60}:
        frame_rate = 30
    cmd = [*base_cmd, "--path", path, "--frame-rate", str(frame_rate)]
    if auto_pause:
        cmd.append("--auto-pause")
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
        log_file.write(f"\n{'=' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting HTML wallpaper subprocess\n")
        log_file.write(
            f"auto_pause={auto_pause} frame_rate={frame_rate}\n"
        )
        log_file.write(f"runtime={selected_runtime.name} runner={script_path}\n")
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
    # Store process for later termination and record its PID.
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
