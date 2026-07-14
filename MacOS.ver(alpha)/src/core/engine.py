# ShangBackground runtime core used by the PySide6 UI.
from __future__ import annotations

import ctypes
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import traceback

from core import random_probability as random_copy
from core import single_instance
from app.config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_GRADIENT_COLOR2,
    DEFAULT_SOLID_COLOR,
    DEFAULT_THEME_COLOR,
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    MODE_KEYS,
    normalize_mode_key,
    normalize_style_key,
)
from app.i18n import t
from app.paths import RESOURCE_ROOT, user_data_dir, is_packaged_runtime, app_executable_path
from platform_adapters.integration import (
    configure_fit_mode,
    get_current_wallpaper_platform,
    get_screen_size,
    refresh_shell_ui,
    set_wallpaper_platform,
)

try:
    import ctypes.wintypes
except ImportError:
    ctypes.wintypes = None

try:
    import winreg
except ImportError:
    winreg = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    from platform_adapters import video as video_wallpaper
except Exception:
    video_wallpaper = None

# HTML 动态壁纸适配器，可选。
try:
    from platform_adapters import html_wallpaper as html_wallpaper
except Exception:
    html_wallpaper = None

# Global hotkey support for non-Windows platforms using pynput.
try:
    from pynput import keyboard as _pynput_keyboard  # type: ignore
except Exception:
    _pynput_keyboard = None

_pynput_hotkey_listener = None  # type: ignore

# UI sidebar is intentionally not imported by the core.
# UI code imports ui.sidebar when it needs the widget.
WallpaperSidebar = None

# Windows消息常量
WM_COPYDATA = 0x004A
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SPI_GETDESKWALLPAPER = 0x0073
HWND_MESSAGE = -3  # message-only window parent; prevents the IPC window from appearing on screen/taskbar

# Win32 ctypes 类型在运行时会被 _configure_win32_ctypes() 更新；先提供兜底，避免局部变量未导出。
HWND = ctypes.c_void_p
HMENU = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HMODULE = ctypes.c_void_p
HANDLE = ctypes.c_void_p
BOOL = ctypes.c_int
DWORD = ctypes.c_ulong
UINT = ctypes.c_uint
LPCWSTR = ctypes.c_wchar_p
ATOM = ctypes.c_ushort
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t


# 定义WNDCLASS结构
class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ('style', ctypes.c_uint),
        ('lpfnWndProc', ctypes.c_void_p),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', ctypes.c_void_p),
        ('hIcon', ctypes.c_void_p),
        ('hCursor', ctypes.c_void_p),
        ('hbrBackground', ctypes.c_void_p),
        ('lpszMenuName', ctypes.c_wchar_p),
        ('lpszClassName', ctypes.c_wchar_p)
    ]


# 定义COPYDATASTRUCT结构
class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ('dwData', ctypes.c_size_t),
        ('cbData', ctypes.c_ulong),
        ('lpData', ctypes.c_void_p)
    ]


def is_frozen():
    return is_packaged_runtime()


def _resource_base_dir() -> str:
    """Return the branch resource root in source and bundled executions."""
    return os.fspath(RESOURCE_ROOT)


BASE_DIR = _resource_base_dir()
DATA_DIR = user_data_dir(APP_NAME)
try:
    random_copy.configure_storage(DATA_DIR)
except Exception as exc:
    log_message = f"随机概率配置目录初始化失败: {exc}"
    print(log_message)

# 全局常量
VERSION = APP_VERSION
CONFIG_PATH = os.path.join(DATA_DIR, "settings.json")
BUNDLED_CONFIG_PATH = os.path.join(BASE_DIR, "settings.json")
LEGACY_CONFIG_PATH = os.path.join(DATA_DIR, "shezhi.json")
LEGACY_BUNDLED_CONFIG_PATH = os.path.join(BASE_DIR, "shezhi.json")
# v1.4.6: 移除 TRIGGER_FILE_PREV/NEXT/RANDOM 和 ERROR_LOG_PATH —— 早期文件触发机制残留, 现已改用 IPC.
_MAX_LOG_FILE_BYTES = 1024 * 1024


# 日志文件写入已改为轻量运行时日志：开发模式输出控制台；用户开启后写入文件。
_LOG_THROTTLE_LOCK = threading.RLock()
_LOG_THROTTLE_STATE: dict[str, tuple[float, int]] = {}
_LOG_THROTTLE_SECONDS = 0.75
_LOG_FILE_LOCK = threading.RLock()


def _normalize_log_level(level: str | None) -> str:
    level = str(level or "INFO").upper().strip()
    return level if level in {"DEBUG", "INFO", "WARNING", "ERROR"} else "INFO"


def _format_exception_text(exc_info) -> str:
    if not exc_info:
        return ""
    if exc_info is True:
        text = traceback.format_exc()
        return "" if text == "NoneType: None\n" else text.rstrip()
    if isinstance(exc_info, BaseException):
        return "".join(traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__)).rstrip()
    return str(exc_info).rstrip()


def _append_log_file(path: str, line: str, *, rotate: bool = True) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if rotate and os.path.exists(path) and os.path.getsize(path) > _MAX_LOG_FILE_BYTES:
            backup = path + ".1"
            try:
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(path, backup)
            except Exception:
                pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _should_emit_log(message: str, level: str) -> bool:
    if level in {"WARNING", "ERROR"}:
        return True
    now = time.monotonic()
    key = str(message)
    with _LOG_THROTTLE_LOCK:
        last, count = _LOG_THROTTLE_STATE.get(key, (0.0, 0))
        if now - last < _LOG_THROTTLE_SECONDS:
            _LOG_THROTTLE_STATE[key] = (last, count + 1)
            return False
        _LOG_THROTTLE_STATE[key] = (now, 0)
        return True


def log(msg, level: str = "INFO", exc_info=False):
    """带时间戳的日志输出函数。

    历史实现用 print + 文件追加，本版本改为桥接到 `app.log_setup` 标准
    logging 模块，获得按天滚动、级别过滤、子日志分离等能力。对外 API
    保持不变以兼容所有现存调用点。

    同时保留“程序内日志”页的用户可选日志文件：当用户在设置中开启
    ``log_enabled`` 并选择 ``log_file_path`` 后，所有 core.log 调用都会
    追加到该文件，避免日志页读取的路径与标准 logging 实际写入路径不一致。
    """
    level = _normalize_log_level(level)
    message = str(msg)
    exc_text = _format_exception_text(exc_info)
    display_message = f"[{level}] {message}" if level != "INFO" else message
    if exc_text:
        display_message = display_message + "\n" + exc_text

    legacy_failed = False
    try:
        from app.log_setup import legacy as _legacy_logger
        _legacy_logger.log(msg, level=level, exc_info=exc_info)
    except Exception:
        legacy_failed = True

    try:
        cfg = globals().get("config", {})
        if isinstance(cfg, dict) and bool(cfg.get("log_enabled", False)):
            user_log_path = str(cfg.get("log_file_path", "") or "").strip()
            if user_log_path and _should_emit_log(display_message, level):
                timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
                _append_log_file(user_log_path, f"{timestamp} {display_message}")
    except Exception:
        pass

    if legacy_failed:
        try:
            if _should_emit_log(display_message, level):
                timestamp = time.strftime("[%H:%M:%S]")
                print(f"{timestamp} {display_message}")
        except Exception:
            pass
    return display_message





def log_error(context: str, exc: BaseException | None = None) -> None:
    """记录错误并保留堆栈；供三端统一使用。"""
    try:
        from app.log_setup import legacy as _legacy_logger
        _legacy_logger.log_error(context, exc)
        return
    except Exception:
        pass
    if exc is None:
        log(context, level="ERROR", exc_info=True)
    else:
        log(f"{context}: {exc}", level="ERROR", exc_info=exc)


# 最近一次壁纸操作的失败原因（空字符串表示最近一次操作成功）。
# 由 set_wallpaper_direct 等核心函数在失败路径上写入；GUI 层在向用户回显
# "操作失败" 类通用提示前可读取该字段，把具体原因一起带给用户。
last_operation_error: str = ""


# v1.4.6: 移除 apply_image_fit_mode —— 定义但零调用, 壁纸适应由系统层处理.


def show_message(title, msg):
    """显示消息；GUI 启动后优先回到 PySide6 主窗口 QMessageBox。"""
    try:
        if root is not None and hasattr(root, "show_message"):
            root.show_message(title, msg)
            return
    except Exception as exc:
        log(f"Qt 消息框显示失败，回退到日志: {exc}")
    log(f"{title}: {msg}")


# 全局变量
hwnd = None
WND_CLASS_NAME = "ShangBackgroundIpcWindowClass"
use_message = False
apply_timer = None
root = None
pending_action = None  # 用于存储待执行的动作（无主进程时）
hide_window = False  # 是否隐藏主窗口（由 --hide 参数控制）
canvas = None
slide_frame = None
shuffle_var = None
chk_next = None
chk_random = None
chk_prev = None
single_frame = None
gradient_frame = None
color1_var = None
color1_preview = None
color2_var = None
color2_preview = None
angle_var = None
solid_frame = None
solid_color_var = None
solid_color_preview = None
mode_var = None
fit_var = None
ctx_prev_var = None
ctx_next_var = None
ctx_random_var = None
wallpaper_monitor_running = False
wallpaper_monitor_last = None
hotkey_running = False
hotkey_thread = None
preview_images_frame = None
wallpaper_preview_labels = None
folder_entry = None
tray_icon_obj = None

_message_loop_thread = None
_session_wallpaper_lock = threading.RLock()
session_original_wallpaper = ""
session_original_wallpaper_style = {}
session_original_wallpaper_captured = False

# 记录“本次程序启动前的壁纸”。旧版放在 TEMP 下且所有权限/进程共用同名文件，
# 容易被旧会话、提权进程或其它实例污染；新版放入用户数据目录，并保留旧文件读取兼容。
SESSION_WALLPAPER_FILE = os.path.join(DATA_DIR, "session_original_wallpaper.json")
LEGACY_SESSION_WALLPAPER_FILE = os.path.join(tempfile.gettempdir(), "ShangBackground_session_wallpaper.json")

