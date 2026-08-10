# ShangBackground runtime core used by the PySide6 UI.
from __future__ import annotations

import ctypes
from functools import wraps
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
from app.runtime_state import RuntimeState
from app.bootstrap import (
    ApplicationServices,
    CallbackHotkeyBackend,
    CallbackWallpaperBackend,
    ProviderMediaBackend,
    build_services,
)
from app.config_defaults import build_default_config
from app.config_normalization import (
    migrate_wallpaper_transition_policy,
    normalize_runtime_config,
    normalize_runtime_config_in_place,
)
from app.media_service import MediaServiceError
from app.relaunch_service import RelaunchService, cleanup_tray_icon
from app.wallpaper_action_policy import wallpaper_action_availability
from app.wallpaper_mode_service import WallpaperModeError
from app.paths import RESOURCE_ROOT, user_data_dir, is_packaged_runtime, app_executable_path
from app.config_repository import ConfigRepository
from app.wallpaper_library import WallpaperLibrary
from app.wallpaper_repositories import (
    CollectionPersistenceError,
    FavoritesRepository,
    HistoryRepository,
    normalize_wallpaper_path,
    newer_history_item,
    previous_history_item,
    wallpaper_path_key,
)
from platform_adapters import windows_legacy_ipc
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

from app.optional_runtime import video_wallpaper, html_wallpaper, hotkey_backend_module
_pynput_keyboard = None
_pynput_hotkey_listener = None

WallpaperSidebar = None

WM_COPYDATA = 0x004A
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SPI_GETDESKWALLPAPER = 0x0073
HWND_MESSAGE = -3  # message-only window parent; prevents the IPC window from appearing on screen/taskbar

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

# 全局常量
VERSION = APP_VERSION
CONFIG_PATH = os.path.join(DATA_DIR, "settings.json")
CONFIG_BACKUP_PATH = CONFIG_PATH + ".bak"
BUNDLED_CONFIG_PATH = os.path.join(BASE_DIR, "settings.json")
LEGACY_CONFIG_PATH = os.path.join(DATA_DIR, "shezhi.json")
LEGACY_BUNDLED_CONFIG_PATH = os.path.join(BASE_DIR, "shezhi.json")
CONFIG_REPOSITORY = ConfigRepository(
    CONFIG_PATH,
    backup_path=CONFIG_BACKUP_PATH,
    fallback_paths=(LEGACY_CONFIG_PATH, BUNDLED_CONFIG_PATH, LEGACY_BUNDLED_CONFIG_PATH),
)
# v1.4.6: 移除 TRIGGER_FILE_PREV/NEXT/RANDOM 和 ERROR_LOG_PATH —— 从未被引用,
# 属于早期文件触发机制残留, 现已改用 IPC.
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


# v1.4.6: 移除 apply_image_fit_mode —— 三端定义但零调用.
# 壁纸适应模式现在由 Windows IDesktopWallpaper::SetPosition / 注册表 WallpaperStyle 处理,
# 不需要在 Python 端预裁剪图片.


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
RUNTIME_STATE = RuntimeState()


def _serialized_wallpaper_operation(function):
    """Serialize state-changing wallpaper operations across UI, timer and IPC workers."""
    @wraps(function)
    def _wrapped(*args, **kwargs):
        with RUNTIME_STATE.wallpaper_operation_lock:
            return function(*args, **kwargs)

    return _wrapped


# 记录“本次程序启动前的壁纸”。旧版放在 TEMP 下且所有权限/进程共用同名文件，
# 容易被旧会话、提权进程或其它实例污染；新版放入用户数据目录，并保留旧文件读取兼容。
SESSION_WALLPAPER_FILE = os.path.join(DATA_DIR, "session_original_wallpaper.json")
LEGACY_SESSION_WALLPAPER_FILE = os.path.join(tempfile.gettempdir(), "ShangBackground_session_wallpaper.json")


def _on_pynput_global_hotkey(action: str):
    """Compatibility facade for the former in-engine pynput callback."""
    return _dispatch_global_hotkey_action(action)


def _platform_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform


def _session_wallpaper_files() -> list[str]:
    return _get_application_services().session.files()


def _is_same_wallpaper_path(left: str | None, right: str | None) -> bool:
    return _get_application_services().session.same_path(left, right)


def _is_restorable_wallpaper_path(path: str | None) -> bool:
    return _get_application_services().session.is_restorable(path)


def _persist_session_original_wallpaper():
    return _get_application_services().session.persist()


def _clear_session_wallpaper_file():
    return _get_application_services().session.clear_files()


def _load_session_original_wallpaper(max_age_seconds=24 * 3600):
    return _get_application_services().session.load(max_age_seconds)


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
    return _get_relaunch_service().is_windows_admin()


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
        return True
    except Exception as e:
        log(f"释放单实例守卫失败: {e}")
        return False


def _recover_relaunch_guard() -> None:
    try:
        acquire_single_instance_mutex()
    except Exception as exc:
        log(f"恢复单实例互斥体失败: {exc}", level="ERROR", exc_info=exc)
    try:
        start_message_window()
    except Exception as exc:
        log(f"恢复消息窗口失败: {exc}", level="ERROR", exc_info=exc)


def _cleanup_tray_icon_on_exit():
    """清理托盘相关对象并刷新通知区域。"""
    global tray_icon_obj
    icon = globals().get("tray_icon_obj", None)
    tray_icon_obj = None
    cleanup_tray_icon(icon, is_windows=IS_WINDOWS, sleep=time.sleep)


def _get_relaunch_service() -> RelaunchService:
    return RelaunchService(
        is_windows=IS_WINDOWS,
        is_frozen=is_frozen,
        executable_path=app_executable_path,
        base_dir=lambda: BASE_DIR,
        capture_session=lambda: capture_session_original_wallpaper(
            inherit_existing=True, force_refresh=False
        ),
        persist_session=_persist_session_original_wallpaper,
        release_guard=lambda: perform_exit_cleanup(
            reason="relaunch", restore_wallpaper=False, restarting=True
        ),
        cleanup_tray=_cleanup_tray_icon_on_exit,
        recover_guard=_recover_relaunch_guard,
        log=log,
    )


def restart_application(extra_args=None):
    """Restart the current app without requesting a new UAC elevation."""
    return _get_relaunch_service().restart(extra_args)


def restart_as_admin(extra_args=None):
    """以管理员身份重启当前应用。"""
    if not IS_WINDOWS:
        log(t("非 Windows 平台，改为普通重启"))
        return restart_application(extra_args=extra_args)
    service = _get_relaunch_service()
    if service.is_windows_admin():
        log(t("当前已是管理员权限，执行普通重启"))
        return service.restart(extra_args)
    return service.restart_as_admin(extra_args)


def _do_exit(code=0):
    """安全退出当前进程，用于提权重启后终止旧实例。"""
    # During restart the ExitService intentionally keeps the singleton guard
    # until process termination.  Do any potentially-slow tray work first,
    # then release only at the final exit boundary (the OS would also release
    # the underlying process handles/locks on termination).
    _cleanup_tray_icon_on_exit()
    release_single_instance_mutex()
    try:
        os._exit(code)
    except Exception:
        sys.exit(code)


def acquire_single_instance_mutex():
    """普通权限单实例检测：每用户系统文件锁。"""
    try:
        return single_instance.acquire()
    except Exception as e:
        log(f"单实例守卫检测失败: {e}")
        return True


def _hwnd_message_parent():
    return windows_legacy_ipc.message_parent(HWND, HWND_MESSAGE)