def _pynput_hotkey_string(hotkey_str: str) -> str | None:
    """Convert a 'Ctrl+Alt+N' style string into a pynput hotkey format.

    Returns None if the string is invalid or lacks a modifier+key combination.
    """
    if not hotkey_str:
        return None
    parts: list[str] = [p.strip() for p in hotkey_str.replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    keys: list[str] = []
    has_modifier = False
    has_key = False
    for p in parts:
        low = p.lower()
        if low in ("ctrl", "control"):
            keys.append("<ctrl>")
            has_modifier = True
        elif low == "alt":
            keys.append("<alt>")
            has_modifier = True
        elif low == "shift":
            keys.append("<shift>")
            has_modifier = True
        elif low in ("win", "meta", "super", "cmd", "command"):
            keys.append("<cmd>" if IS_MACOS else "<super>")
            has_modifier = True
        elif len(p) == 1 and p.isalpha():
            keys.append(p.lower())
            has_key = True
        elif len(p) == 1 and p.isdigit():
            keys.append(p)
            has_key = True
        elif p.upper().startswith("F") and p[1:].isdigit():
            keys.append(p.lower())
            has_key = True
        else:
            return None
    if not has_modifier or not has_key:
        return None
    return "+".join(keys)

def _on_pynput_global_hotkey(action: str):
    """Execute a registered hotkey action without blocking pynput's listener."""
    def _runner():
        try:
            log(f"全局热键触发: {action}")
            action_map = {
                "previous": previous_wallpaper,
                "next": next_wallpaper,
                "random": random_wallpaper,
                "mode": lambda: switch_wallpaper_mode("next"),
            }
            fn = action_map.get(action)
            if fn is not None:
                fn()
                try:
                    save_config()
                except Exception as exc:
                    log(f"全局热键动作后保存配置失败({action}): {exc}")
            elif action == "jump":
                if root is not None and hasattr(root, "after"):
                    root.after(0, lambda: _gui_open_wallpaper_sidebar())
                else:
                    log("无法触发跳转到壁纸：GUI 未就绪")
            else:
                log(f"未知的全局热键动作: {action}")
        except Exception as exc:
            log_error(f"全局热键动作执行失败({action})", exc)
    threading.Thread(target=_runner, daemon=True, name=f"ShangBackgroundHotkeyAction-{action}").start()


def refresh_global_hotkeys():
    """Register global hotkeys only after the user explicitly opts in.

    Linux/macOS use pynput instead of Win32 RegisterHotKey.  Keeping the same
    default-off switch as Windows avoids silently installing keyboard hooks on
    first launch and makes settings behavior consistent across all platforms.
    """
    global _pynput_hotkey_listener
    if IS_WINDOWS:
        return False

    if _pynput_hotkey_listener is not None:
        try:
            _pynput_hotkey_listener.stop()
        except Exception:
            pass
        _pynput_hotkey_listener = None

    if not bool(config.get("global_hotkeys_enabled", False)):
        log("全局热键未启用，已跳过系统级注册")
        return False
    if _pynput_keyboard is None:
        log("pynput 未安装，无法注册全局热键")
        return False
    if IS_MACOS:
        try:
            import Quartz
            preflight = getattr(Quartz, "CGPreflightListenEventAccess", None)
            request = getattr(Quartz, "CGRequestListenEventAccess", None)
            trusted = bool(preflight()) if callable(preflight) else True
            if not trusted and callable(request):
                trusted = bool(request())
            if not trusted:
                log("macOS 未授予输入监控权限，无法注册全局热键。请在系统设置 > 隐私与安全性 > 输入监控中允许本应用。")
                return False
        except Exception as exc:
            log(f"macOS 输入监控权限预检失败，将尝试由 pynput 注册: {exc}")

    combos: dict[str, callable] = {}
    for action in ("previous", "next", "random", "jump", "mode"):
        hotkey_str = str(config.get(f"hotkey_{action}", "") or "")
        parsed = _pynput_hotkey_string(hotkey_str)
        if parsed:
            if parsed in combos:
                log(f"全局热键重复，已忽略后续动作: {parsed} -> {action}")
                continue
            combos[parsed] = lambda act=action: _on_pynput_global_hotkey(act)
    if not combos:
        log("无有效的全局热键可注册（需要包含修饰键和非修饰键）")
        return False
    try:
        listener = _pynput_keyboard.GlobalHotKeys(combos)
        listener.daemon = True
        listener.start()
        _pynput_hotkey_listener = listener
        log(f"已注册 {len(combos)} 个全局热键")
        return True
    except Exception as exc:
        log(f"注册全局热键失败: {exc}")
        return False



def stop_global_hotkeys():
    """Unregister global hotkeys and stop any listener."""
    global _pynput_hotkey_listener
    if not IS_WINDOWS:
        if _pynput_hotkey_listener is not None:
            try:
                _pynput_hotkey_listener.stop()
            except Exception:
                pass
            _pynput_hotkey_listener = None
        return
    pass

def _gui_open_wallpaper_sidebar():
    """在 GUI 线程打开壁纸跳转侧栏（由全局热键线程通过 root.after 调用）。"""
    try:
        if root is not None and hasattr(root, "window") and hasattr(root.window, "open_wallpaper_sidebar"):
            root.window.open_wallpaper_sidebar()
    except Exception as exc:
        log(f"打开壁纸侧栏失败: {exc}")

def _gui_switch_wallpaper_mode(target: str | None = "next") -> bool:
    """在 GUI 线程切换壁纸模式；用于托盘、IPC 和全局热键。"""
    try:
        if root is not None and hasattr(root, "window"):
            window = root.window
            if target and str(target).lower() not in {"next", "cycle"} and hasattr(window, "switch_to_mode"):
                window.switch_to_mode(str(target))
                return True
            if hasattr(window, "switch_to_next_mode"):
                window.switch_to_next_mode()
                return True
    except Exception as exc:
        log_error("gui switch wallpaper mode failed", exc)
    return switch_wallpaper_mode(target or "next")


def _platform_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform


def _session_wallpaper_files() -> list[str]:
    files = [SESSION_WALLPAPER_FILE]
    if LEGACY_SESSION_WALLPAPER_FILE not in files:
        files.append(LEGACY_SESSION_WALLPAPER_FILE)
    return files


def _is_same_wallpaper_path(left: str | None, right: str | None) -> bool:
    try:
        if not left or not right:
            return False
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except Exception:
        return False


def _is_restorable_wallpaper_path(path: str | None) -> bool:
    """Only image files can be restored through the static wallpaper API.

    Dynamic providers such as Windows Spotlight, GNOME XML slideshows or missing files are
    intentionally rejected instead of falling back to app config and restoring the wrong image.
    """
    if not path:
        return False
    try:
        normalized = _normalize_wallpaper_path(path)
    except Exception:
        normalized = str(path or "")
    try:
        return bool(normalized and os.path.isfile(normalized) and normalized.lower().endswith(_IMAGE_EXTENSIONS))
    except Exception:
        return bool(normalized and os.path.isfile(normalized))


def _read_session_payload(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("session wallpaper payload must be a JSON object")
    return data


def _persist_session_original_wallpaper():
    with _session_wallpaper_lock:
        try:
            if not session_original_wallpaper:
                return False
            os.makedirs(os.path.dirname(SESSION_WALLPAPER_FILE), exist_ok=True)
            data = {
                "schema": 2,
                "platform": _platform_name(),
                "wallpaper": _normalize_wallpaper_path(session_original_wallpaper),
                "style": session_original_wallpaper_style or {},
                "captured_at": time.time(),
                "pid": os.getpid(),
                "app_base_dir": BASE_DIR,
            }
            tmp_file = f"{SESSION_WALLPAPER_FILE}.{os.getpid()}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp_file, 0o600)
            except OSError:
                pass
            os.replace(tmp_file, SESSION_WALLPAPER_FILE)
            return True
        except Exception as e:
            log(f"保存启动前壁纸会话失败: {e}")
            return False


def _clear_session_wallpaper_file():
    """恢复、失效或退出后清除会话锚点文件，防止下次启动误读旧壁纸。"""
    with _session_wallpaper_lock:
        for path in _session_wallpaper_files():
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                log(f"清除会话壁纸文件失败({path}): {e}")


def _load_session_original_wallpaper(max_age_seconds=24 * 3600):
    """Load a persisted startup-wallpaper record for same-session handoff.

    Used mainly for admin/elevated restarts. Ordinary launches clear the record first
    and capture the actual system wallpaper again.
    """
    global session_original_wallpaper, session_original_wallpaper_style, session_original_wallpaper_captured
    with _session_wallpaper_lock:
        for session_file in _session_wallpaper_files():
            try:
                if not session_file or not os.path.exists(session_file):
                    continue
                data = _read_session_payload(session_file)
                captured_at = float(data.get("captured_at", 0) or 0)
                if captured_at <= 0 or time.time() - captured_at > max_age_seconds:
                    try:
                        os.remove(session_file)
                    except OSError:
                        pass
                    continue
                platform = data.get("platform")
                if platform and platform != _platform_name():
                    log(f"启动前壁纸会话平台不匹配，已忽略: {platform}")
                    continue
                target = _normalize_wallpaper_path(data.get("wallpaper") or "")
                if not _is_restorable_wallpaper_path(target):
                    log(f"启动前壁纸会话无效，已忽略: {target or '<empty>'}")
                    try:
                        os.remove(session_file)
                    except OSError:
                        pass
                    continue
                session_original_wallpaper = target
                session_original_wallpaper_style = data.get("style") or {}
                session_original_wallpaper_captured = True
                log(f"已从会话文件恢复启动前壁纸记录: {session_original_wallpaper}")
                return True
            except Exception as e:
                log(f"读取启动前壁纸会话失败({session_file}): {e}")
        return False
pending_show_request = False

APP_MUTEX_NAME = single_instance.APP_MUTEX_NAME
STARTUP_ITEM_NAME = "ShangBackground"
STARTUP_VBS_NAME = f"{STARTUP_ITEM_NAME}.vbs"
LEGACY_STARTUP_VALUE_NAMES = ["xxdz_WallpaperController"]
ALL_STARTUP_VALUE_NAMES = LEGACY_STARTUP_VALUE_NAMES + [STARTUP_ITEM_NAME]
LEGACY_STARTUP_VBS_NAMES = ["PowerOn.vbs"]
ALL_STARTUP_VBS_NAMES = LEGACY_STARTUP_VBS_NAMES + [STARTUP_VBS_NAME]
_instance_mutex_handle = None


def _win_type(name, fallback):
    """ctypes.wintypes 在不同 Python 版本里字段不完全一致；这里集中做兜底。"""
    try:
        return getattr(ctypes.wintypes, name)
    except Exception:
        return fallback


def _win_int(value):
    """把 WNDPROC 回调里可能出现的 None / c_void_p 安全转换成 Win32 整数值。"""
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(value.value or 0)
        except Exception:
            return 0


def _configure_win32_ctypes():
    """声明常用 Win32 API 的参数/返回类型，兼容 Python 3.14 的严格 ctypes 转换。"""
    global HWND, HMENU, HINSTANCE, HMODULE, HANDLE, BOOL, DWORD, UINT, LPCWSTR, ATOM, WPARAM, LPARAM, LRESULT
    if not IS_WINDOWS or ctypes.wintypes is None:
        return
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32
        HWND = _win_type("HWND", ctypes.c_void_p)
        HMENU = _win_type("HMENU", ctypes.c_void_p)
        HINSTANCE = _win_type("HINSTANCE", ctypes.c_void_p)
        HMODULE = _win_type("HMODULE", ctypes.c_void_p)
        HANDLE = _win_type("HANDLE", ctypes.c_void_p)
        BOOL = _win_type("BOOL", ctypes.c_int)
        DWORD = _win_type("DWORD", ctypes.c_ulong)
        UINT = _win_type("UINT", ctypes.c_uint)
        LPCWSTR = _win_type("LPCWSTR", ctypes.c_wchar_p)
        ATOM = _win_type("ATOM", ctypes.c_ushort)
        WPARAM = _win_type("WPARAM", ctypes.c_size_t)
        LPARAM = _win_type("LPARAM", ctypes.c_ssize_t)
        LRESULT = _win_type("LRESULT", ctypes.c_ssize_t)

        kernel32.GetModuleHandleW.argtypes = [LPCWSTR]
        kernel32.GetModuleHandleW.restype = HMODULE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, LPCWSTR]
        kernel32.CreateMutexW.restype = HANDLE
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.CloseHandle.restype = BOOL
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = DWORD
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        user32.RegisterClassW.restype = ATOM
        user32.CreateWindowExW.argtypes = [
            DWORD, LPCWSTR, LPCWSTR, DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            HWND, HMENU, HINSTANCE, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = HWND
        user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        user32.SendMessageW.restype = LRESULT
        user32.FindWindowW.argtypes = [LPCWSTR, LPCWSTR]
        user32.FindWindowW.restype = HWND
        user32.FindWindowExW.argtypes = [HWND, HWND, LPCWSTR, LPCWSTR]
        user32.FindWindowExW.restype = HWND
        user32.DestroyWindow.argtypes = [HWND]
        user32.DestroyWindow.restype = BOOL
        shell32.ShellExecuteW.argtypes = [HWND, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, ctypes.c_int]
        shell32.ShellExecuteW.restype = HINSTANCE
    except Exception as e:
        log(f"Win32 API 类型声明失败: {e}")


_configure_win32_ctypes()


def is_windows_admin():
    """检测当前进程是否以管理员权限运行（仅 Windows 有效）。"""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as e:
        log(f"管理员权限检测失败: {e}")
        return False


def release_single_instance_mutex():
    """释放单实例守卫，用于提权重启或退出前清理。"""
    global _instance_mutex_handle, hwnd, use_message
    try:
        if hwnd and IS_WINDOWS:
            try:
                ctypes.windll.user32.DestroyWindow(HWND(int(hwnd)))
            except Exception:
                pass
            hwnd = None
            use_message = False
        single_instance.release()
        _instance_mutex_handle = None
        log("已释放单实例守卫")
    except Exception as e:
        log(f"释放单实例守卫失败: {e}")


def _relaunch_command_parts(extra_args=None):
    """Return (program, base_args) for relaunching this app.

    v1.4.8: Nuitka 打包后 sys.executable 是编译后的二进制，sys.argv[0] 也是
    同一个路径。直接用 [sys.executable, sys.argv[0]] 重启会把二进制自身路径
    作为参数传给 argparse，导致重启失败。打包模式下应返回 (exe, [])。
    """
    if is_frozen():
        candidates = [app_executable_path(), sys.executable, sys.argv[0] if sys.argv else ""]
        for c in candidates:
            c = os.path.abspath(os.path.expanduser(str(c or "")))
            if c and os.path.isfile(c):
                return c, []
        return os.path.abspath(app_executable_path()), []
    # source mode
    return sys.executable, [os.path.abspath(sys.argv[0]) if sys.argv else os.path.join(BASE_DIR, "main.py")]


def restart_application(extra_args=None):
    """Restart the current app without requesting elevation."""
    try:
        executable, base_args = _relaunch_command_parts()
        relaunch_args = [*base_args, *(str(arg) for arg in (extra_args or []))]
        release_single_instance_mutex()
        _cleanup_tray_icon_on_exit()
        subprocess.Popen([executable, *relaunch_args], cwd=os.getcwd(), close_fds=True)
        log(f"已请求普通重启: exe={executable}; args={relaunch_args}")
        return True
    except Exception as exc:
        log(f"普通重启失败: {exc}", level="ERROR", exc_info=exc)
        return False


def restart_as_admin(extra_args=None):
    """以管理员身份重启当前应用。

    使用 ShellExecuteW 的 "runas" 动词触发 UAC 提权，
    然后退出当前非管理员进程。
    注意：提权前必须先释放互斥体和销毁托盘图标，
    否则新实例无法获取互斥体，且旧托盘图标会残留。
    """
    if not IS_WINDOWS:
        log(t("非 Windows 平台，改为普通重启"))
        return restart_application(extra_args=extra_args)
    guard_released = False
    try:
        # 获取当前解释器路径和脚本路径。脚本运行时优先用 pythonw.exe，避免提权后出现控制台日志窗口。
        if is_frozen():
            executable = sys.executable
            base_args = []
        else:
            pythonw_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            executable = pythonw_exe if os.path.exists(pythonw_exe) else sys.executable
            base_args = [os.path.abspath(sys.argv[0])]

        requested_extra_args = list(extra_args or [])
        restart_skip_flags = {
            "--previous",
            "--next",
            "--random",
            "--show",
            "--hide",
            "--jump-to-wallpaper",
            "--sync-context-on-start",
            "--inherit-session-wallpaper",
        }
        restart_skip_value_flags = {"--set-wallpaper"}
        current_args = []
        skip_next_arg = False
        for arg in sys.argv[1:]:
            if skip_next_arg:
                skip_next_arg = False
                continue
            if arg in restart_skip_flags:
                continue
            if arg in restart_skip_value_flags:
                skip_next_arg = True
                continue
            if any(arg.startswith(flag + "=") for flag in restart_skip_value_flags):
                continue
            current_args.append(arg)
        for arg in requested_extra_args:
            if arg not in current_args:
                current_args.append(arg)
        if "--inherit-session-wallpaper" not in current_args:
            current_args.append("--inherit-session-wallpaper")
        params = subprocess.list2cmdline([str(a) for a in [*base_args, *current_args]])

        # 先落盘启动前壁纸，再释放互斥体和托盘图标。管理员提权会产生新进程，内存状态不可依赖。
        # 这里必须优先继承本次会话最早记录的“启动前壁纸”，不能用当前桌面强制刷新；
        # 否则用户已经播放/切换到幻灯片后再提权，会把恢复目标覆盖成切换后的壁纸。
        capture_session_original_wallpaper(inherit_existing=True, force_refresh=False)
        _persist_session_original_wallpaper()
        # 先释放互斥体和托盘图标，再触发 UAC。否则被提权的新实例可能先启动、发现旧互斥体后退出，
        # 造成“旧实例退出 + 新实例也退出”的双杀问题。
        release_single_instance_mutex()
        guard_released = True
        _cleanup_tray_icon_on_exit()

        # 使用 ShellExecuteW 以管理员身份运行
        ret = ctypes.windll.shell32.ShellExecuteW(
            0, "runas", executable, params, None, 1  # SW_SHOWNORMAL=1
        )
        if ret <= 32:
            log(f"提权重启失败，ShellExecuteW 返回值: {ret}")
            acquire_single_instance_mutex()
            try:
                start_message_window()
            except Exception as ipc_error:
                log(f"恢复消息窗口失败: {ipc_error}")
            return False
        log("已请求管理员权限重启")

        # 销毁消息窗口
        global hwnd
        if hwnd:
            try:
                ctypes.windll.user32.DestroyWindow(hwnd)
            except Exception:
                pass
            hwnd = None

        return True
    except Exception as e:
        if guard_released:
            try:
                acquire_single_instance_mutex()
                start_message_window()
            except Exception as recover_error:
                log(f"恢复单实例守卫失败: {recover_error}")
        log(f"提权重启异常: {e}")
        return False


def _cleanup_tray_icon_on_exit():
    """清理托盘相关对象并刷新通知区域。"""
    global tray_icon_obj
    tray_icon_obj = globals().get("tray_icon_obj", None)
    if tray_icon_obj is not None:
        icon = tray_icon_obj
        tray_icon_obj = None
        try:
            icon.visible = False
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
    # 使用 Win32 API 刷新通知区域，强制系统回收残留的幽灵图标
    if IS_WINDOWS:
        try:
            # 方法：通过向通知区域发送鼠标移动消息来触发图标刷新
            # 找到系统托盘通知区域窗口
            tray_hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            if tray_hwnd:
                # 查找通知区域子窗口
                tray_notify = ctypes.windll.user32.FindWindowExW(tray_hwnd, None, "TrayNotifyWnd", None)
                if tray_notify:
                    # 查找工具提示子窗口
                    toolbar_hwnd = ctypes.windll.user32.FindWindowExW(tray_notify, None, "ToolbarWindow32", None)
                    if toolbar_hwnd:
                        # 发送 WM_MOUSEMOVE 消息触发图标刷新
                        ctypes.windll.user32.SendMessageW(toolbar_hwnd, 0x0200, 0, 0)
        except Exception:
            pass
    try:
        time.sleep(0.08)
    except Exception:
        pass


def acquire_single_instance_mutex():
    """普通权限单实例检测：系统文件锁 + 本机回环端口辅助。"""
    try:
        return single_instance.acquire()
    except Exception as e:
        log(f"单实例守卫检测失败: {e}")
        return True


def _hwnd_message_parent():
    """返回 HWND_MESSAGE 的 ctypes 表示，用于创建/查找不可见 message-only IPC 窗口。"""
    try:
        return HWND(HWND_MESSAGE)
    except Exception:
        try:
            return ctypes.c_void_p(HWND_MESSAGE)
        except Exception:
            pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
            return ctypes.c_void_p((1 << pointer_bits) + HWND_MESSAGE)


def find_existing_main_window(timeout=2.0):
    if not IS_WINDOWS:
        return None
    deadline = time.time() + max(0, timeout)
    while True:
        try:
            user32 = ctypes.windll.user32
            # 新版本使用 message-only 窗口承载 WM_COPYDATA，避免出现空白 WallpaperController 顶层窗口。
            existing = user32.FindWindowExW(_hwnd_message_parent(), HWND(0), WND_CLASS_NAME, None)
            if not existing:
                # 兼容旧版本曾创建的 0x0 顶层控制窗口。
                existing = user32.FindWindowW(WND_CLASS_NAME, None)
            if existing:
                return existing
            raise Exception("no window yet")
        except Exception as e:
            if "no window yet" not in str(e):
                log(f"查找已有实例 IPC 窗口失败: {e}")
        if time.time() >= deadline:
            return None
        time.sleep(0.1)


def send_command_to_hwnd(target_hwnd, command):
    if not IS_WINDOWS or not target_hwnd:
        return False
    try:
        payload = command.encode("utf-8") + b"\x00"
        buffer = ctypes.create_string_buffer(payload)
        cds = COPYDATASTRUCT()
        cds.dwData = 1
        cds.cbData = len(payload)
        cds.lpData = ctypes.cast(buffer, ctypes.c_void_p)
        # SendMessageW 的 lParam 是整数大小的 LPARAM。Python 3.14/ctypes 对类型更严格，
        # 直接传 byref(cds) 会报 “_ctypes.CArgObject cannot be interpreted as an integer”。
        lparam = LPARAM(ctypes.addressof(cds)) if IS_WINDOWS else ctypes.addressof(cds)
        result = ctypes.windll.user32.SendMessageW(HWND(_win_int(target_hwnd)), UINT(WM_COPYDATA), WPARAM(0), lparam)
        return int(result or 0) == 1
    except Exception as e:
        log(f"发送命令到已有实例失败: {e}")
        return False


def activate_existing_instance(show_notice=True):
    """激活已有实例的主窗口。

    使用多种方式确保窗口能被正确激活：
    1. 通过 WM_COPYDATA 发送 "show" 命令
    2. 使用 ShowWindow + SetForegroundWindow 强制前台显示
    3. 使用 AttachThreadInput 解决前台窗口锁定问题

    Note: 该函数仅 Windows 可用（依赖 ``ctypes.windll``）。MacOS 上
    单实例检测由 ``core.single_instance`` 模块负责，不会走到这里；此处
    显式 return False 防止任何意外调用路径触发 ``AttributeError``。
    """
    if not IS_WINDOWS:
        return False
    existing = find_existing_main_window(timeout=5.0)
    activated = False
    if existing:
        activated = send_command_to_hwnd(existing, "show")
        try:
            # 获取当前线程和目标窗口的线程
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            target_thread = ctypes.windll.user32.GetWindowThreadProcessId(existing, None)
            # 附加线程输入，解决 SetForegroundWindow 在某些情况下不生效的问题
            if current_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)
            ctypes.windll.user32.ShowWindow(existing, 9)  # SW_RESTORE
            ctypes.windll.user32.ShowWindow(existing, 1)  # SW_SHOWNORMAL
            ctypes.windll.user32.SetForegroundWindow(existing)
            ctypes.windll.user32.BringWindowToTop(existing)
            # 取消线程附加
            if current_thread != target_thread:
                try:
                    ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)
                except Exception:
                    pass
        except Exception as e:
            log(f"激活已有实例失败: {e}")
    if show_notice:
        if existing:
            show_message(t("不要重复运行"), t("不要重复运行，已为您打开现有主界面。"))
        else:
            show_message(t("不要重复运行"), t("不要重复运行。检测到 ShangBackground 已经在启动或运行，本次启动已取消。"))
    return activated or existing is not None


def get_startup_folder_path_windows():
    if not IS_WINDOWS:
        return ""
    try:
        CSIDL_STARTUP = 7
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_STARTUP, None, 0, buf)
        return buf.value
    except Exception as e:
        log(f"获取 Windows 启动文件夹失败: {e}")
        return os.path.join(os.path.expanduser('~'), r'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup')


def get_startup_vbs_path(name=STARTUP_VBS_NAME):
    folder = get_startup_folder_path_windows()
    return os.path.join(folder, name) if folder else name


# 版本检查全局变量
remote_version = "1"
remote_release_notes = ""
remote_download_urls = {"GitHub Release": "", t("发布页"): "https://github.com/purrfecto114-lgtm/ShangBackground/releases/latest"}
show_update_flag = False
check_failed = False


def _history_key(path: str) -> str:
    """Return a stable key so the same image path only appears once in history."""
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(path or ""))))
    except Exception:
        return str(path or "").strip().lower()


def _normalize_wallpaper_path(path: str) -> str:
    if not path:
        return ""
    # 如果是 HTTP/HTTPS URL，直接返回原始字符串
    try:
        s = str(path)
    except Exception:
        s = path
    if isinstance(s, str) and s.lower().startswith(("http://", "https://")):
        return s
    try:
        return os.path.abspath(os.path.expanduser(s))
    except Exception:
        return s


def dedupe_wallpaper_history(history, *, keep_missing: bool = True, limit: int = 50):
    """Remove duplicate wallpaper entries while preserving order.

    This prevents language switching / elevated restart / path case differences from creating
    repeated history thumbnails for the same image.
    """
    result = []
    seen = set()
    for item in history or []:
        if not item:
            continue
        normalized = _normalize_wallpaper_path(item)
        if not keep_missing and not os.path.isfile(normalized):
            continue
        key = _history_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


CONFIG_MIGRATION_PENDING = False


def get_default_config() -> dict:
    """Return a fresh factory-default configuration dictionary."""
    return {
        "mode": "幻灯片放映",
        "slide_folder": "",
        "slide_seconds": 300,
        "transition_effect": "none",
        "transition_direction": "right",
        "transition_duration_ms": 0,
        "wallpaper_transition_enabled": False,
        "video_file": "",
        "video_muted": True,
        "video_volume": 100,  # 0-100, only effective when video_muted is False
        "video_focus_behavior": "none",  # none / pause / duck when foreground is not desktop
        "video_focus_duck_volume": 20,
        "html_file": "",
        "html_auto_pause": True,
        "html_gpu_enabled": True,
        "html_mouse_through": True,
        "shuffle": False,
        "fit_mode": "填充",
        "single_image": "",
        "solid_color": DEFAULT_SOLID_COLOR,
        "gradient_color2": DEFAULT_GRADIENT_COLOR2,
        "theme_color": DEFAULT_THEME_COLOR,
        "gradient_angle": 60,
        "current_wallpaper": "",
        "slideshow_last_wallpaper": "",
        "history": [],
        "favorites": [],  # v1.4.7: 收藏的壁纸路径列表 (用户主动收藏, 不随历史滚动消失)
        "auto_start": False,
        "ctx_last_wallpaper": False,
        "ctx_next_wallpaper": False,
        "ctx_random_wallpaper": False,
        "ctx_jump_to_wallpaper": False,
        "hotkey_previous": "Ctrl+Alt+U",
        "hotkey_next": "Ctrl+Alt+N",
        "hotkey_random": "Ctrl+Alt+R",
        "hotkey_jump": "Ctrl+Alt+J",
        # Keep the same opt-in contract as Windows.  Focus guarding is a
        # Windows-only implementation detail, but the key is retained so a
        # shared settings file remains forward/backward compatible.
        "hotkey_focus_guard": False,
        "global_hotkeys_enabled": False,
        "app_shortcuts_enabled": True,
        "app_shortcuts": {
            # 应用内热键（QShortcut，仅在主窗口获得焦点时生效）。
            # 与 hotkey_* 全局热键（Ctrl+Alt+...）互补：前者用于"主窗口
            # 激活时"的快速操作，后者用于"任何应用前台时"的全局触发。
            # 留空字符串禁用对应快捷键。
            "previous":     "PgUp",
            "next":         "PgDown",
            "random":       "R",
            "bing":         "F5",
            "settings":     "Ctrl+,",
            "exit":         "Ctrl+Q",
            "hide_to_tray": "Esc",
        },
        "recent_folders": [],
        "run_in_background": True,  # 默认后台运行
        "tray_icon": True,  # 默认托盘图标
        "tray_click_action": "next",
        "tray_menu_items": ["show", "previous", "next", "random", "bing", "jump", "about", "exit"],
        "dark_mode": False,
        "performance_mode": False,  # 向后兼容: 旧版布尔开关. v1.4.6 起用 performance_level 三档.
        "performance_level": "balanced",  # v1.4.6: 三档性能模式 "power_saver" / "balanced" / "performance"
        "silent_update_check_on_startup": True,
        "bing_cache_dir": "",
        "bing_sync_count": 1,
        "bing_next_index": 0,
        "bing_auto_cleanup": False,
        "bing_auto_update_on_start": False,
        "bing_auto_update_count": 1,
        "bing_auto_delete_on_start": False,
        "bing_auto_delete_count": 1,
        "log_enabled": False,  # 默认关闭日志文件记录；在新版日志页开启时需要先选择路径
        "log_file_path": "",  # 日志文件保存路径，首次开启日志时填写
        "ignored_version": "",  # 用户选择忽略的版本号
        "app_theme": "default",  # 默认使用 Qt/系统原生样式
        "font_path": "",
        "font_weight": "normal",  # v1.4.7: 字体粗细 "normal" / "medium" / "bold"
        "font_size": 0,  # v1.4.7: 字体大小 (0 = 跟随系统默认, 否则 px)
        "dpi_scale": 1.0,
        "language": "zh",
    }