def find_existing_main_window(timeout=2.0):
    return windows_legacy_ipc.find_window(
        timeout=timeout,
        class_name=WND_CLASS_NAME,
        hwnd_type=HWND,
        hwnd_message=HWND_MESSAGE,
        is_windows=IS_WINDOWS,
        log=log,
    )


def send_command_to_hwnd(target_hwnd, command):
    return windows_legacy_ipc.send_command(
        target=target_hwnd,
        command=command,
        copydata_type=COPYDATASTRUCT,
        hwnd_type=HWND,
        uint_type=UINT,
        wparam_type=WPARAM,
        lparam_type=LPARAM,
        wm_copydata=WM_COPYDATA,
        is_windows=IS_WINDOWS,
        win_int=_win_int,
        log=log,
    )


def activate_existing_instance(show_notice=True):
    existing = find_existing_main_window(timeout=5.0)
    activated = windows_legacy_ipc.activate(
        existing=existing,
        send_show=send_command_to_hwnd,
        log=log,
    )
    if show_notice:
        if existing:
            show_message(t("不要重复运行"), t("不要重复运行，已为您打开现有主界面。"))
        else:
            show_message(
                t("不要重复运行"),
                t("不要重复运行。检测到 ShangBackground 已经在启动或运行，本次启动已取消。"),
            )
    return activated


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