def load_config():
    """加载配置文件，如果不存在则返回默认配置。

    迁移旧配置时只更新内存，不在导入阶段同步写 settings.json，
    避免 GUI 还没显示就被磁盘/杀软/网络盘卡住。
    """
    global CONFIG_MIGRATION_PENDING
    default = get_default_config()
    source_path = ""
    for candidate in (CONFIG_PATH, LEGACY_CONFIG_PATH, BUNDLED_CONFIG_PATH, LEGACY_BUNDLED_CONFIG_PATH):
        if candidate and os.path.exists(candidate):
            source_path = candidate
            break
    if source_path:
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("settings.json 根节点必须是对象")
            data = default.copy()
            data.update(loaded)
            log(f"配置加载成功: {os.path.basename(source_path)}")
            # 自动转换旧配置。
            converted = False
            if "user_id" in data:
                data.pop("user_id", None)
                converted = True
            if "tray_menu_items" in data and data["tray_menu_items"]:
                first_item = data["tray_menu_items"][0]
                if isinstance(first_item, dict) and "action" in first_item:
                    # 旧格式：转换为只存储 action 字符串的新格式
                    new_items = [item["action"] for item in data["tray_menu_items"]]
                    data["tray_menu_items"] = new_items
                    converted = True
            if isinstance(data.get("tray_menu_items"), list) and "mode" not in data["tray_menu_items"]:
                try:
                    _insert_at = data["tray_menu_items"].index("random") + 1
                except ValueError:
                    _insert_at = len(data["tray_menu_items"])
                data["tray_menu_items"].insert(_insert_at, "mode")
                converted = True
            # 迁移右键菜单配置：旧版“全局设置/个性化/设置为壁纸”入口已移除。
            if "ctx_jump_to_wallpaper" not in data:
                data["ctx_jump_to_wallpaper"] = bool(data.get("ctx_global_settings", False))
                converted = True
            for _stale_ctx_key in ("ctx_personalize", "ctx_global_settings", "ctx_set_wallpaper"):
                if _stale_ctx_key in data:
                    data.pop(_stale_ctx_key, None)
                    converted = True
            for _key, _default in {"hotkey_previous": "Ctrl+Alt+U", "hotkey_next": "Ctrl+Alt+N", "hotkey_random": "Ctrl+Alt+R", "hotkey_jump": "Ctrl+Alt+J"}.items():
                if _key not in data:
                    data[_key] = _default
                    converted = True

            # 应用内热键（QShortcut）—— 三端同步
            _app_sc_default = {
                "previous":     "PgUp",
                "next":         "PgDown",
                "random":       "R",
                "bing":         "F5",
                "settings":     "Ctrl+,",
                "exit":         "Ctrl+Q",
                "hide_to_tray": "Esc",
            }
            if not isinstance(data.get("app_shortcuts"), dict):
                data["app_shortcuts"] = dict(_app_sc_default)
                converted = True
            else:
                for _sc_key, _sc_default in _app_sc_default.items():
                    if _sc_key not in data["app_shortcuts"]:
                        data["app_shortcuts"][_sc_key] = _sc_default
                        converted = True
            for _key, _default in {
                "video_focus_behavior": "none",
                "video_focus_duck_volume": 20,
                "html_file": "",
                "html_auto_pause": True,
                "html_gpu_enabled": True,
                "html_mouse_through": True,
            }.items():
                if _key not in data:
                    data[_key] = _default
                    converted = True
            if "hotkey_focus_guard" not in data:
                data["hotkey_focus_guard"] = False
                converted = True
            if "global_hotkeys_enabled" not in data:
                data["global_hotkeys_enabled"] = False
                converted = True
            # v1.4.7: 应用内热键功能已彻底移除. app_shortcuts_enabled 配置键
            # 保留向后兼容 (旧 settings.json 不报错), 但不再有任何功能.
            # 清理 v1.4.6 遗留的 _app_sc_user_disabled_v146 标记键 (writer 已删).
            if "app_shortcuts_enabled" not in data:
                data["app_shortcuts_enabled"] = True
                converted = True
            if "_app_sc_user_disabled_v146" in data:
                data.pop("_app_sc_user_disabled_v146", None)
                converted = True
            # v1.4.6: 迁移 performance_mode 布尔到 performance_level 三档.
            if "performance_level" not in data:
                if data.get("performance_mode"):
                    data["performance_level"] = "performance"
                else:
                    data["performance_level"] = "balanced"
                converted = True
            else:
                _pl = str(data.get("performance_level", "balanced")).lower()
                if _pl not in ("power_saver", "balanced", "performance"):
                    data["performance_level"] = "balanced"
                    converted = True
            if "log_enabled" not in data:
                data["log_enabled"] = False
                converted = True
            if "log_file_path" not in data:
                data["log_file_path"] = ""
                converted = True
            if "app_theme" not in data:
                data["app_theme"] = "default"
                converted = True
            if "theme_color" not in data:
                data["theme_color"] = default.get("theme_color", DEFAULT_THEME_COLOR)
                converted = True
            if "font_path" not in data:
                data["font_path"] = ""
                converted = True
            # v1.4.7: 字体粗细和大小
            if "font_weight" not in data:
                data["font_weight"] = "normal"
                converted = True
            else:
                _fw = str(data.get("font_weight", "normal")).lower()
                if _fw not in ("normal", "medium", "bold"):
                    data["font_weight"] = "normal"
                    converted = True
            if "font_size" not in data or not isinstance(data.get("font_size"), (int, float)):
                data["font_size"] = 0
                converted = True
            else:
                try:
                    _fs = int(data.get("font_size", 0))
                    if _fs < 0 or _fs > 48:
                        data["font_size"] = 0
                        converted = True
                    else:
                        data["font_size"] = _fs
                except Exception:
                    data["font_size"] = 0
                    converted = True
            if "dpi_scale" not in data:
                data["dpi_scale"] = 1.0
                converted = True
            if "language" not in data:
                data["language"] = "zh"
                converted = True
            # v1.4.7: font_size 现在是新功能的合法 key (0=系统默认, 否则 px). 不再 pop.
            if str(data.get("solid_color", "")).lower() in {"#4facfe", "#2d2d2d"}:
                data["solid_color"] = "#ffffff"
                converted = True
            if str(data.get("gradient_color2", "")).lower() in {"#00f2fe", "#4a4a4a"}:
                data["gradient_color2"] = "#ffffff"
                converted = True
            if data.get("bing_cache_dir") == os.path.join(BASE_DIR, "bing_wallpapers"):
                data["bing_cache_dir"] = ""
                converted = True
            if "bing_next_index" not in data:
                data["bing_next_index"] = 0
                converted = True
            if "bing_auto_cleanup" not in data:
                data["bing_auto_cleanup"] = False
                converted = True
            for _key, _default in {
                "silent_update_check_on_startup": True,
                "bing_auto_update_on_start": False,
                "bing_auto_update_count": 1,
                "bing_auto_delete_on_start": False,
                "bing_auto_delete_count": 1,
            }.items():
                if _key not in data:
                    data[_key] = _default
                    converted = True
            cleaned_history = dedupe_wallpaper_history(data.get("history", []), keep_missing=True)
            if cleaned_history != data.get("history", []):
                data["history"] = cleaned_history
                converted = True
            # v1.4.7: 收藏夹初始化 + 去重
            if "favorites" not in data or not isinstance(data.get("favorites"), list):
                data["favorites"] = []
                converted = True
            else:
                _fav = data.get("favorites", [])
                _fav_clean = []
                _seen_fav = set()
                for _f in _fav:
                    _fpath = str(_f or "").strip()
                    if _fpath and _fpath not in _seen_fav:
                        _seen_fav.add(_fpath)
                        _fav_clean.append(_fpath)
                if len(_fav_clean) != len(_fav):
                    data["favorites"] = _fav_clean
                    converted = True
            if data.get("current_wallpaper"):
                normalized_current = _normalize_wallpaper_path(data.get("current_wallpaper", ""))
                if normalized_current != data.get("current_wallpaper"):
                    data["current_wallpaper"] = normalized_current
                    converted = True
            if data.get("slideshow_last_wallpaper"):
                normalized_slide_current = _normalize_wallpaper_path(data.get("slideshow_last_wallpaper", ""))
                if normalized_slide_current != data.get("slideshow_last_wallpaper"):
                    data["slideshow_last_wallpaper"] = normalized_slide_current
                    converted = True
            for _legacy_transition_key in ("transition_frames", "transition_preview", "transition_animation"):
                if _legacy_transition_key in data:
                    data.pop(_legacy_transition_key, None)
                    converted = True
            if data.get("wallpaper_transition_enabled") or data.get("transition_effect") not in (None, "none") or data.get("transition_duration_ms") not in (None, 0):
                converted = True
            data["wallpaper_transition_enabled"] = False
            data["transition_effect"] = "none"
            data["transition_direction"] = "right"
            data["transition_duration_ms"] = 0

            old_mode = data.get("mode")
            new_mode = normalize_mode_key(old_mode, default.get("mode", "幻灯片放映"))
            if old_mode != new_mode:
                data["mode"] = new_mode
                converted = True
            old_fit = data.get("fit_mode")
            new_fit = normalize_style_key(old_fit, default.get("fit_mode", "填充"))
            if old_fit != new_fit:
                data["fit_mode"] = new_fit
                converted = True

            # 转换完或从旧 shezhi.json 读取时，标记为稍后保存到 settings.json。
            # 不能在模块导入/GUI 创建前同步写盘，否则某些机器上会造成启动阶段卡顿。
            if converted or source_path != CONFIG_PATH:
                CONFIG_MIGRATION_PENDING = True
                data["__config_migration_pending__"] = True
                log("配置已在内存中迁移；将于 GUI 显示后延迟保存 settings.json")
            return data
        except Exception as e:
            log("加载配置失败: " + str(e))
            return default
    return default


def flush_pending_config_migration() -> bool:
    """Persist a deferred load_config() migration after the GUI has shown."""
    global CONFIG_MIGRATION_PENDING
    if not (CONFIG_MIGRATION_PENDING or bool(config.get("__config_migration_pending__", False))):
        return False
    save_config()
    CONFIG_MIGRATION_PENDING = False
    return True


def save_config():
    """保存配置到文件，使用线程锁保护写入操作。"""
    with _config_lock:
        try:
            config.pop("user_id", None)
            config.pop("__config_migration_pending__", None)
            if "tray_click_action" not in config:
                config["tray_click_action"] = "next"
            if "tray_menu_items" not in config:
                config["tray_menu_items"] = ["show", "previous", "next", "random", "bing", "jump", "about", "exit"]
            if isinstance(config.get("tray_menu_items"), list) and "mode" not in config["tray_menu_items"]:
                try:
                    _insert_at = config["tray_menu_items"].index("random") + 1
                except ValueError:
                    _insert_at = len(config["tray_menu_items"])
                config["tray_menu_items"].insert(_insert_at, "mode")
            if "log_enabled" not in config:
                config["log_enabled"] = False
            if "log_file_path" not in config:
                config["log_file_path"] = ""
            if "app_theme" not in config:
                config["app_theme"] = "default"
            for _legacy_transition_key in ("transition_frames", "transition_preview", "transition_animation"):
                config.pop(_legacy_transition_key, None)
            config["wallpaper_transition_enabled"] = False
            config["transition_effect"] = "none"
            config["transition_direction"] = "right"
            config["transition_duration_ms"] = 0
            if "theme_color" not in config:
                config["theme_color"] = DEFAULT_THEME_COLOR
            if "font_path" not in config:
                config["font_path"] = ""
            # v1.4.7: 字体粗细和大小规范化
            _fw_save = str(config.get("font_weight", "normal")).lower()
            if _fw_save not in ("normal", "medium", "bold"):
                _fw_save = "normal"
            config["font_weight"] = _fw_save
            try:
                _fs_save = int(config.get("font_size", 0))
                config["font_size"] = max(0, min(48, _fs_save))
            except Exception:
                config["font_size"] = 0
            if "dpi_scale" not in config:
                config["dpi_scale"] = 1.0
            try:
                config["dpi_scale"] = max(0.75, min(2.0, float(config.get("dpi_scale", 1.0))))
            except Exception:
                config["dpi_scale"] = 1.0
            config["ctx_jump_to_wallpaper"] = bool(config.get("ctx_jump_to_wallpaper", config.get("ctx_global_settings", False)))
            for _stale_ctx_key in ("ctx_personalize", "ctx_global_settings", "ctx_set_wallpaper"):
                config.pop(_stale_ctx_key, None)
            for _key, _default in {"hotkey_previous": "Ctrl+Alt+U", "hotkey_next": "Ctrl+Alt+N", "hotkey_random": "Ctrl+Alt+R", "hotkey_jump": "Ctrl+Alt+J"}.items():
                config.setdefault(_key, _default)
            config["hotkey_focus_guard"] = bool(config.get("hotkey_focus_guard", False))
            config["global_hotkeys_enabled"] = bool(config.get("global_hotkeys_enabled", False))
            config["app_shortcuts_enabled"] = bool(config.get("app_shortcuts_enabled", True))
            # v1.4.6: 规范化 performance_level, 同步旧 performance_mode 布尔
            _pl = str(config.get("performance_level", "balanced")).lower()
            if _pl not in ("power_saver", "balanced", "performance"):
                _pl = "balanced"
            config["performance_level"] = _pl
            config["performance_mode"] = (_pl == "performance")

            # 应用内热键（QShortcut）—— 三端同步
            _app_sc_default = {
                "previous":     "PgUp",
                "next":         "PgDown",
                "random":       "R",
                "bing":         "F5",
                "settings":     "Ctrl+,",
                "exit":         "Ctrl+Q",
                "hide_to_tray": "Esc",
            }
            if not isinstance(config.get("app_shortcuts"), dict):
                config["app_shortcuts"] = dict(_app_sc_default)
            else:
                for _sc_key, _sc_default in _app_sc_default.items():
                    if _sc_key not in config["app_shortcuts"]:
                        config["app_shortcuts"][_sc_key] = _sc_default
            for _key, _default in {
                "video_focus_behavior": "none",
                "video_focus_duck_volume": 20,
                "html_file": "",
                "html_auto_pause": True,
                "html_gpu_enabled": True,
                "html_mouse_through": True,
            }.items():
                config.setdefault(_key, _default)
            if config.get("bing_cache_dir") is None:
                config["bing_cache_dir"] = ""
            config["bing_auto_cleanup"] = bool(config.get("bing_auto_cleanup", False))
            config["bing_auto_update_on_start"] = bool(config.get("bing_auto_update_on_start", False))
            config["bing_auto_delete_on_start"] = bool(config.get("bing_auto_delete_on_start", False))
            try:
                config["bing_auto_update_count"] = max(1, min(16, int(config.get("bing_auto_update_count", 1))))
            except Exception:
                config["bing_auto_update_count"] = 1
            try:
                config["bing_auto_delete_count"] = max(1, min(200, int(config.get("bing_auto_delete_count", 1))))
            except Exception:
                config["bing_auto_delete_count"] = 1
            try:
                config["bing_next_index"] = max(0, int(config.get("bing_next_index", 0)))
            except Exception:
                config["bing_next_index"] = 0
            config["history"] = dedupe_wallpaper_history(config.get("history", []), keep_missing=True)
            # v1.4.7: 收藏夹规范化 (去重, 保留顺序, 限 200 条)
            _fav_save = config.get("favorites", [])
            if not isinstance(_fav_save, list):
                _fav_save = []
            _fav_seen = set()
            _fav_out = []
            for _f in _fav_save:
                _fp = str(_f or "").strip()
                if _fp and _fp not in _fav_seen:
                    _fav_seen.add(_fp)
                    _fav_out.append(_fp)
            config["favorites"] = _fav_out[:200]
            if config.get("current_wallpaper"):
                config["current_wallpaper"] = _normalize_wallpaper_path(config.get("current_wallpaper", ""))
            if config.get("slideshow_last_wallpaper"):
                config["slideshow_last_wallpaper"] = _normalize_wallpaper_path(config.get("slideshow_last_wallpaper", ""))
            config["mode"] = normalize_mode_key(config.get("mode", "幻灯片放映"))
            config["fit_mode"] = normalize_style_key(config.get("fit_mode", "填充"))
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            tmp_path = CONFIG_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, CONFIG_PATH)
            log("配置已保存")
        except Exception as e:
            log("保存配置失败: " + str(e))


# 配置文件写入线程锁，避免多线程并发写入导致数据损坏
_config_lock = threading.RLock()


_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif")
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")
_TRANSITION_TEMP_DIR = os.path.join(DATA_DIR, "transition_frames")


# v1.4.6: 移除 _normalize_transition_effect/direction/_transition_offset —— 过渡动画功能被强制禁用后的残留死代码.


config = load_config()



# 上报用户使用情况（另开个线程，能不卡界面）
def report_usage():
    """隐私保护：默认不进行联网统计。"""
    return


slide_timer = None
slide_timer_lock = threading.Lock()
slide_enabled = False
slide_images = []
_slide_images_index_cache: dict[str, tuple[int, str]] = {}
_slide_images_index_signature: tuple[int, str, str] | None = None

last_wallpaper_change_time = None
operation_cancel_event = threading.Event()
_ipc_command_lock = threading.Lock()
_ipc_worker_active = False
_ipc_pending_command: str | None = None


def request_cancel_operations(reason: str = ""):
    """Request cancellation for queued/long wallpaper work.

    A system API call that has already entered the OS cannot always be interrupted,
    but this stops future slideshow ticks and skips pending wallpaper changes.
    """
    operation_cancel_event.set()
    if reason:
        log("请求终止操作: " + str(reason))
    else:
        log("请求终止操作")


def clear_cancel_operations():
    operation_cancel_event.clear()


def is_operation_cancelled() -> bool:
    return operation_cancel_event.is_set()


def _execute_ipc_wallpaper_command(command: str) -> bool:
    if command == "previous":
        previous_wallpaper()
        return True
    if command == "next":
        next_wallpaper()
        return True
    if command == "random":
        random_wallpaper()
        return True
    if command.startswith("set_wallpaper|"):
        target = command.split("|", 1)[1]
        if os.path.isfile(target):
            log(f"侧边栏请求切换壁纸: {target}")
            set_wallpaper(target, "侧边栏切换")
        return True
    if command == "jump":
        if root is not None and hasattr(root, "after"):
            try:
                root.after(0, _gui_open_wallpaper_sidebar)
                return True
            except Exception as exc:
                log_error("ipc: jump command schedule failed", exc)
        _gui_open_wallpaper_sidebar()
        return True
    return False


def queue_ipc_wallpaper_command(command: str) -> None:
    """Run wallpaper IPC commands on one coalescing worker instead of the Win32 message callback.

    Repeated Next/Previous/Random requests keep only the latest pending command while
    the current OS wallpaper call is running. This keeps Explorer/taskbar messages and
    the PySide UI responsive under rapid clicks.
    """
    global _ipc_worker_active, _ipc_pending_command
    with _ipc_command_lock:
        if _ipc_worker_active:
            _ipc_pending_command = command
            log("壁纸切换繁忙，已合并最新请求")
            return
        _ipc_worker_active = True

    def _worker(first_command: str):
        global _ipc_worker_active, _ipc_pending_command
        current = first_command
        while current:
            try:
                _execute_ipc_wallpaper_command(current)
            except Exception as exc:
                log(f"IPC 壁纸命令执行失败: {exc}")
            with _ipc_command_lock:
                current = _ipc_pending_command
                _ipc_pending_command = None
                if not current:
                    _ipc_worker_active = False
                    return

    threading.Thread(target=_worker, args=(command,), daemon=True).start()


def log_time_diff(operation_name, new_wallpaper):
    global last_wallpaper_change_time
    current_time = time.time() * 1000
    if last_wallpaper_change_time is not None:
        time_diff = current_time - last_wallpaper_change_time
        log(f"[时间差] {operation_name} 切换到 {os.path.basename(new_wallpaper)}，距离上次切换 {time_diff:.2f} ms")
    else:
        log(f"[时间差] {operation_name} 首次切换到 {os.path.basename(new_wallpaper)}")
    last_wallpaper_change_time = current_time


current_preview_image = None
overlay_image = None
_wallpaper_query_last_error = ""
_wallpaper_query_last_log_time = 0.0
_WALLPAPER_QUERY_ERROR_LOG_INTERVAL = 20.0
# Bug 7 fix: 30-second cache for get_current_wallpaper().  macOS osascript
# calls take ~500ms-2s; without this cache the GUI preview polling timer
# would spawn an osascript subprocess on every tick.
_CACHED_CURRENT_WALLPAPER: str = ""
_CACHED_CURRENT_WALLPAPER_AT: float = 0.0
_CURRENT_WALLPAPER_CACHE_TTL = 30.0  # seconds


def _invalidate_current_wallpaper_cache() -> None:
    """Clear the 30s get_current_wallpaper cache."""
    global _CACHED_CURRENT_WALLPAPER, _CACHED_CURRENT_WALLPAPER_AT
    _CACHED_CURRENT_WALLPAPER = ""
    _CACHED_CURRENT_WALLPAPER_AT = 0.0


def get_current_wallpaper(*, use_cache: bool = True):
    """获取当前系统壁纸路径。

    Bug 7 fix: 当 ``use_cache=True``（默认）时，如果距上次成功查询不到 30s，
    直接返回缓存值。
    """
    global _wallpaper_query_last_error, _wallpaper_query_last_log_time
    global _CACHED_CURRENT_WALLPAPER, _CACHED_CURRENT_WALLPAPER_AT
    if use_cache and _CACHED_CURRENT_WALLPAPER:
        now = time.monotonic()
        if now - _CACHED_CURRENT_WALLPAPER_AT < _CURRENT_WALLPAPER_CACHE_TTL:
            return _CACHED_CURRENT_WALLPAPER
    try:
        path = get_current_wallpaper_platform()
        _wallpaper_query_last_error = ""
        if path and use_cache:
            _CACHED_CURRENT_WALLPAPER = path
            _CACHED_CURRENT_WALLPAPER_AT = time.monotonic()
        return path
    except Exception as e:
        message = str(e)
        now = time.monotonic()
        should_log = (
            message != _wallpaper_query_last_error
            or now - _wallpaper_query_last_log_time >= _WALLPAPER_QUERY_ERROR_LOG_INTERVAL
        )
        if should_log:
            log("获取当前壁纸失败: " + message)
            _wallpaper_query_last_error = message
            _wallpaper_query_last_log_time = now
        return ""


def push_wallpaper(path, *, update_current: bool = True, refresh_preview: bool = True):
    """将壁纸路径推入历史记录；同一张图片在历史中只保留一次。"""
    path = _normalize_wallpaper_path(path)
    if not path or not os.path.isfile(path):
        return
    with _config_lock:
        hist = dedupe_wallpaper_history(config.get("history", []), keep_missing=True)
        path_key = _history_key(path)
        hist = [p for p in hist if _history_key(p) != path_key]
        hist.insert(0, path)
        config["history"] = hist[:50]
        if update_current:
            config["current_wallpaper"] = path
        history_len = len(config.get("history", []))
        save_config()
    log("已记录壁纸: " + os.path.basename(path) + " | 历史总数: " + str(history_len))
    if refresh_preview:
        _queue_ui_preview_update(path)



def _queue_ui_preview_update(path: str | None = None) -> None:
    """Queue a main-window preview refresh without relying on the legacy Tk canvas."""
    try:
        if root is None or not hasattr(root, "after"):
            return
        def _refresh():
            try:
                window = getattr(root, "window", None)
                if window is not None and hasattr(window, "update_preview"):
                    window.update_preview()
                else:
                    update_preview(path or config.get("current_wallpaper", ""))
            except Exception as exc:
                log(f"刷新预览失败: {exc}")
        root.after(0, _refresh)
    except Exception as exc:
        log(f"无法排队刷新预览: {exc}")


def _slideshow_index_map(images=None) -> dict[str, tuple[int, str]]:
    """Return a cached path-key -> (index, path) map for the current slideshow list."""
    global _slide_images_index_cache, _slide_images_index_signature
    candidates = images if images is not None else slide_images
    if not candidates:
        return {}
    try:
        signature = (len(candidates), _history_key(candidates[0]), _history_key(candidates[-1]))
    except Exception:
        signature = (len(candidates), "", "")
    if images is None and signature == _slide_images_index_signature and _slide_images_index_cache:
        return _slide_images_index_cache
    mapping: dict[str, tuple[int, str]] = {}
    for idx, img in enumerate(candidates):
        key = _history_key(img)
        if key and key not in mapping:
            mapping[key] = (idx, _normalize_wallpaper_path(img))
    if images is None:
        _slide_images_index_signature = signature
        _slide_images_index_cache = mapping
    return mapping


def _invalidate_slideshow_index_cache() -> None:
    global _slide_images_index_cache, _slide_images_index_signature
    _slide_images_index_cache = {}
    _slide_images_index_signature = None