def is_owned_startup_vbs(path):
    """Return whether a legacy, generically named VBS belongs to this app."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read(8192).lower()
        return "shangbackground" in content or "xxdz" in content
    except Exception:
        return False


remote_version = "1"
remote_release_notes = ""
remote_download_urls = {"GitHub Release": "", t("发布页"): "https://github.com/purrfecto114-lgtm/ShangBackground/releases/latest"}
show_update_flag = False
check_failed = False


def _history_key(path: str) -> str:
    """Compatibility wrapper for the shared wallpaper path identity rule."""
    return wallpaper_path_key(path)


def _normalize_wallpaper_path(path: str) -> str:
    """Compatibility wrapper for the shared wallpaper path normalizer."""
    return normalize_wallpaper_path(path)


def dedupe_wallpaper_history(history, *, keep_missing: bool = True, limit: int = 20):
    """Compatibility wrapper retained while callers migrate to HistoryRepository."""
    return HistoryRepository.normalize_items(
        history,
        keep_missing=keep_missing,
        limit=limit,
    )


CONFIG_MIGRATION_PENDING = False


def get_default_config() -> dict:
    """Return a fresh factory-default configuration dictionary."""
    return build_default_config()

def load_config():
    """加载配置文件，如果不存在则返回默认配置。

    迁移旧配置时只更新内存，不在导入阶段同步写 settings.json，
    避免 GUI 还没显示就被磁盘/杀软/网络盘卡住。
    """
    global CONFIG_MIGRATION_PENDING
    default = get_default_config()
    load_result = CONFIG_REPOSITORY.load_first_valid()
    for failure in load_result.failures:
        log(f"跳过无效配置 {failure.path.name}: {failure.error}")
    source_path = os.fspath(load_result.source_path) if load_result.source_path else ""
    loaded = load_result.data
    if load_result.source_path == CONFIG_REPOSITORY.backup_path:
        log("主配置不可用，已从 settings.json.bak 恢复")
    if source_path and loaded is not None:
        try:
            data = default.copy()
            data.update(loaded)
            log(f"配置加载成功: {os.path.basename(source_path)}")
            # 自动转换旧配置。
            converted = False
            if "user_id" in data:
                data.pop("user_id", None)
                converted = True
            if isinstance(data.get("tray_menu_items"), list) and data["tray_menu_items"]:
                first_item = data["tray_menu_items"][0]
                if isinstance(first_item, dict) and "action" in first_item:
                    # 旧格式：转换为只存储 action 字符串的新格式。历史配置
                    # 可能混入空值或半迁移条目，因此逐项校验而非直接索引。
                    new_items: list[str] = []
                    for item in data["tray_menu_items"]:
                        action = item.get("action") if isinstance(item, dict) else item
                        if isinstance(action, str) and action.strip():
                            new_items.append(action.strip())
                    data["tray_menu_items"] = new_items
                    converted = True
            # 迁移右键菜单配置：旧版“全局设置/个性化/设置为壁纸”入口已移除。
            if "ctx_jump_to_wallpaper" not in data:
                data["ctx_jump_to_wallpaper"] = bool(data.get("ctx_global_settings", False))
                converted = True
            for _stale_ctx_key in ("ctx_personalize", "ctx_global_settings", "ctx_set_wallpaper"):
                if _stale_ctx_key in data:
                    data.pop(_stale_ctx_key, None)
                    converted = True
            # 迁移旧版 Windows 默认热键 (无修饰键, 无法注册为全局热键) 到 Ctrl+Alt+... 组合.
            # 仅当用户当前值恰好等于旧默认值时才迁移, 尊重用户自定义.
            _legacy_hotkey_defaults = {
                "hotkey_previous": "PgUp",
                "hotkey_next": "PgDown",
                "hotkey_random": "R",
                "hotkey_jump": "J",
            }
            _new_hotkey_defaults = {
                "hotkey_previous": "Ctrl+Alt+U",
                "hotkey_next": "Ctrl+Alt+N",
                "hotkey_random": "Ctrl+Alt+R",
                "hotkey_jump": "Ctrl+Alt+J",
                    }
            for _key, _legacy in _legacy_hotkey_defaults.items():
                if _key not in data:
                    data[_key] = _new_hotkey_defaults[_key]
                    converted = True
                elif str(data.get(_key, "")).strip() == _legacy:
                    # 旧默认值 → 升级为新默认值 (三端统一)
                    data[_key] = _new_hotkey_defaults[_key]
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
                "html_frame_rate": 30,
            }.items():
                if _key not in data:
                    data[_key] = _default
                    converted = True
            if data.pop("html_compatibility_mode", None) is not None:
                converted = True
            if data.pop("html_mouse_through", None) is not None:
                converted = True
            if "hotkey_focus_guard" not in data:
                data["hotkey_focus_guard"] = True
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
            # 旧版 performance_mode=True → performance_level="performance"
            # 旧版 performance_mode=False → performance_level="balanced" (默认)
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
            # v1.4.7: font_size 现在是新功能的合法 key (0=系统默认, 否则 px).
            # 不再 pop. 旧版 font_size 是遗留的无效 key, 但新版会被上面的迁移逻辑
            # 规范化为 0-48 的整数, 所以保留即可.
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

            # Restore the historical native Windows transition once for
            # configurations written before the switch became a real preference.
            if migrate_wallpaper_transition_policy(data):
                converted = True

            # Establish one typed boundary before repositories, path helpers,
            # services, or Qt widgets consume externally edited JSON values.
            data, normalized_changed = normalize_runtime_config(data, defaults=default)
            converted = converted or normalized_changed

            cleaned_history = HistoryRepository.normalize_items(
                data.get("history", []), keep_missing=True
            )
            if cleaned_history != data.get("history", []):
                data["history"] = cleaned_history
                converted = True
            cleaned_favorites = FavoritesRepository.normalize_items(
                data.get("favorites", []), keep_missing=True
            )
            if cleaned_favorites != data.get("favorites", []):
                data["favorites"] = cleaned_favorites
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
            if "wallpaper_transition_enabled" not in data:
                data["wallpaper_transition_enabled"] = True
                converted = True
            data["transition_effect"] = "system" if data.get("wallpaper_transition_enabled") else "none"
            data["transition_direction"] = "right"
            data["transition_duration_ms"] = 300 if data.get("wallpaper_transition_enabled") else 0

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
            return normalize_runtime_config(default, defaults=default)[0]
    return normalize_runtime_config(default, defaults=default)[0]


def flush_pending_config_migration() -> bool:
    """Persist a deferred load_config() migration after the GUI has shown."""
    global CONFIG_MIGRATION_PENDING
    if not (CONFIG_MIGRATION_PENDING or bool(config.get("__config_migration_pending__", False))):
        return False
    if not save_config():
        return False
    CONFIG_MIGRATION_PENDING = False
    return True


def save_config() -> bool:
    """Normalize and durably persist the configuration.

    The previous valid document is retained as ``settings.json.bak`` and
    byte-identical no-op writes are skipped to reduce UI-thread disk churn.
    """
    with _config_lock:
        try:
            config.pop("user_id", None)
            config.pop("__config_migration_pending__", None)
            # Keep the global dictionary identity stable because repositories and
            # services retain references to it, while repairing hostile values.
            normalize_runtime_config_in_place(config, defaults=get_default_config())
            if "tray_click_action" not in config:
                config["tray_click_action"] = "next"
            if "tray_menu_items" not in config:
                config["tray_menu_items"] = ["show", "previous", "next", "random", "bing", "jump", "about", "exit"]
            if "log_enabled" not in config:
                config["log_enabled"] = False
            if "log_file_path" not in config:
                config["log_file_path"] = ""
            if "app_theme" not in config:
                config["app_theme"] = "default"
            for _legacy_transition_key in ("transition_frames", "transition_preview", "transition_animation"):
                config.pop(_legacy_transition_key, None)
            config.setdefault("wallpaper_transition_enabled", True)
            config["wallpaper_transition_policy_version"] = 1
            config["transition_effect"] = "system" if config.get("wallpaper_transition_enabled") else "none"
            config["transition_direction"] = "right"
            config["transition_duration_ms"] = 300 if config.get("wallpaper_transition_enabled") else 0
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
            # v1.4.7: 不再 pop font_size (现在是新功能的合法 key, 0=跟随系统).
            config["ctx_jump_to_wallpaper"] = bool(config.get("ctx_jump_to_wallpaper", config.get("ctx_global_settings", False)))
            for _stale_ctx_key in ("ctx_personalize", "ctx_global_settings", "ctx_set_wallpaper"):
                config.pop(_stale_ctx_key, None)
            for _key, _default in {"hotkey_previous": "Ctrl+Alt+U", "hotkey_next": "Ctrl+Alt+N", "hotkey_random": "Ctrl+Alt+R", "hotkey_jump": "Ctrl+Alt+J"}.items():
                config.setdefault(_key, _default)
            config["global_hotkeys_enabled"] = bool(config.get("global_hotkeys_enabled", False))
            config["app_shortcuts_enabled"] = bool(config.get("app_shortcuts_enabled", True))
            # v1.4.6: 规范化 performance_level, 同步旧 performance_mode 布尔
            _pl = str(config.get("performance_level", "balanced")).lower()
            if _pl not in ("power_saver", "balanced", "performance"):
                _pl = "balanced"
            config["performance_level"] = _pl
            # 保持旧 performance_mode 布尔同步 (向后兼容老代码读取)
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
                "html_frame_rate": 30,
            }.items():
                config.setdefault(_key, _default)
            config.pop("html_compatibility_mode", None)
            config.pop("html_mouse_through", None)
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
            config["history"] = HistoryRepository.normalize_items(
                config.get("history", []), keep_missing=True
            )
            config["favorites"] = FavoritesRepository.normalize_items(
                config.get("favorites", []), keep_missing=True
            )
            if config.get("current_wallpaper"):
                config["current_wallpaper"] = _normalize_wallpaper_path(config.get("current_wallpaper", ""))
            if config.get("slideshow_last_wallpaper"):
                config["slideshow_last_wallpaper"] = _normalize_wallpaper_path(config.get("slideshow_last_wallpaper", ""))
            config["mode"] = normalize_mode_key(config.get("mode", "幻灯片放映"))
            config["fit_mode"] = normalize_style_key(config.get("fit_mode", "填充"))
            changed = CONFIG_REPOSITORY.save(config)
            if changed:
                log("配置已保存")
            return True
        except Exception as e:
            log("保存配置失败: " + str(e))
            return False


# 配置文件写入线程锁，避免多线程并发写入导致数据损坏
_config_lock = threading.RLock()
_TRANSITION_TEMP_DIR = os.path.join(DATA_DIR, "transition_frames")

config = get_default_config()
WALLPAPER_LIBRARY = WallpaperLibrary(lambda: config, persist=save_config, lock=_config_lock)
class _ServiceRegistry:
    services: ApplicationServices | None = None
    runtime_state: RuntimeState | None = None
    initialized = False


_SERVICE_REGISTRY = _ServiceRegistry()
_SERVICE_REGISTRY_LOCK = threading.RLock()


def _set_last_operation_error(message: str) -> None:
    global last_operation_error
    last_operation_error = str(message or "")


def _service_slideshow_anchor(path: str, cfg) -> str | None:
    if normalize_mode_key(cfg.get("mode")) != "幻灯片放映":
        return None
    images = RUNTIME_STATE.slideshow.snapshot().images
    matched = _find_wallpaper_in_slideshow_images(path, images) if images else ""
    if matched:
        return matched
    folder = _normalize_wallpaper_path(cfg.get("slide_folder", ""))
    try:
        if folder and os.path.commonpath([os.path.abspath(folder), os.path.abspath(path)]) == os.path.abspath(folder):
            return path
    except Exception:
        return None
    return None


def _build_application_services() -> ApplicationServices:
    wallpaper_backend = CallbackWallpaperBackend(
        get_current=lambda: get_current_wallpaper_platform(),
        configure_fit_mode=lambda mode: configure_fit_mode(mode, winreg, log),
        set_wallpaper=(
            (lambda path: set_wallpaper_platform(path, bool(config.get("wallpaper_transition_enabled", True))))
            if IS_WINDOWS
            else (lambda path: set_wallpaper_platform(path))
        ),
    )
    media_backend = ProviderMediaBackend(
        lambda: video_wallpaper,
        lambda: html_wallpaper,
    )
    if hotkey_backend_module is None:
        hotkey_backend = CallbackHotkeyBackend(
            lambda _bindings, _dispatch: False,
            lambda: None,
        )
        focus_guard = None
    else:
        hotkey_backend = CallbackHotkeyBackend(
            hotkey_backend_module.refresh,
            hotkey_backend_module.stop,
        )
        focus_guard = getattr(
            hotkey_backend_module,
            "focus_block_reason",
            lambda _action, _binding: "",
        )
    return build_services(
        wallpaper_backend=wallpaper_backend,
        media_backend=media_backend,
        hotkey_backend=hotkey_backend,
        config=lambda: config,
        persist=save_config,
        library=WALLPAPER_LIBRARY,
        runtime_state=RUNTIME_STATE,
        image_source=lambda folder: random_copy.get_original_image_paths(folder),
        apply_wallpaper=lambda path, operation: set_wallpaper(path, operation),
        hotkey_dispatch=_dispatch_global_hotkey_action,
        hotkey_focus_guard=focus_guard,
        session_primary_file=lambda: SESSION_WALLPAPER_FILE,
        session_legacy_files=lambda: (LEGACY_SESSION_WALLPAPER_FILE,),
        platform_name=_platform_name,
        app_base_dir=lambda: BASE_DIR,
        session_get_style=get_windows_wallpaper_style,
        session_restore_style=restore_windows_wallpaper_style,
        refresh_shell=refresh_shell_ui,
        mode_order=tuple(MODE_KEYS) + ("HTML",),
        apply_solid=apply_solid,
        apply_gradient=apply_gradient,
        weighted_choice=lambda folder, current: random_copy.weighted_choice(folder, current),
        slideshow_update=_service_slideshow_anchor,
        preview=lambda path: _queue_ui_preview_update(path),
        is_cancelled=is_operation_cancelled,
        normalize_mode=normalize_mode_key,
        normalize_fit_mode=normalize_style_key,
        log=log,
        set_error=_set_last_operation_error,
        cancel_timer=lambda timer: _cancel_slideshow_timer(timer),
        timer_factory=lambda delay, callback, args: threading.Timer(
            delay, callback, args=args
        ),
        request_cancel=lambda: request_cancel_operations(t("程序退出")),
        release_single_instance=release_single_instance_mutex,
    )


def _get_application_services() -> ApplicationServices:
    with _SERVICE_REGISTRY_LOCK:
        registry = _SERVICE_REGISTRY
        if registry.services is None or registry.runtime_state is not RUNTIME_STATE:
            registry.services = _build_application_services()
            registry.runtime_state = RUNTIME_STATE
        return registry.services


def reset_application_services() -> None:
    """Drop assembled services so tests/bootstrap changes are resolved lazily."""
    with _SERVICE_REGISTRY_LOCK:
        _SERVICE_REGISTRY.services = None
        _SERVICE_REGISTRY.runtime_state = None


def initialize_application(*, load_user_config: bool = True, force: bool = False):
    """Explicitly initialize storage, configuration and application services once."""
    global config
    with _SERVICE_REGISTRY_LOCK:
        if _SERVICE_REGISTRY.initialized and not force:
            return config
        try:
            random_copy.configure_storage(DATA_DIR)
        except Exception as exc:
            log(f"随机概率配置目录初始化失败: {exc}")
        if load_user_config:
            config = load_config()
        reset_application_services()
        _get_application_services()
        _SERVICE_REGISTRY.initialized = True
        # v1.4.4: Prime Explorer's desktop host on Windows so the first
        # static wallpaper change has a transition animation. This replicates
        # the side effect that HTML mode used to provide (0x052C to Progman).
        # Called once at init, not per wallpaper change, to avoid interfering
        # with IDesktopWallpaper's own transition policy.
        if IS_WINDOWS:
            try:
                from platform_adapters.backends.windows.integration import _prime_explorer_wallpaper_host
                _prime_explorer_wallpaper_host()
            except Exception as exc:
                log(f"Explorer desktop host prime failed: {exc}")
        return config


def configure_exit_runtime(*, close_ipc=None, release_single_instance=None) -> None:
    """Attach resources created after application service bootstrap."""
    _get_application_services().exit.configure_runtime_resources(
        close_ipc=close_ipc,
        release_single_instance=release_single_instance,
    )


def perform_exit_cleanup(
    *,
    reason: str = "application_exit",
    restore_wallpaper: bool = True,
    restarting: bool = False,
):
    """Run the idempotent ordered shutdown transaction."""
    return _get_application_services().exit.run(
        reason=reason,
        restore_wallpaper=restore_wallpaper,
        restarting=restarting,
    )


def report_usage(): return None




def request_cancel_operations(reason: str = ""):
    """Request cancellation for queued/long wallpaper work.

    A system API call that has already entered the OS cannot always be interrupted,
    but this stops future slideshow ticks and skips pending wallpaper changes.
    """
    RUNTIME_STATE.cancellation.request(reason)
    if reason:
        log("请求终止操作: " + str(reason))
    else:
        log("请求终止操作")


def clear_cancel_operations():
    RUNTIME_STATE.cancellation.clear()


def is_operation_cancelled() -> bool:
    return RUNTIME_STATE.cancellation.is_requested()


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
        target = os.path.abspath(os.path.expanduser(command.split("|", 1)[1]))
        if not os.path.isfile(target):
            raise RuntimeError(f"壁纸文件不存在: {target}")
        log(f"跨进程请求切换到图片模式: {target}")
        if not switch_wallpaper_mode("图片", updates={"single_image": target}):
            reason = last_operation_error or "切换到图片模式失败"
            raise RuntimeError(reason)
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

    v1.4.5: When the command fails (e.g. no previous/next wallpaper), show a tray
    notification so the user gets feedback instead of silent failure.
    """
    generation = RUNTIME_STATE.ipc_commands.submit(command)
    if generation is None:
        if command:
            log("壁纸切换繁忙，已合并最新请求")
        return

    def _worker(first_command: str, worker_generation: int):
        current = first_command
        while current:
            try:
                _execute_ipc_wallpaper_command(current)
            except Exception as exc:
                log(f"IPC 壁纸命令执行失败: {exc}")
                # v1.4.5: Show tray notification for user-visible failures
                # (e.g. "没有上一张壁纸", "没有下一张壁纸")
                _notify_ipc_failure(current, str(exc))
            current = RUNTIME_STATE.ipc_commands.next_or_finish(worker_generation)

    worker = threading.Thread(
        target=_worker,
        args=(command, generation),
        daemon=True,
        name="ShangBackgroundIpcWallpaper",
    )
    try:
        worker.start()
    except Exception:
        # Do not leave the queue permanently busy when thread creation fails.
        RUNTIME_STATE.ipc_commands.abort_worker(generation)
        raise