def _find_wallpaper_in_slideshow_images(path: str, images=None) -> str:
    """在幻灯片列表中按规范化路径查找同一张图，避免路径大小写/分隔符差异导致回退到第一张。"""
    path = _normalize_wallpaper_path(path or "")
    if not path:
        return ""
    item = _slideshow_index_map(images).get(_history_key(path))
    return item[1] if item else ""


def _remember_slideshow_wallpaper(path: str, *, persist: bool = False) -> bool:
    """单独记住幻灯片最后播放到哪一张，不受退出时恢复启动前壁纸影响。"""
    if normalize_mode_key(config.get("mode")) != "幻灯片放映":
        return False
    path = _normalize_wallpaper_path(path or "")
    if not path or not os.path.isfile(path):
        return False
    matched = _find_wallpaper_in_slideshow_images(path)
    if not matched:
        folder = _normalize_wallpaper_path(config.get("slide_folder", ""))
        try:
            if not folder or os.path.commonpath([os.path.abspath(folder), os.path.abspath(path)]) != os.path.abspath(folder):
                return False
        except Exception:
            return False
        matched = path
    if config.get("slideshow_last_wallpaper") == matched:
        return False
    config["slideshow_last_wallpaper"] = matched
    if persist:
        save_config()
    return True


def switch_wallpaper_mode(target: str | None = "next") -> bool:
    """Cycle to the next wallpaper mode or switch to a specific mode.

    The public UI shows HTML as “网页”, while config stores the canonical
    internal key "HTML". This helper normalizes aliases so tray, global hotkey,
    Windows desktop context menu and command-line entry points behave the same
    on all platforms.
    """
    try:
        order: list[str] = []
        for item in MODE_KEYS:
            key = normalize_mode_key(item)
            if key and key not in order:
                order.append(key)
        if "HTML" not in order:
            order.append("HTML")
        raw_target = str(target or "next").strip()
        if raw_target.lower() in {"next", "cycle"}:
            current = normalize_mode_key(config.get("mode"))
            try:
                index = order.index(current)
            except ValueError:
                index = -1
            mode_key = order[(index + 1) % len(order)]
        else:
            mode_key = normalize_mode_key(raw_target)
        if mode_key not in order:
            mode_key = normalize_mode_key(mode_key)
        config["mode"] = mode_key
        save_config()

        if mode_key == "幻灯片放映":
            stop_video_wallpaper()
            folder = config.get("slide_folder")
            if folder and os.path.isdir(folder):
                return bool(restart_slideshow())
            return True
        if mode_key == "图片":
            stop_slideshow()
            stop_video_wallpaper()
            image = config.get("single_image")
            if image and os.path.exists(image):
                return bool(set_wallpaper(image, t("切换单张图片模式")))
            return True
        if mode_key == "视频":
            stop_slideshow()
            video = config.get("video_file")
            if video and os.path.exists(video):
                return bool(start_video_wallpaper(video))
            stop_video_wallpaper()
            return True
        if mode_key == "HTML":
            stop_slideshow()
            stop_video_wallpaper()
            path = config.get("html_file") or config.get("html_url") or ""
            if path:
                return bool(start_html_wallpaper(path))
            try:
                stop_html_wallpaper()
            except Exception:
                pass
            return True
        if mode_key == "纯色":
            stop_slideshow()
            stop_video_wallpaper()
            return bool(apply_solid())
        if mode_key == "渐变":
            stop_slideshow()
            stop_video_wallpaper()
            return bool(apply_gradient())
        return True
    except Exception as exc:
        log_error("switch wallpaper mode failed", exc)
        return False


def set_wallpaper_direct(path, operation_name="系统", skip_history=False, previous_path: str | None = None, progress_cb=None):
    """直接设置壁纸到系统，区分 OSError 和通用异常。

    Bug 7 fix: 新增 ``progress_cb`` 参数 — 可选的
    ``Callable[[str, float], None]``，用于在壁纸应用的不同阶段回调
    状态文本和进度百分比。
    """
    global last_operation_error
    if is_operation_cancelled():
        log("壁纸操作已在开始前终止")
        last_operation_error = "壁纸操作已在开始前终止"
        return False
    path = _normalize_wallpaper_path(path)
    if not os.path.isfile(path):
        log("壁纸文件不存在: " + path)
        last_operation_error = "壁纸文件不存在: " + path
        return False

    def _emit(status: str, percent: float) -> None:
        if progress_cb is not None:
            try:
                progress_cb(status, percent)
            except Exception:
                pass

    try:
        if normalize_mode_key(config.get("mode")) != "视频" and is_video_wallpaper_running():
            _emit("正在停止视频壁纸…", 0.1)
            stop_video_wallpaper()
        fit_mode = config.get("fit_mode", "填充")
        _emit("正在应用适应方式…", 0.2)
        configure_fit_mode(fit_mode, winreg, log)
        if is_operation_cancelled():
            log("壁纸操作已终止，跳过系统壁纸设置")
            last_operation_error = "壁纸操作已终止，跳过系统壁纸设置"
            return False
        _emit("正在设置壁纸…", 0.5)
        set_wallpaper_platform(path)
        _emit("正在刷新桌面…", 0.85)
        try:
            refresh_shell_ui()
        except Exception:
            pass
        if not skip_history:
            config["current_wallpaper"] = path
            _remember_slideshow_wallpaper(path, persist=False)
            save_config()
        log("设置壁纸成功: " + os.path.basename(path))
        log_time_diff(operation_name, path)
        _queue_ui_preview_update(path)
        # 成功路径：清空最近一次操作错误，避免下次失败时显示陈旧原因
        last_operation_error = ""
        _emit("完成", 1.0)
        _invalidate_current_wallpaper_cache()
        return True
    except OSError as e:
        log("设置壁纸失败（系统错误）: " + str(e))
        last_operation_error = "设置壁纸失败（系统错误）: " + str(e)
        return False
    except Exception as e:
        log("设置壁纸失败（未知错误）: " + str(e))
        last_operation_error = "设置壁纸失败（未知错误）: " + str(e)
        return False


def get_windows_wallpaper_style():
    if not IS_WINDOWS or winreg is None:
        return {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            style = {}
            for value_name in ("WallpaperStyle", "TileWallpaper"):
                try:
                    style[value_name] = winreg.QueryValueEx(key, value_name)[0]
                except FileNotFoundError:
                    pass
            return style
    except Exception as e:
        log(f"读取原始壁纸样式失败: {e}")
        return {}


def restore_windows_wallpaper_style(style):
    if not IS_WINDOWS or winreg is None or not style:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE) as key:
            for value_name, value in style.items():
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, str(value))
    except Exception as e:
        log(f"恢复原始壁纸样式失败: {e}")


def capture_session_original_wallpaper(inherit_existing: bool = False, force_refresh: bool = False):
    """记录本次程序启动前的静态壁纸。

    这里不再用 config["current_wallpaper"] 兜底：配置记录的是本软件上次设置的壁纸，
    读取系统当前壁纸失败时盲目兜底会把“软件壁纸”误记录为“启动前壁纸”。
    """
    global session_original_wallpaper, session_original_wallpaper_style, session_original_wallpaper_captured
    with _session_wallpaper_lock:
        if session_original_wallpaper_captured and not force_refresh:
            return True
        if force_refresh:
            session_original_wallpaper = ""
            session_original_wallpaper_style = {}
            session_original_wallpaper_captured = False
        if inherit_existing:
            if _load_session_original_wallpaper():
                return True
            if not force_refresh:
                log("未找到有效的继承壁纸会话，避免误记录当前程序壁纸")
                return False
        if not inherit_existing:
            _clear_session_wallpaper_file()
        try:
            current = _normalize_wallpaper_path(get_current_wallpaper())
            if not _is_restorable_wallpaper_path(current):
                session_original_wallpaper_captured = False
                log("启动前壁纸不是可恢复的本地图片文件，已跳过记录: " + (current or "<empty>"))
                return False
            session_original_wallpaper = current
            session_original_wallpaper_style = get_windows_wallpaper_style()
            session_original_wallpaper_captured = True
            _persist_session_original_wallpaper()
            log(f"已记录启动前壁纸: {session_original_wallpaper}")
            return True
        except Exception as e:
            session_original_wallpaper_captured = False
            log(f"记录启动前壁纸失败: {e}")
            return False


def restore_session_original_wallpaper(stop_video: bool = True):
    """恢复本次程序启动前的静态壁纸；返回是否成功恢复/无需恢复。"""
    global session_original_wallpaper, session_original_wallpaper_style, session_original_wallpaper_captured
    with _session_wallpaper_lock:
        if not session_original_wallpaper:
            _load_session_original_wallpaper()
        target = _normalize_wallpaper_path(session_original_wallpaper)
    if not target:
        log("没有可恢复的启动前壁纸记录")
        return False
    if not _is_restorable_wallpaper_path(target):
        log(f"启动前壁纸不可恢复或文件已不存在，跳过恢复: {target}")
        _clear_session_wallpaper_file()
        return False
    try:
        current = _normalize_wallpaper_path(get_current_wallpaper())
        if current and _is_same_wallpaper_path(current, target):
            log("当前壁纸已经是启动前壁纸，无需恢复")
            _clear_session_wallpaper_file()
            return True
    except Exception:
        pass
    try:
        if stop_video:
            try:
                stop_video_wallpaper()
            except Exception as exc:
                log(f"恢复前停止视频壁纸失败: {exc}")
        restore_windows_wallpaper_style(session_original_wallpaper_style)
        set_wallpaper_platform(target)
        try:
            refresh_shell_ui()
        except Exception:
            pass
        config["current_wallpaper"] = _normalize_wallpaper_path(target)
        save_config()
        _clear_session_wallpaper_file()
        with _session_wallpaper_lock:
            session_original_wallpaper = ""
            session_original_wallpaper_style = {}
            session_original_wallpaper_captured = False
        log("已恢复启动前壁纸: " + os.path.basename(target))
        return True
    except Exception as e:
        log(f"恢复启动前壁纸失败: {e}")
        return False


def set_wallpaper(path, operation_name="用户", progress_cb=None):
    if is_operation_cancelled():
        log("壁纸操作已终止")
        return False
    path = _normalize_wallpaper_path(path)
    if not os.path.isfile(path):
        return False
    previous_path = config.get("current_wallpaper") or get_current_wallpaper()
    success = set_wallpaper_direct(path, operation_name, skip_history=True, previous_path=previous_path, progress_cb=progress_cb)
    if success:
        _remember_slideshow_wallpaper(path, persist=False)
        push_wallpaper(path, update_current=True, refresh_preview=False)
    return success


def previous_wallpaper():
    """切换到上一张壁纸，支持历史记录回退。"""
    global last_operation_error
    with _config_lock:
        hist = dedupe_wallpaper_history(config.get("history", []), keep_missing=False)
        config["history"] = hist[:50]
    log("当前历史: " + str([os.path.basename(p) for p in hist[:5]]) + ("..." if len(hist) > 5 else ""))
    if len(hist) < 2:
        log("没有上一张壁纸")
        # 不能在 worker 线程弹 QMessageBox（会导致 GUI 未响应）。
        last_operation_error = t("没有上一张壁纸")
        raise RuntimeError(last_operation_error)
    found = None
    for p in hist[1:]:
        if os.path.exists(p):
            found = p
            break
        else:
            log("历史壁纸文件丢失: " + p)
    if found is None:
        log("历史壁纸文件都已丢失")
        last_operation_error = t("历史壁纸文件已丢失")
        raise RuntimeError(last_operation_error)
    found_key = _history_key(found)
    with _config_lock:
        latest_hist = dedupe_wallpaper_history(config.get("history", hist), keep_missing=False)
        new_hist = [found] + [p for p in latest_hist if _history_key(p) != found_key]
        config["history"] = dedupe_wallpaper_history(new_hist, keep_missing=True)[:50]
        save_config()
    log("回退到: " + os.path.basename(found))
    success = set_wallpaper(found, "右键菜单(上一张)")
    if success and normalize_mode_key(config.get("mode")) == "幻灯片放映":
        try:
            reset_slide_timer()
        except Exception:
            pass
    log("=" * 50)