def _notify_ipc_failure(command: str, error: str) -> None:
    """Show a tray notification when an IPC wallpaper command fails.

    This runs on the IPC worker thread, so it must not touch Qt widgets
    directly. Use the root shim's after() to schedule on the GUI thread.
    """
    try:
        msg = str(error)
        # Only show notification for user-meaningful errors, not internal failures
        if any(keyword in msg for keyword in ("没有", "无", "不存在", "未找到", "empty", "not found")):
            if root is not None and hasattr(root, "after"):
                root.after(0, lambda: _show_tray_notification(msg))
            else:
                log(f"IPC 失败（无 GUI 通知通道）: {msg}")
    except Exception:
        pass


def _show_tray_notification(message: str) -> None:
    """Show a tray balloon notification (called on GUI thread)."""
    try:
        from PySide6.QtWidgets import QSystemTrayIcon
        tray = globals().get("tray_icon_obj", None)
        if tray is not None and isinstance(tray, QSystemTrayIcon):
            tray.showMessage(
                APP_NAME,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        # Also update the status bar
        if root is not None and hasattr(root, "set_status"):
            root.set_status(message)
    except Exception:
        pass


def log_time_diff(operation_name, new_wallpaper):
    current_time = time.time() * 1000
    time_diff = RUNTIME_STATE.operation_clock.record(current_time)
    if time_diff is not None:
        log(f"[时间差] {operation_name} 切换到 {os.path.basename(new_wallpaper)}，距离上次切换 {time_diff:.2f} ms")
    else:
        log(f"[时间差] {operation_name} 首次切换到 {os.path.basename(new_wallpaper)}")


current_preview_image = None
overlay_image = None
_WALLPAPER_QUERY_ERROR_LOG_INTERVAL = 20.0
# Bug 7 fix: 30-second cache for get_current_wallpaper() so the GUI preview
# polling timer doesn't re-query the system on every tick.  On Windows the
# SystemParametersInfo call is fast (~1ms) so the cache is mostly a no-op,
# but on Linux/KDE it saves a qdbus6 subprocess per tick.  Kept on Windows
# for API consistency across the three platforms.
_CACHED_CURRENT_WALLPAPER: str = ""
_CACHED_CURRENT_WALLPAPER_AT: float = 0.0
_CURRENT_WALLPAPER_CACHE_TTL = 30.0  # seconds


def _invalidate_current_wallpaper_cache() -> None:
    _get_application_services().wallpaper.invalidate_current_cache()



def get_current_wallpaper(*, use_cache: bool = True):
    """Compatibility facade for the WallpaperService current-state query."""
    return _get_application_services().wallpaper.get_current(use_cache=use_cache)



list_wallpaper_history = WALLPAPER_LIBRARY.list_history
wallpaper_history_count = WALLPAPER_LIBRARY.history_count
remember_wallpaper = WALLPAPER_LIBRARY.remember_wallpaper
clear_wallpaper_history = WALLPAPER_LIBRARY.clear_history
list_favorites = WALLPAPER_LIBRARY.list_favorites
is_favorite = WALLPAPER_LIBRARY.is_favorite
add_favorite = WALLPAPER_LIBRARY.add_favorite
remove_favorite = WALLPAPER_LIBRARY.remove_favorite
toggle_favorite = WALLPAPER_LIBRARY.toggle_favorite
clear_favorites = WALLPAPER_LIBRARY.clear_favorites


def push_wallpaper(path, *, update_current: bool = True, refresh_preview: bool = True):
    """Compatibility facade for recording one wallpaper and refreshing the UI."""
    try:
        changed = _get_application_services().wallpaper.record(
            path,
            update_current=update_current,
            refresh_preview=refresh_preview,
        )
    except CollectionPersistenceError as exc:
        _set_last_operation_error(str(exc))
        raise
    if changed:
        normalized = _normalize_wallpaper_path(path)
        log(
            "已记录壁纸: "
            + os.path.basename(normalized)
            + " | 历史总数: "
            + str(wallpaper_history_count())
        )
    return changed



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
    """Return a path-key index owned by the slideshow runtime state."""
    return RUNTIME_STATE.slideshow.index_map(
        _history_key,
        _normalize_wallpaper_path,
        images,
    )


def _invalidate_slideshow_index_cache() -> None:
    RUNTIME_STATE.slideshow.invalidate_index()


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


@_serialized_wallpaper_operation
def switch_wallpaper_mode(target: str | None = "next", *, updates=None) -> bool:
    """Compatibility facade for WallpaperModeService."""
    _set_last_operation_error("")
    try:
        return _get_application_services().modes.switch(target, updates=updates)
    except WallpaperModeError as exc:
        _set_last_operation_error(str(exc))
        log_error("switch wallpaper mode failed", exc)
        return False


@_serialized_wallpaper_operation
def set_wallpaper_direct(
    path,
    operation_name="系统",
    skip_history=False,
    previous_path: str | None = None,
    progress_cb=None,
):
    """Compatibility facade for WallpaperService.apply()."""
    success = _get_application_services().wallpaper.apply(
        path,
        operation_name,
        record_history=not bool(skip_history),
        previous_path=previous_path,
        progress=progress_cb,
    )
    if success:
        log_time_diff(operation_name, path)
    return success



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
    return _get_application_services().session.capture(
        inherit_existing=inherit_existing,
        force_refresh=force_refresh,
    )


@_serialized_wallpaper_operation
def restore_session_original_wallpaper(stop_video: bool = True):
    # stop_video is kept for API compatibility; the service now stops both
    # video and HTML so exit restoration cannot leave either backend running.
    return _get_application_services().session.restore(stop_dynamic=bool(stop_video))


@_serialized_wallpaper_operation
def set_wallpaper(path, operation_name="用户", progress_cb=None):
    """Apply and record a wallpaper through WallpaperService."""
    previous_path = config.get("current_wallpaper") or get_current_wallpaper()
    success = _get_application_services().wallpaper.apply(
        path,
        operation_name,
        record_history=True,
        previous_path=previous_path,
        progress=progress_cb,
    )
    if success:
        log_time_diff(operation_name, path)
    return success


@_serialized_wallpaper_operation
def apply_browsed_wallpaper(path, operation_name="浏览壁纸") -> bool:
    """Apply a wallpaper selected from the library/sidebar without splitting mode state.

    In slideshow mode a browse selection is a seek within the active slideshow,
    so keep the mode and renew its timer.  In every other mode the selected
    image is a real mode transition to ``图片`` so video/HTML/color state cannot
    remain persisted behind a static desktop.
    """
    _set_last_operation_error("")
    normalized = _normalize_wallpaper_path(path or "")
    if not normalized or not os.path.isfile(normalized):
        _set_last_operation_error("壁纸文件不存在: " + normalized)
        return False
    if normalize_mode_key(config.get("mode")) == "幻灯片放映":
        success = bool(set_wallpaper(normalized, operation_name))
        if success:
            try:
                reset_slide_timer()
            except Exception as exc:
                log_error("重置幻灯片计时器失败", exc)
        return success
    return bool(
        switch_wallpaper_mode(
            "图片", updates={"single_image": normalized}
        )
    )



def _require_wallpaper_action(action: str) -> None:
    availability = wallpaper_action_availability(config.get("mode"), action)
    if availability.allowed:
        return
    message = (
        t("请在 HTML 模式下使用此功能")
        if availability.reason == "requires_html"
        else t("请在幻灯片放映模式下使用此功能")
    )
    _set_last_operation_error(message)
    raise RuntimeError(message)


@_serialized_wallpaper_operation
def previous_wallpaper():
    """切换到上一张幻灯片历史项。"""
    global last_operation_error
    _require_wallpaper_action("previous")
    hist = list_wallpaper_history(existing_only=True)
    log("当前历史: " + str([os.path.basename(p) for p in hist[:5]]) + ("..." if len(hist) > 5 else ""))
    # The GUI recent list is newest-first; navigation must not reorder it.
    actual_current = _get_application_services().wallpaper.get_current(use_cache=False)
    current_anchor = actual_current or config.get("current_wallpaper", "")
    found = previous_history_item(hist, current_anchor)
    if found is None:
        log("没有上一张壁纸")
        # 不能在 worker 线程弹 QMessageBox（会导致 GUI 未响应）。
        # 改为设置 last_operation_error + 抛 RuntimeError，由 GUI 线程的
        # _on_core_finished 统一显示。
        last_operation_error = t("没有上一张壁纸")
        raise RuntimeError(last_operation_error)
    log("回退到: " + os.path.basename(found))
    # Preserve the GUI recent-list order while moving its current marker.
    # This is deliberately not set_wallpaper(), which would move ``found`` to
    # the MRU front and make the next Previous action bounce back.
    previous_path = actual_current or config.get("current_wallpaper") or get_current_wallpaper()
    success = _get_application_services().wallpaper.apply(
        found,
        "右键菜单(上一张)",
        record_history=False,
        previous_path=previous_path,
    )
    if success and normalize_mode_key(config.get("mode")) == "幻灯片放映":
        try:
            reset_slide_timer()
        except Exception:
            pass
    log("=" * 50)
    return success


@_serialized_wallpaper_operation
def next_wallpaper():
    """Move toward the newest recent wallpaper, then continue the slideshow.

    The recent list is newest-first.  After a Previous action the current
    wallpaper may sit behind the latest entry; Next must first walk that same
    history forward without moving entries in the MRU list.  Only after the
    latest entry is reached do we advance through the slideshow folder.
    """
    global last_operation_error
    _require_wallpaper_action("next")

    history = list_wallpaper_history(existing_only=True)
    actual_current = _get_application_services().wallpaper.get_current(use_cache=False)
    current = actual_current or config.get("current_wallpaper", "")
    newer = newer_history_item(history, current)
    if newer is not None:
        log("沿最近使用历史前进到: " + os.path.basename(newer))
        previous_path = current or get_current_wallpaper()
        success = _get_application_services().wallpaper.apply(
            newer,
            "右键菜单(下一张-历史)",
            record_history=False,
            previous_path=previous_path,
        )
        if success:
            try:
                reset_slide_timer()
            except Exception:
                pass
        log("=" * 50)
        return success

    images = RUNTIME_STATE.slideshow.snapshot().images
    if not images:
        folder = config["slide_folder"]
        if folder and os.path.isdir(folder):
            try:
                discovered = random_copy.get_original_image_paths(folder)
            except Exception:
                discovered = []
            if discovered:
                images_list = [_normalize_wallpaper_path(path) for path in discovered]
                if config.get("shuffle"):
                    random.shuffle(images_list)
                images = RUNTIME_STATE.slideshow.replace_images(images_list)
                log(f"重新加载 {len(images)} 张图片")
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
    return success


@_serialized_wallpaper_operation
def random_wallpaper():
    """从当前幻灯片文件夹中随机选择壁纸。"""
    global last_operation_error
    _require_wallpaper_action("random")

    folder = config["slide_folder"]
    if not folder or not os.path.isdir(folder):
        log("幻灯片文件夹无效")
        last_operation_error = t("请先设置幻灯片文件夹")
        raise RuntimeError(last_operation_error)

    images = tuple(
        _normalize_wallpaper_path(path)
        for path in random_copy.get_original_image_paths(folder)
    )
    if not images:
        log("文件夹中没有图片")
        last_operation_error = t("文件夹中没有图片")
        raise RuntimeError(last_operation_error)

    current = config.get("current_wallpaper", "")
    random_img = random_copy.weighted_choice(folder, current)
    if not random_img:
        random_img = random.choice(images)
    log("随机切换到: " + os.path.basename(random_img))
    success = set_wallpaper(random_img, "右键菜单(随机)")
    if success:
        RUNTIME_STATE.slideshow.replace_images(images)
        try:
            reset_slide_timer()
        except Exception:
            pass
    log("=" * 50)
    return success


@_serialized_wallpaper_operation
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
            # 当前壁纸已不存在时，回退到 history 中最近一张仍存在的壁纸。
            # 历史按“最近优先”存储，旧实现 reversed(history) 实际会选最旧项。
            history = list_wallpaper_history(existing_only=True)
            if history:
                candidate = history[0]
                set_wallpaper_direct(candidate, "适应模式")
                applied_path = candidate
            if applied_path is None:
                log("适应模式: 当前壁纸已不存在且历史记录中无可回退项，未重新应用")
        if applied_path is not None:
            log("适应模式: " + mode + " (reapplied: " + str(applied_path) + ")")
        else:
            log("适应模式: " + mode)
    except Exception as e:
        log("设置适应模式失败: " + str(e))


def get_next_wallpaper(images: tuple[str, ...] | list[str] | None = None):
    candidates = (
        tuple(images)
        if images is not None
        else RUNTIME_STATE.slideshow.snapshot().images
    )
    return _get_application_services().slideshow.next_image(candidates, config)



def _cancel_slideshow_timer(timer) -> None:
    if timer is None:
        return
    try:
        if root is not None and isinstance(timer, str):
            root.after_cancel(timer)
        else:
            timer.cancel()
    except Exception as exc:
        log_error("取消幻灯片定时器失败", exc)


def _schedule_slide_timer(generation: int) -> bool:
    """Compatibility hook around SlideshowService timer scheduling."""
    return _get_application_services().slideshow._schedule(generation, config)



@_serialized_wallpaper_operation
def slide_next(generation: int | None = None):
    return _get_application_services().slideshow.advance(generation)



def reset_slide_timer():
    return _get_application_services().slideshow.reset()



@_serialized_wallpaper_operation
def start_slideshow(is_startup: bool = False):
    return _get_application_services().slideshow.start(is_startup=is_startup)



@_serialized_wallpaper_operation
def stop_slideshow():
    return _get_application_services().slideshow.stop()



@_serialized_wallpaper_operation
def restart_slideshow():
    return _get_application_services().slideshow.restart()


@_serialized_wallpaper_operation
def restore_configured_wallpaper_mode(expected_mode: str, *, is_startup: bool = False) -> bool:
    """Restore a delayed startup mode only if it is still the committed mode.

    Startup restoration is intentionally delayed until after the first frame.
    Holding the shared wallpaper-operation lock across the mode check and the
    backend start prevents a stale QTimer callback from overwriting a newer GUI
    or IPC mode change that happened during that delay.
    """
    expected = normalize_mode_key(expected_mode)
    current = normalize_mode_key(config.get("mode"))
    if current != expected:
        log(f"跳过过期的启动模式恢复: expected={expected}, current={current}")
        return True
    if expected == "幻灯片放映":
        return bool(start_slideshow(is_startup=is_startup))
    if expected == "视频":
        return bool(start_video_wallpaper(config.get("video_file")))
    if expected == "HTML":
        return bool(
            start_html_wallpaper(
                config.get("html_file", "") or config.get("html_url", "")
            )
        )
    return True


@_serialized_wallpaper_operation
def start_video_wallpaper(path: str | None = None):
    try:
        return _get_application_services().media.start_video(path)
    except MediaServiceError as exc:
        _set_last_operation_error(str(exc))
        log("视频壁纸启动失败: " + str(exc))
        raise RuntimeError(str(exc)) from exc



@_serialized_wallpaper_operation
def stop_video_wallpaper():
    """Stop every dynamic wallpaper through MediaService."""
    try:
        return _get_application_services().media.stop_all()
    except MediaServiceError as exc:
        log_error("停止动态壁纸失败", exc)
        return False



def is_video_wallpaper_running():
    return _get_application_services().media.is_running("video")



def set_video_paused(paused: bool) -> bool:
    return _get_application_services().media.set_option("video", "paused", bool(paused))


def set_video_volume(muted: bool, volume: int) -> bool:
    return _get_application_services().media.set_option(
        "video", "volume", (bool(muted), int(volume))
    )



# ====================== HTML 壁纸控制 ===========================

def _sync_html_wallpaper_runtime_options_from_config() -> None:
    service = _get_application_services().media
    service.set_option("html", "auto_pause", bool(config.get("html_auto_pause", True)))
    service.set_option("html", "frame_rate", int(config.get("html_frame_rate", 30)))



@_serialized_wallpaper_operation
def start_html_wallpaper(path: str | None = None):
    try:
        return _get_application_services().media.start_html(path)
    except MediaServiceError as exc:
        _set_last_operation_error(str(exc))
        log("HTML 壁纸启动失败: " + str(exc))
        raise RuntimeError(str(exc)) from exc



@_serialized_wallpaper_operation
def stop_html_wallpaper() -> bool:
    try:
        _get_application_services().media.stop("html")
        return True
    except MediaServiceError as exc:
        log_error("停止 HTML 壁纸失败", exc)
        return False



def is_html_wallpaper_running() -> bool:
    return _get_application_services().media.is_running("html")



def html_wallpaper_runtime_set_option(key: str, value) -> bool:
    return _get_application_services().media.set_option("html", key, value)



def html_wallpaper_get_last_path() -> str:
    return _get_application_services().media.last_target("html")



def restart_html_wallpaper(path: str | None = None) -> bool:
    try:
        return _get_application_services().media.restart_html(path)
    except MediaServiceError as exc:
        log_error("重启 HTML 壁纸失败", exc)
        return False



# ====================== 全局热键兼容门面 ======================

def _dispatch_global_hotkey_action(action: str):
    """Dispatch one registered action without blocking the backend listener."""
    action = str(action or "").strip().lower()
    log(f"全局热键触发: {action}")

    def _run_wallpaper_action(fn, action_name: str):
        try:
            # Wallpaper/media/mode services commit their own transaction.  A
            # second unconditional save here caused duplicate disk writes and
            # could mask the original service result.
            fn()
        except Exception as exc:
            log_error(f"全局热键动作执行失败({action_name})", exc)

    action_map = {
        "previous": previous_wallpaper,
        "next": next_wallpaper,
        "random": random_wallpaper,
        "mode": lambda: switch_wallpaper_mode("next"),
    }
    fn = action_map.get(action)
    if fn is not None:
        threading.Thread(
            target=_run_wallpaper_action,
            args=(fn, action),
            daemon=True,
            name=f"ShangBackgroundHotkeyAction-{action}",
        ).start()
        return
    if action == "jump":
        if root is not None and hasattr(root, "after"):
            root.after(0, _gui_open_wallpaper_sidebar)
        else:
            log("无法触发跳转到壁纸：GUI 未就绪")
        return
    log(f"未知的全局热键动作: {action}")


def _gui_open_wallpaper_sidebar():
    try:
        if root is not None and hasattr(root, "window") and hasattr(root.window, "open_wallpaper_sidebar"):
            root.window.open_wallpaper_sidebar()
    except Exception as exc:
        log(f"打开壁纸侧栏失败: {exc}")


def _gui_switch_wallpaper_mode(target: str | None = "next") -> bool:
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


def refresh_global_hotkeys():
    if hotkey_backend_module is not None and not IS_WINDOWS and _pynput_keyboard is not None:
        try:
            hotkey_backend_module._KEYBOARD_OVERRIDE = _pynput_keyboard
        except Exception:
            pass
    return _get_application_services().hotkeys.refresh()


def stop_global_hotkeys():
    return _get_application_services().hotkeys.stop()


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
        bmp_path = os.path.join(diy_dir, "gradient_wallpaper.bmp")
        img.save(bmp_path)
        log("渐变壁纸生成完成 (Pillow/Qt 无 NumPy 引擎)")
        return bmp_path
    except Exception as e:
        log("创建渐变壁纸失败: " + str(e))
        return None


def create_gradient_wallpaper(color1, color2, angle=0):
    return create_gradient_wallpaper_optimized(color1, color2, angle)


def apply_gradient():
    color1 = config.get("solid_color", DEFAULT_SOLID_COLOR)
    color2 = config.get("gradient_color2", DEFAULT_GRADIENT_COLOR2)
    angle = config.get("gradient_angle", 0)
    bmp_path = create_gradient_wallpaper(color1, color2, angle)
    if bmp_path and os.path.exists(bmp_path):
        return bool(set_wallpaper(bmp_path, "渐变壁纸"))
    return False


def apply_solid():
    if Image is None:
        log("Pillow 不可用，无法生成纯色壁纸")
        return False
    try:
        color = config.get("solid_color", DEFAULT_SOLID_COLOR)
        screen_width, screen_height = get_screen_size(root)
        screen_width = int(screen_width)
        screen_height = int(screen_height)
        if screen_width <= 0 or screen_height <= 0:
            raise ValueError(f"invalid screen size: {screen_width}x{screen_height}")
        img = Image.new("RGB", (screen_width, screen_height), color)
        diy_dir = os.path.join(DATA_DIR, "diy")
        os.makedirs(diy_dir, exist_ok=True)
        bmp_path = os.path.join(diy_dir, "solid_wallpaper.bmp")
        img.save(bmp_path)
        if os.path.exists(bmp_path):
            return bool(set_wallpaper(bmp_path, "纯色壁纸"))
    except Exception as exc:
        log(f"创建纯色壁纸失败: {exc}")
    return False


def update_preview(_img_path): return None


def show_main_window_now():
    """显示主窗口并尽力绕过 Windows 前台窗口限制。"""
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


def _context_command_parts(*args):
    """Return the command parts used by Windows desktop context-menu registry entries."""
    if is_frozen():
        return [app_executable_path(), *args]
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    interpreter = pythonw if os.path.exists(pythonw) else sys.executable
    return [interpreter, os.path.join(BASE_DIR, "main.py"), *args]


def _context_command_target_error() -> str:
    parts = _context_command_parts()
    if not parts or not os.path.exists(parts[0]):
        return f"右键菜单命令目标不存在: {parts[0] if parts else '<empty>'}"
    if not is_frozen():
        script = parts[1] if len(parts) > 1 else ""
        if not script or not os.path.isfile(script):
            return f"右键菜单源码入口不存在: {script}"
    return ""


def _build_context_action_command(*args):
    """Build a desktop-context command that launches without a visible window.

    v1.4.4: Previous implementation used ``cmd.exe /c start "" /b`` to detach
    the process from Explorer's shell invocation. This caused two problems:
    1. A cmd.exe window flashed briefly on every right-click action
    2. cmd.exe added ~200ms overhead on top of the already-slow cold start

    The fix: register the executable directly without cmd.exe wrapper. In
    packaged (frozen) builds, the Nuitka standalone executable already has
    ``--windows-console-mode=disable`` which prevents any console window.
    In source mode, pythonw.exe is used (no console).

    The registered command intentionally stays direct and quote-safe. Latency
    isolation is handled by app.context_menu_fastpath before the GUI stack is
    imported: a live instance receives a bounded WM_COPYDATA accelerator, while
    a cold action is detached from Explorer and continues through authenticated
    local IPC / normal single-instance startup.
    """
    parts = [str(part) for part in _context_command_parts(*args)]
    return subprocess.list2cmdline(parts)


def _desired_context_menu_entries():
    """Return desired Windows context-menu registry entries from current config."""
    return (
        {
            "path": r"DesktopBackground\Shell\LastWallpaper",
            "enabled": bool(config.get("ctx_last_wallpaper", False)),
            "label": "上一个桌面背景",
            "command": _build_context_action_command("--from-context-menu", "--previous"),
        },
        {
            "path": r"DesktopBackground\Shell\NextWallpaper",
            "enabled": bool(config.get("ctx_next_wallpaper", False)),
            "label": "下一个桌面背景",
            "command": _build_context_action_command("--from-context-menu", "--next"),
        },
        {
            "path": r"DesktopBackground\Shell\RandomWallpaper",
            "enabled": bool(config.get("ctx_random_wallpaper", False)),
            "label": "随机一个桌面背景",
            "command": _build_context_action_command("--from-context-menu", "--random"),
        },
        {
            "path": r"DesktopBackground\Shell\ZJumpToWallpaper",
            "enabled": bool(config.get("ctx_jump_to_wallpaper", False)),
            "label": "跳转到壁纸",
            "command": _build_context_action_command("--from-context-menu", "--jump-to-wallpaper"),
        },
    )


# v1.4.7: 权限优化 —— 右键菜单注册从 HKEY_CLASSES_ROOT (需 admin) 改为
# HKEY_CURRENT_USER\Software\Classes (per-user, 无需 admin).
# 原理: HKCR 是 HKLM\Software\Classes 和 HKCU\Software\Classes 的合并视图,
# 写入 HKCU\Software\Classes\DesktopBackground\Shell\... 同样能让桌面右键菜单
# 显示该项, 且不需要 UAC 提权. 这大幅改善用户体验和安全性 (不再需要 admin).
_CTX_MENU_REG_ROOT = None  # 延迟初始化


def _ctx_menu_reg_root():
    """返回右键菜单注册表根 (优先 HKCU\\Software\\Classes, 无需 admin)."""
    global _CTX_MENU_REG_ROOT
    if _CTX_MENU_REG_ROOT is not None:
        return _CTX_MENU_REG_ROOT
    if winreg is None:
        return None
    # 确保 HKCU\Software\Classes\DesktopBackground\Shell 存在
    try:
        root = winreg.HKEY_CURRENT_USER
        sub = r"Software\Classes\DesktopBackground\Shell"
        try:
            key = winreg.CreateKey(root, sub)
            winreg.CloseKey(key)
        except Exception:
            pass
        _CTX_MENU_REG_ROOT = root
        return root
    except Exception:
        # 兜底: 退回 HKCR (需 admin)
        _CTX_MENU_REG_ROOT = winreg.HKEY_CLASSES_ROOT
        return _CTX_MENU_REG_ROOT


def _ctx_menu_reg_prefix() -> str:
    """返回 HKCU 下的前缀 (Software\\Classes\\), HKCR 下为空."""
    root = _ctx_menu_reg_root()
    if root == winreg.HKEY_CURRENT_USER:
        return "Software\\Classes\\"
    return ""


def _registry_key_exists(path: str) -> bool:
    if winreg is None:
        return False
    root = _ctx_menu_reg_root()
    full_path = _ctx_menu_reg_prefix() + path
    try:
        key = winreg.OpenKey(root, full_path, 0, winreg.KEY_READ)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        # 也检查 HKCR (兼容旧版 admin 写入的项)
        if root != winreg.HKEY_CLASSES_ROOT:
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, winreg.KEY_READ)
                winreg.CloseKey(key)
                return True
            except (FileNotFoundError, OSError):
                pass
        return False


def _registry_default_value(path: str):
    if winreg is None:
        return None
    root = _ctx_menu_reg_root()
    full_path = _ctx_menu_reg_prefix() + path
    try:
        key = winreg.OpenKey(root, full_path, 0, winreg.KEY_READ)
        try:
            value, _value_type = winreg.QueryValueEx(key, "")
            return value
        finally:
            winreg.CloseKey(key)
    except (FileNotFoundError, OSError):
        # 兼容旧版 HKCR 写入
        if root != winreg.HKEY_CLASSES_ROOT:
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, winreg.KEY_READ)
                try:
                    value, _value_type = winreg.QueryValueEx(key, "")
                    return value
                finally:
                    winreg.CloseKey(key)
            except (FileNotFoundError, OSError):
                pass
        return None


def _delete_registry_tree(root, subkey: str) -> None:
    """Delete a registry key tree if it exists; ignore missing keys."""
    if winreg is None:
        return
    try:
        key = winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except (FileNotFoundError, OSError):
        return
    try:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_registry_tree(root, subkey + "\\" + child)
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
    try:
        winreg.DeleteKey(root, subkey)
    except (FileNotFoundError, OSError):
        pass


def _stale_context_menu_paths() -> tuple[str, ...]:
    """Registry paths from removed/renamed right-click features."""
    return (
        r"DesktopBackground\Shell\JumpToWallpaper",
        r"DesktopBackground\Shell\~~PersonalizeBackground",
        r"SystemFileAssociations\image\shell\ShangBackgroundSetWallpaper",
    )