def next_wallpaper():
    """切换到下一张壁纸，根据当前模式选择顺序或随机切换。

    HTML 模式下"下一张"重新加载当前 HTML 壁纸（相当于刷新页面）；
    其他非幻灯片模式（图片/视频/纯色/渐变）下抛 RuntimeError 提示
    用户切到幻灯片放映模式。
    """
    global last_operation_error
    current_mode = normalize_mode_key(config.get("mode"))
    # HTML 模式：刷新当前 HTML 壁纸（重启子进程 = 重新加载页面）
    if current_mode == "HTML":
        html_path = config.get("html_file", "")
        if not html_path:
            last_operation_error = t("当前没有 HTML 壁纸可刷新")
            raise RuntimeError(last_operation_error)
        log("HTML 模式：刷新 HTML 壁纸")
        try:
            if is_html_wallpaper_running():
                return restart_html_wallpaper(html_path)
            return start_html_wallpaper(html_path)
        except Exception as exc:
            last_operation_error = str(exc) or t("刷新 HTML 壁纸失败")
            raise RuntimeError(last_operation_error) from exc
    if current_mode != "幻灯片放映":
        log("当前模式不是幻灯片放映，无法使用下一张功能")
        # 不能在 worker 线程弹 QMessageBox（会导致 GUI 未响应）。
        # 改为抛 RuntimeError，由 GUI 线程的 _on_core_finished 统一显示。
        last_operation_error = t("请在幻灯片放映模式下使用此功能")
        raise RuntimeError(last_operation_error)
    global slide_images
    if not slide_images:
        folder = config["slide_folder"]
        if folder and os.path.isdir(folder):
            try:
                images = random_copy.get_original_image_paths(folder)
            except Exception:
                images = []
            if images:
                slide_images = [p for p in images]
                _invalidate_slideshow_index_cache()
                if config.get("shuffle"):
                    random.shuffle(slide_images)
                log(f"重新加载 {len(slide_images)} 张图片")
            else:
                log("幻灯片列表为空，无法切换到下一张")
                last_operation_error = t("请先设置幻灯片文件夹")
                raise RuntimeError(last_operation_error)
        else:
            log("幻灯片列表为空，无法切换到下一张")
            last_operation_error = t("请先设置幻灯片文件夹")
            raise RuntimeError(last_operation_error)
    next_img = get_next_wallpaper()
    if next_img is None:
        log("无法获取下一张壁纸")
        last_operation_error = t("无法获取下一张壁纸")
        raise RuntimeError(last_operation_error)
    log("切换到: " + os.path.basename(next_img))
    success = set_wallpaper(next_img, "右键菜单(下一张)")
    if success:
        try:
            reset_slide_timer()
        except Exception:
            pass
    log("=" * 50)


def random_wallpaper():
    """随机切换到一张壁纸，从幻灯片文件夹中随机选择。

    HTML 模式下"随机"等同于刷新 HTML 壁纸（与"下一张"行为一致）。
    """
    global last_operation_error
    current_mode = normalize_mode_key(config.get("mode"))
    if current_mode == "HTML":
        html_path = config.get("html_file", "")
        if not html_path:
            last_operation_error = t("当前没有 HTML 壁纸可刷新")
            raise RuntimeError(last_operation_error)
        log("HTML 模式：刷新 HTML 壁纸")
        try:
            if is_html_wallpaper_running():
                return restart_html_wallpaper(html_path)
            return start_html_wallpaper(html_path)
        except Exception as exc:
            last_operation_error = str(exc) or t("刷新 HTML 壁纸失败")
            raise RuntimeError(last_operation_error) from exc
    if current_mode != "幻灯片放映":
        log("当前模式不是幻灯片放映，无法使用随机功能")
        last_operation_error = t("请在幻灯片放映模式下使用此功能")
        raise RuntimeError(last_operation_error)
    global slide_images
    folder = config["slide_folder"]
    if not folder or not os.path.isdir(folder):
        log("幻灯片文件夹无效")
        last_operation_error = t("请先设置幻灯片文件夹")
        raise RuntimeError(last_operation_error)

    # 新版随机概率使用 random.json 中的权重，不再依赖物理副本文件。
    slide_images = random_copy.get_original_image_paths(folder)
    _invalidate_slideshow_index_cache()
    if not slide_images:
        log("文件夹中没有图片")
        last_operation_error = t("文件夹中没有图片")
        raise RuntimeError(last_operation_error)

    current = config.get("current_wallpaper", "")
    random_img = random_copy.weighted_choice(folder, current)
    if not random_img:
        random_img = random.choice(slide_images)
    log("随机切换到: " + os.path.basename(random_img))
    success = set_wallpaper(random_img, "右键菜单(随机)")
    if success:
        try:
            reset_slide_timer()
        except Exception:
            pass
    log("=" * 50)


def set_fit_mode(mode):
    try:
        mode = normalize_style_key(mode)
        config["fit_mode"] = mode
        configure_fit_mode(mode, winreg, log)
        current = config.get("current_wallpaper")
        applied_path = None
        if current and os.path.exists(current):
            set_wallpaper_direct(current, "适应模式")
            applied_path = current
        else:
            # 当前壁纸已不存在时，回退到 history 中最近一张仍存在的壁纸，
            # 避免切换适应方式后用户看不到任何反馈。
            history = config.get("history", []) or []
            if isinstance(history, list):
                for entry in reversed(history):
                    if isinstance(entry, dict):
                        candidate = entry.get("path", "")
                    else:
                        candidate = str(entry)
                    if candidate and os.path.exists(candidate):
                        set_wallpaper_direct(candidate, "适应模式")
                        applied_path = candidate
                        break
            if applied_path is None:
                log("适应模式: 当前壁纸已不存在且历史记录中无可回退项，未重新应用")
        if applied_path is not None:
            log("适应模式: " + mode + " (reapplied: " + str(applied_path) + ")")
        else:
            log("适应模式: " + mode)
    except Exception as e:
        log("设置适应模式失败: " + str(e))


def get_next_wallpaper():
    global slide_images
    if not slide_images:
        return None
    index_map = _slideshow_index_map()
    current_key = _history_key(config.get("current_wallpaper", ""))
    last_key = _history_key(config.get("slideshow_last_wallpaper", ""))
    item = index_map.get(current_key) or index_map.get(last_key)
    if item:
        next_idx = (item[0] + 1) % len(slide_images)
        return slide_images[next_idx]
    return slide_images[0] if slide_images else None


def _schedule_slide_timer_locked():
    """Schedule slideshow work on a background timer, never on the GUI event loop."""
    global slide_timer
    try:
        delay = max(5, int(config.get("slide_seconds", 300)))
    except Exception:
        delay = 300
    slide_timer = threading.Timer(delay, slide_next)
    slide_timer.daemon = True
    slide_timer.start()


def slide_next():
    global slide_timer, slide_enabled
    if is_operation_cancelled():
        log("幻灯片切换已终止")
        return
    next_img = None
    with slide_timer_lock:
        if not slide_enabled:
            return
        # 如果开启了随机顺序，使用 random.json 中的权重选择原始壁纸。
        if config.get("shuffle", False):
            folder = config["slide_folder"]
            if folder and os.path.isdir(folder):
                current = config.get("current_wallpaper", "")
                next_img = random_copy.weighted_choice(folder, current) or get_next_wallpaper()
            else:
                next_img = get_next_wallpaper()
        else:
            next_img = get_next_wallpaper()
        slide_timer = None
    if next_img is None:
        return
    set_wallpaper(next_img, "幻灯片")
    with slide_timer_lock:
        if slide_enabled:
            _schedule_slide_timer_locked()


def reset_slide_timer():
    """重置幻灯片定时器，根据配置的间隔时间重新计时。"""
    global slide_timer
    with slide_timer_lock:
        if not slide_enabled or not slide_images:
            return
        current = (
            _find_wallpaper_in_slideshow_images(config.get("current_wallpaper", ""), slide_images)
            or _find_wallpaper_in_slideshow_images(config.get("slideshow_last_wallpaper", ""), slide_images)
        )
        if current not in slide_images:
            return
        if slide_timer:
            if root is not None and isinstance(slide_timer, str):
                try:
                    root.after_cancel(slide_timer)
                except Exception:
                    pass
            else:
                try:
                    slide_timer.cancel()
                except Exception:
                    pass
            slide_timer = None
        _schedule_slide_timer_locked()


def start_slideshow(is_startup: bool = False):
    """启动幻灯片。

    退出时会恢复“启动前壁纸”，因此 current_wallpaper 可能不是上次播放的幻灯片。
    这里优先使用 slideshow_last_wallpaper 作为恢复/继续播放锚点，避免每次启动都回到第一张。
    """
    if is_operation_cancelled():
        log("幻灯片启动已终止")
        return False
    # 启动幻灯片前停止所有动态壁纸（视频/HTML）
    try:
        stop_video_wallpaper()
    except Exception:
        pass
    global slide_images, slide_enabled, slide_timer
    target_to_apply = None
    restore_current = None
    with slide_timer_lock:
        if normalize_mode_key(config.get("mode")) != "幻灯片放映":
            return False
        folder = config["slide_folder"]
        if not folder or not os.path.isdir(folder):
            return False
        images = random_copy.get_original_image_paths(folder)
        if not images:
            return False
        if config["shuffle"]:
            random.shuffle(images)
        slide_images = [_normalize_wallpaper_path(p) for p in images]
        _invalidate_slideshow_index_cache()
        log(f"加载 {len(slide_images)} 张图片")

        last_slide = _find_wallpaper_in_slideshow_images(config.get("slideshow_last_wallpaper", ""), slide_images)
        current = _find_wallpaper_in_slideshow_images(config.get("current_wallpaper", ""), slide_images)
        anchor = last_slide or current

        if anchor:
            # 启动恢复和手动重启都优先停在上次播放到的幻灯片，而不是回到列表第一张。
            restore_current = anchor
        elif not is_startup:
            # 只有用户主动启动/重启且没有可用锚点时，才从第一张开始。
            target_to_apply = slide_images[0]

        if slide_timer:
            try:
                if root is not None and isinstance(slide_timer, str):
                    root.after_cancel(slide_timer)
                else:
                    slide_timer.cancel()
            except Exception as e:
                log(f"取消旧定时器失败: {e}")
            slide_timer = None
        slide_enabled = True

    # 不在 slide_timer_lock 中执行系统壁纸设置；系统 API 可能耗时。
    if target_to_apply and not is_operation_cancelled():
        set_wallpaper(target_to_apply, "幻灯片启动")
    elif restore_current and not is_operation_cancelled():
        # 继续播放/启动恢复也走统一 set_wallpaper 路径，保证 current_wallpaper、
        # slideshow_last_wallpaper、历史和预览状态一致；否则恢复启动前壁纸后，
        # 仅直接设置系统壁纸可能让 GUI/配置状态滞后到下一次手动切换才修正。
        set_wallpaper(restore_current, "幻灯片恢复")

    with slide_timer_lock:
        if not slide_enabled:
            return True
        _schedule_slide_timer_locked()
    log(f"幻灯片启动，间隔 {config['slide_seconds']} 秒")
    return True


def stop_slideshow():
    """停止幻灯片计时器；可安全重复调用，不把“未运行”误报为错误。"""
    global slide_enabled, slide_timer
    # 停止幻灯片时同步停止动态壁纸（视频/HTML）
    try:
        stop_video_wallpaper()
    except Exception:
        pass
    with slide_timer_lock:
        was_running = bool(slide_enabled) or bool(slide_timer)
        slide_enabled = False
        if slide_timer:
            try:
                if root is not None and isinstance(slide_timer, str):
                    root.after_cancel(slide_timer)
                else:
                    slide_timer.cancel()
            except Exception as exc:
                log_error("取消幻灯片定时器失败", exc)
            slide_timer = None
        if was_running:
            log("幻灯片已停止")
    return True


def restart_slideshow():
    """重启幻灯片放映，返回实际启动结果，便于 UI 正确显示状态。"""
    stop_slideshow()
    if is_operation_cancelled():
        log("幻灯片重启已终止")
        return False
    if normalize_mode_key(config.get("mode")) == "幻灯片放映" and config.get("slide_folder"):
        return start_slideshow()
    log("当前不是幻灯片放映模式或未设置文件夹，跳过重启")
    return False


def start_video_wallpaper(path: str | None = None):
    """启动视频壁纸。播放进程独立运行，避免阻塞 GUI 主线程。"""
    target = _normalize_wallpaper_path(path or config.get("video_file", ""))
    if not target:
        message = t("请先选择视频文件")
        log("视频壁纸路径为空")
        raise RuntimeError(message)
    if video_wallpaper is None:
        message = t("视频壁纸模块不可用")
        log(message)
        raise RuntimeError(message)
    if not session_original_wallpaper_captured:
        capture_session_original_wallpaper(inherit_existing=False, force_refresh=False)
    muted = bool(config.get("video_muted", True))
    # Clamp volume to 0-100; out-of-range legacy values silently fall back to 100.
    try:
        _raw_volume = int(config.get("video_volume", 100))
    except (TypeError, ValueError):
        _raw_volume = 100
    volume = max(0, min(100, _raw_volume))
    ok, message = video_wallpaper.start_video_wallpaper(target, muted=muted, volume=volume)
    if ok:
        config["mode"] = "视频"
        config["video_file"] = target
        config["current_wallpaper"] = target
        save_config()
        if message:
            log("视频壁纸提示: " + str(message))
        log("视频壁纸已启动: " + os.path.basename(target))
        return True
    log("视频壁纸启动失败: " + str(message))
    raise RuntimeError(str(message))


def stop_video_wallpaper():
    """停止视频壁纸；可安全重复调用，未运行时不误报“已停止”。"""
    try:
        # 先停止 HTML 壁纸以防止动态模式冲突
        try:
            if html_wallpaper is not None and html_wallpaper.is_html_wallpaper_running():
                html_wallpaper.stop_html_wallpaper()
                log("HTML 壁纸已停止")
        except Exception as exc:
            log_error("停止 HTML 壁纸失败", exc)
        # 再停止视频壁纸
        if video_wallpaper is None:
            return False
        was_running = False
        try:
            was_running = bool(video_wallpaper.is_video_wallpaper_running())
        except Exception as exc:
            log_error("检查视频壁纸运行状态失败", exc)
        video_wallpaper.stop_video_wallpaper()
        if was_running:
            log("视频壁纸已停止")
        return True
    except Exception as e:
        log_error("停止视频壁纸失败", e)
        return False