def _notify_shell_association_changed() -> None:
    """Tell Explorer that per-user shell verbs changed.

    Registry writes are synchronous, but Explorer is allowed to cache shell
    association data.  SHCNE_ASSOCCHANGED is the documented broad invalidation
    signal for this case and avoids making users restart Explorer/log out just
    to see a newly enabled or disabled verb.
    """
    if not IS_WINDOWS:
        return
    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except Exception as exc:
        log(f"刷新 Windows Shell 关联缓存失败: {exc}")


def is_context_menu_synced() -> bool:
    """Return True if the Windows desktop context menu exactly matches config."""
    if not IS_WINDOWS or winreg is None:
        return False
    try:
        for entry in _desired_context_menu_entries():
            path = entry["path"]
            command_path = path + r"\command"
            if entry["enabled"]:
                if _registry_default_value(path) != entry["label"]:
                    return False
                if _registry_default_value(command_path) != entry["command"]:
                    return False
            else:
                if _registry_key_exists(command_path) or _registry_key_exists(path):
                    return False
        for path in _stale_context_menu_paths():
            if _registry_key_exists(path) or _registry_key_exists(path + r"\command"):
                return False
        return True
    except Exception as e:
        log(f"检查右键菜单同步状态失败: {e}")
        return False


def register_context(show_admin_prompt=False):
    """注册或同步 Windows 桌面右键菜单.

    v1.4.7: 改用 HKEY_CURRENT_USER\\Software\\Classes (per-user, 无需 admin).
    之前用 HKEY_CLASSES_ROOT 需要 UAC 提权, 用户体验差且有安全风险.
    HKCR 是 HKLM\\Software\\Classes 和 HKCU\\Software\\Classes 的合并视图,
    写入 HKCU 路径同样能让桌面右键菜单显示该项.

    同时清理旧版在 HKCR (需 admin) 写入的残留项; 如果当前不是 admin,
    HKCR 残留项无法清理, 但不会影响新项的注册 (HKCU 项会覆盖显示).
    """
    global last_operation_error
    if not IS_WINDOWS or winreg is None:
        log("当前平台不支持 Windows 桌面右键菜单注册，已跳过")
        last_operation_error = "当前平台不支持 Windows 桌面右键菜单注册，已跳过"
        return False
    # v1.4.7: 不再强制要求 admin. HKCU\Software\Classes 无需提权.
    # 仅当需要清理 HKCR 旧残留时才提示 admin (可选).
    target_error = _context_command_target_error()
    if target_error:
        log(target_error)
        last_operation_error = target_error
        if show_admin_prompt:
            show_message(t("错误"), target_error)
        return False
    try:
        root = _ctx_menu_reg_root()
        prefix = _ctx_menu_reg_prefix()
        # 清理 HKCU 下的旧残留项 (无需 admin)
        for stale_path in _stale_context_menu_paths():
            _delete_registry_tree(root, prefix + stale_path)
        # 如果当前是 admin, 也清理 HKCR 下的旧残留 (迁移用)
        if is_windows_admin():
            for stale_path in _stale_context_menu_paths():
                try:
                    _delete_registry_tree(winreg.HKEY_CLASSES_ROOT, stale_path)
                except Exception:
                    pass

        for entry in _desired_context_menu_entries():
            path = entry["path"]
            command_path = path + r"\command"
            full_path = prefix + path
            full_cmd_path = prefix + command_path
            if entry["enabled"]:
                key = winreg.CreateKey(root, full_path)
                try:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, entry["label"])
                finally:
                    winreg.CloseKey(key)
                cmd_key = winreg.CreateKey(root, full_cmd_path)
                try:
                    winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, entry["command"])
                finally:
                    winreg.CloseKey(cmd_key)
                log(f"右键菜单已同步: {path}")
            else:
                _delete_registry_tree(root, full_path)
                # 如果是 admin, 也清理 HKCR 旧项
                if is_windows_admin():
                    try:
                        _delete_registry_tree(winreg.HKEY_CLASSES_ROOT, path)
                    except Exception:
                        pass
                log(f"右键菜单已关闭: {path}")

        for stale_key in ("ctx_personalize", "ctx_global_settings", "ctx_set_wallpaper"):
            config.pop(stale_key, None)
        try:
            save_config()
        except Exception:
            pass
        _notify_shell_association_changed()
        last_operation_error = ""
        return True

    except Exception as e:
        log("右键注册失败: " + str(e))
        last_operation_error = "右键注册失败: " + str(e)
        if show_admin_prompt:
            show_message(t("错误"), t("右键菜单注册失败。") + f"\n\n{t('原因')}：{e}")
        return False