def is_video_wallpaper_running():
    try:
        return bool(video_wallpaper is not None and video_wallpaper.is_video_wallpaper_running())
    except Exception:
        return False


def set_video_paused(paused: bool) -> bool:
    """实时暂停/恢复视频壁纸。当前主要由 mpv JSON IPC 支持。"""
    try:
        if video_wallpaper is None:
            return False
        if not hasattr(video_wallpaper, "set_video_paused"):
            return False
        return bool(video_wallpaper.set_video_paused(bool(paused)))
    except Exception as exc:
        log_error("实时暂停/恢复视频失败", exc)
        return False

def set_video_volume(muted: bool, volume: int) -> bool:
    """实时调整视频壁纸音量/静音，不中断播放。

    返回 True 表示后端已通过 IPC 热更新；返回 False 表示后端不支持热更新，
    GUI 应回退到 stop + start 重新启动播放进程。即使返回 False 也不抛异常，
    调用方负责决定是否触发回退路径。
    """
    try:
        if video_wallpaper is None:
            return False
        if not hasattr(video_wallpaper, "set_video_volume"):
            return False
        return bool(video_wallpaper.set_video_volume(bool(muted), int(volume)))
    except Exception as exc:
        log_error("实时调整视频音量失败", exc)
        return False


# ====================== HTML 壁纸控制 ===========================

def _sync_html_wallpaper_runtime_options_from_config() -> None:
    """Publish config-backed HTML runtime options before launch/restart."""
    try:
        html_wallpaper_runtime_set_option("auto_pause", bool(config.get("html_auto_pause", True)))
        html_wallpaper_runtime_set_option("gpu_enabled", bool(config.get("html_gpu_enabled", True)))
        html_wallpaper_runtime_set_option("mouse_through", bool(config.get("html_mouse_through", True)))
    except Exception as exc:
        log_error("同步 HTML 壁纸运行选项失败", exc)


def start_html_wallpaper(path: str | None = None):
    """启动 HTML 交互式壁纸。

    如果适配器不可用或依赖缺失，将抛出 RuntimeError。"""
    target = _normalize_wallpaper_path(path or config.get("html_file", ""))
    if path is None:
        target = config.get("html_file", "") or config.get("html_url", "") or target
    if not target:
        message = t("请先选择 HTML 文件或输入 URL")
        log(message)
        raise RuntimeError(message)
    if html_wallpaper is None:
        message = t("HTML 壁纸模块不可用")
        log(message)
        raise RuntimeError(message)
    if not html_wallpaper.validate_html_path(target):
        message = t("所选文件不是有效的 HTML 文件或 URL")
        log(message)
        raise RuntimeError(message)
    if not session_original_wallpaper_captured:
        capture_session_original_wallpaper(inherit_existing=False, force_refresh=False)
    _sync_html_wallpaper_runtime_options_from_config()
    try:
        ok, message = html_wallpaper.start_html_wallpaper(target)
    except Exception as exc:
        log(f"HTML 壁纸启动失败: {exc}")
        raise RuntimeError(str(exc))
    if ok:
        config["mode"] = "HTML"
        config["html_file"] = target
        config["current_wallpaper"] = target
        save_config()
        if message:
            log("HTML 壁纸提示: " + str(message))
        log("HTML 壁纸已启动: " + os.path.basename(target))
        return True
    log("HTML 壁纸启动失败: " + str(message))
    raise RuntimeError(str(message))


def stop_html_wallpaper() -> bool:
    try:
        if html_wallpaper is None:
            return False
        was_running = False
        try:
            was_running = bool(html_wallpaper.is_html_wallpaper_running())
        except Exception as exc:
            log_error("检查 HTML 壁纸运行状态失败", exc)
        html_wallpaper.stop_html_wallpaper()
        if was_running:
            log("HTML 壁纸已停止")
        return True
    except Exception as e:
        log_error("停止 HTML 壁纸失败", e)
        return False


def is_html_wallpaper_running() -> bool:
    try:
        return bool(html_wallpaper is not None and html_wallpaper.is_html_wallpaper_running())
    except Exception:
        return False


def html_wallpaper_runtime_set_option(key: str, value) -> bool:
    """热更新 HTML 壁纸子进程的运行时选项（目前支持 auto_pause）。"""
    try:
        if html_wallpaper is None or not hasattr(html_wallpaper, "runtime_set_option"):
            return False
        return bool(html_wallpaper.runtime_set_option(str(key), value))
    except Exception as exc:
        log_error(f"热更新 HTML 壁纸选项失败({key}={value})", exc)
        return False


def html_wallpaper_get_last_path() -> str:
    """返回上次启动 HTML 壁纸时使用的路径，用于在切换 GPU 等不可热更新选项后重启。"""
    try:
        if html_wallpaper is None or not hasattr(html_wallpaper, "get_last_path"):
            return ""
        return str(html_wallpaper.get_last_path() or "")
    except Exception:
        return ""


def restart_html_wallpaper(path: str | None = None) -> bool:
    """停止并以同一路径（或指定路径）重新启动 HTML 壁纸。"""
    try:
        if html_wallpaper is None or not hasattr(html_wallpaper, "restart_html_wallpaper"):
            return False
        target = path or html_wallpaper_get_last_path() or config.get("html_file", "")
        if not target:
            log("重启 HTML 壁纸失败：未找到上次使用的路径")
            return False
        _sync_html_wallpaper_runtime_options_from_config()
        ok, message = html_wallpaper.restart_html_wallpaper(target)
        if ok:
            log("HTML 壁纸已重启")
            return True
        log("HTML 壁纸重启失败: " + str(message))
        return False
    except Exception as exc:
        log_error("重启 HTML 壁纸失败", exc)
        return False


# ====================== 优化的渐变生成函数 ======================
def create_gradient_wallpaper_optimized(color1, color2, angle=0):
    """使用 Pillow 自带渐变/合成生成壁纸；不再依赖 NumPy。"""
    try:
        if Image is None:
            log("Pillow 不可用，无法生成渐变壁纸")
            return None
        screen_width = get_screen_size(root)[0]
        screen_height = get_screen_size(root)[1]
        diag = int(math.ceil(math.sqrt(screen_width ** 2 + screen_height ** 2)))
        diag = max(diag, screen_width, screen_height, 2)
        mask = Image.linear_gradient("L").resize((diag, diag))
        # Pillow 的线性渐变默认从上到下；旋转后居中裁切到屏幕大小。
        mask = mask.rotate(float(angle) - 90.0, resample=Image.Resampling.BICUBIC, expand=False)
        left = max(0, (diag - screen_width) // 2)
        top = max(0, (diag - screen_height) // 2)
        mask = mask.crop((left, top, left + screen_width, top + screen_height))
        base = Image.new("RGB", (screen_width, screen_height), color1)
        overlay = Image.new("RGB", (screen_width, screen_height), color2)
        img = Image.composite(overlay, base, mask)
        diy_dir = os.path.join(DATA_DIR, "diy")
        os.makedirs(diy_dir, exist_ok=True)
        bmp_path = os.path.join(diy_dir, "gradient_wallpaper.png")
        img.save(bmp_path)
        log("渐变壁纸生成完成 (Pillow/Qt 无 NumPy 引擎)")
        return bmp_path
    except Exception as e:
        log("创建渐变壁纸失败: " + str(e))
        return None


def create_gradient_wallpaper(color1, color2, angle=0):
    return create_gradient_wallpaper_optimized(color1, color2, angle)


def apply_gradient():
    color1 = config.get("solid_color", "#2d2d2d")
    color2 = config.get("gradient_color2", "#4a4a4a")
    angle = config.get("gradient_angle", 0)
    bmp_path = create_gradient_wallpaper(color1, color2, angle)
    if bmp_path and os.path.exists(bmp_path):
        set_wallpaper(bmp_path, "渐变壁纸")


def apply_solid():
    color = config.get("solid_color", "#2d2d2d")
    screen_width = get_screen_size(root)[0]
    screen_height = get_screen_size(root)[1]
    img = Image.new("RGB", (screen_width, screen_height), color)
    diy_dir = os.path.join(DATA_DIR, "diy")
    os.makedirs(diy_dir, exist_ok=True)
    bmp_path = os.path.join(diy_dir, "solid_wallpaper.bmp")
    img.save(bmp_path)
    if os.path.exists(bmp_path):
        set_wallpaper(bmp_path, "纯色壁纸")


def update_preview(img_path):
    return


def show_main_window_now():
    """显示主窗口并强制置于前台。

    使用 AttachThreadInput 解决 SetForegroundWindow 在某些情况下
    无法将窗口置于前台的问题（Windows 前台锁定限制）。
    """
    global pending_show_request
    pending_show_request = False
    if root is None:
        return
    try:
        root.deiconify()
        root.state("normal")
        root.lift()
        root.focus_force()
        if IS_WINDOWS:
            try:
                hwnd_root = root.winfo_id()
                current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                target_thread = ctypes.windll.user32.GetWindowThreadProcessId(hwnd_root, None)
                if current_thread != target_thread:
                    ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)
                ctypes.windll.user32.SetForegroundWindow(hwnd_root)
                ctypes.windll.user32.BringWindowToTop(hwnd_root)
                if current_thread != target_thread:
                    try:
                        ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception as e:
        log(f"打开已有主界面失败: {e}")


def request_show_main_window():
    global pending_show_request
    pending_show_request = True
    if root is not None:
        try:
            root.after(0, show_main_window_now)
        except Exception as e:
            log(f"请求打开主界面失败: {e}")


_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = _WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_void_p,
) if IS_WINDOWS else (lambda func: func)


@WNDPROC
def window_proc(hwnd, msg, wparam, lparam):
    hwnd_i = _win_int(hwnd)
    msg_i = int(msg or 0)
    wparam_i = _win_int(wparam)
    lparam_i = _win_int(lparam)
    if msg_i == WM_SETTINGCHANGE:
        log("检测到系统设置变化，检查壁纸")
        current = get_current_wallpaper()
        if current and current != config.get("current_wallpaper", ""):
            log(f"系统壁纸已改变: {os.path.basename(current)}")
            push_wallpaper(current)
            _queue_ui_preview_update(current)
        return 0
    elif msg_i == WM_COPYDATA:
        try:
            if not lparam_i:
                return 0
            cds = ctypes.cast(lparam_i, ctypes.POINTER(COPYDATASTRUCT)).contents
            if cds.dwData == 1:
                data = ctypes.string_at(cds.lpData, cds.cbData)
                command = data.decode('utf-8').rstrip('\x00')
                log(f"收到消息: {command}")
                if command in {"previous", "next", "random"} or command.startswith("set_wallpaper|"):
                    queue_ipc_wallpaper_command(command)
                    return 1
                elif command == "jump":
                    request_show_main_window()
                    try:
                        if root is not None and hasattr(root, "after"):
                            root.after(0, lambda: _gui_open_wallpaper_sidebar())
                    except Exception as exc:
                        log(f"右键菜单跳转壁纸转发失败: {exc}")
                    return 1
                elif command == "show":
                    request_show_main_window()
                    return 1
                elif command == "create_file":
                    return 1
        except Exception as e:
            log(f"消息处理错误: {e}")
        return 0
    return ctypes.windll.user32.DefWindowProcW(hwnd_i, msg_i, wparam_i, lparam_i)


def create_message_window():
    global hwnd, use_message
    if not IS_WINDOWS:
        log("当前平台不需要 Windows 消息窗口")
        return None
    try:
        wc = WNDCLASS()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(window_proc, ctypes.c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = WND_CLASS_NAME
        atom = ctypes.windll.user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = ctypes.windll.kernel32.GetLastError()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                log(f"注册窗口类失败: {err}")
                return None
        hwnd = ctypes.windll.user32.CreateWindowExW(
            0,
            WND_CLASS_NAME,
            "",
            0,
            0, 0, 0, 0,
            _hwnd_message_parent(),
            0,
            wc.hInstance,
            0
        )
        if not hwnd:
            log("创建窗口失败")
            return None
        use_message = True
        log(f"IPC message-only 窗口创建成功, HWND: {hwnd}")
        return hwnd
    except Exception as e:
        log("创建消息窗口失败: " + str(e))
        return None


def message_loop():
    if not IS_WINDOWS or ctypes.wintypes is None:
        return
    msg = ctypes.wintypes.MSG()
    while True:
        ret = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret <= 0:
            break
        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def start_message_window():
    global _message_loop_thread
    if not IS_WINDOWS:
        return None
    if hwnd:
        return hwnd
    msg_hwnd = create_message_window()
    if msg_hwnd and (_message_loop_thread is None or not _message_loop_thread.is_alive()):
        _message_loop_thread = threading.Thread(target=message_loop, daemon=True)
        _message_loop_thread.start()
        log("消息循环已启动")
    return msg_hwnd


def is_context_menu_synced() -> bool:
    """macOS does not provide Windows desktop context-menu registry sync."""
    return False


def register_context(show_admin_prompt=False):
    """No-op on macOS; desktop shell context-menu registration is Windows-only."""
    global last_operation_error
    msg = "当前平台不支持 Windows 桌面右键菜单注册；macOS 版仅提供全局热键与托盘菜单。"
    log(msg)
    last_operation_error = msg
    return False
