from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager

from app.config import STYLE_MAP, normalize_style_key



_UNICODE_ALIAS_PREFIX = "wallpaper-"
_UNICODE_ALIAS_LIMIT = 24


def _contains_non_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _unicode_alias_dir() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or tempfile.gettempdir()
    path = os.path.join(root, "ShangBackground", "wallpaper-cache")
    os.makedirs(path, exist_ok=True)
    return path


def _prune_unicode_aliases(cache_dir: str) -> None:
    try:
        files = []
        for name in os.listdir(cache_dir):
            if not name.startswith(_UNICODE_ALIAS_PREFIX) or name.endswith(".source.json"):
                continue
            path = os.path.join(cache_dir, name)
            try:
                files.append((os.path.getmtime(path), path))
            except OSError:
                continue
        for _mtime, path in sorted(files, reverse=True)[_UNICODE_ALIAS_LIMIT:]:
            for candidate in (path, path + ".source.json"):
                try:
                    os.remove(candidate)
                except OSError:
                    pass
    except OSError:
        pass


def _wallpaper_api_path(original: str) -> str:
    """Return an ASCII alias for non-ASCII Windows wallpaper paths.

    The Win32 APIs accept Unicode, but real-world Explorer/image-codec stacks
    can repeatedly re-open and canonicalize non-ASCII source names.  A stable
    per-file alias removes that hot-path variability while history/config keep
    the user's original path.  Hard links make the common same-volume case
    metadata-only; copying is a fallback and is reused until size/mtime change.
    """
    original = _ensure_existing_file(original)
    if not _contains_non_ascii(original):
        return original
    try:
        stat = os.stat(original)
        identity = f"{original}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8", errors="surrogatepass")
        digest = hashlib.sha256(identity).hexdigest()[:24]
        suffix = os.path.splitext(original)[1].lower() or ".img"
        cache_dir = _unicode_alias_dir()
        alias = os.path.join(cache_dir, f"{_UNICODE_ALIAS_PREFIX}{digest}{suffix}")
        if not os.path.isfile(alias) or os.path.getsize(alias) != stat.st_size:
            temp_alias = alias + f".{os.getpid()}.tmp"
            try:
                os.remove(temp_alias)
            except OSError:
                pass
            try:
                os.link(original, temp_alias)
            except OSError:
                shutil.copyfile(original, temp_alias)
            os.replace(temp_alias, alias)
        source_file = alias + ".source.json"
        try:
            with open(source_file + ".tmp", "w", encoding="utf-8") as handle:
                json.dump({"source": original}, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(source_file + ".tmp", source_file)
        except OSError:
            pass
        _prune_unicode_aliases(cache_dir)
        return alias
    except OSError:
        return original


def _original_path_for_alias(path: str) -> str:
    try:
        name = os.path.basename(path)
        if not name.startswith(_UNICODE_ALIAS_PREFIX):
            return path
        source_file = path + ".source.json"
        with open(source_file, "r", encoding="utf-8") as handle:
            source = str(json.load(handle).get("source") or "")
        return source if source and os.path.isfile(source) else path
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return path

SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002


# --- IDesktopWallpaper COM interface (Windows 8+) ---------------------------
# Static wallpaper writes use the documented IDesktopWallpaper API.  The
# interface is invoked directly with ctypes instead of comtypes because this
# application executes every mode transition on a short-lived worker thread.
# COM must be initialized on *each* calling thread; relying on whichever thread
# imported comtypes first made the first transition depend on startup order.
# ---------------------------------------------------------------------------

_DESKTOP_WALLPAPER_IID = "{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}"
_DESKTOP_WALLPAPER_CLSID = "{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}"

# DWPOS_* — IDesktopWallpaper::SetPosition
DWPOS_CENTER = 0
DWPOS_TILE = 1
DWPOS_STRETCH = 2
DWPOS_FIT = 3
DWPOS_FILL = 4
DWPOS_SPAN = 5

# COM / Shell constants.
_CLSCTX_INPROC_SERVER = 0x1
_CLSCTX_LOCAL_SERVER = 0x4
_CLSCTX_SERVER = _CLSCTX_INPROC_SERVER | _CLSCTX_LOCAL_SERVER
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
_PROGMAN_SPAWN_WORKERW = 0x052C
_SMTO_ABORTIFHUNG = 0x0002

# IUnknown occupies vtable slots 0..2.  The documented IDesktopWallpaper
# methods then start at slot 3.
_IDW_RELEASE_INDEX = 2
_IDW_SET_WALLPAPER_INDEX = 3
_IDW_GET_WALLPAPER_INDEX = 4
_IDW_SET_POSITION_INDEX = 10

_last_position_mode: str | None = None
_explorer_state_lock = threading.RLock()
_observed_progman_hwnd = 0
_primed_progman_hwnd = 0


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _load_windows_dll(name: str):
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError(f"Windows DLL loader is unavailable: {name}")
    return loader(name, use_last_error=True)


def _set_signature(function, argtypes, restype) -> None:
    """Set ctypes metadata while remaining friendly to lightweight test fakes."""
    try:
        function.argtypes = argtypes
        function.restype = restype
    except Exception:
        pass


def _signed_hresult(value) -> int:
    return ctypes.c_int32(int(value or 0)).value


def _hresult_succeeded(value) -> bool:
    return _signed_hresult(value) >= 0


class _DesktopWallpaperPointer:
    """Minimal apartment-local wrapper around an IDesktopWallpaper pointer."""

    def __init__(self, pointer: ctypes.c_void_p, ole32) -> None:
        self._pointer = pointer
        self._ole32 = ole32
        self._closed = False

    def _method(self, index: int, restype, *argtypes):
        vtable = ctypes.cast(
            self._pointer,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        address = vtable[index]
        if not address:
            raise RuntimeError(f"IDesktopWallpaper vtable slot {index} is null")
        prototype_factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        prototype = prototype_factory(restype, ctypes.c_void_p, *argtypes)
        return prototype(address)

    def set_wallpaper(self, path: str) -> int:
        method = self._method(
            _IDW_SET_WALLPAPER_INDEX,
            ctypes.c_int32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
        )
        return _signed_hresult(method(self._pointer, None, path))

    def get_wallpaper(self) -> tuple[int, str]:
        method = self._method(
            _IDW_GET_WALLPAPER_INDEX,
            ctypes.c_int32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        output = ctypes.c_void_p()
        result = _signed_hresult(method(self._pointer, None, ctypes.byref(output)))
        value = ""
        try:
            if _hresult_succeeded(result) and output.value:
                value = ctypes.wstring_at(output.value)
        finally:
            if output.value:
                free = self._ole32.CoTaskMemFree
                _set_signature(free, [ctypes.c_void_p], None)
                free(output)
        return result, value

    def set_position(self, position: int) -> int:
        method = self._method(
            _IDW_SET_POSITION_INDEX,
            ctypes.c_int32,
            ctypes.c_uint32,
        )
        return _signed_hresult(method(self._pointer, int(position)))

    def close(self) -> None:
        if self._closed or not self._pointer.value:
            return
        self._closed = True
        try:
            release = self._method(_IDW_RELEASE_INDEX, ctypes.c_uint32)
            release(self._pointer)
        finally:
            self._pointer = ctypes.c_void_p()


@contextmanager
def _open_idesktop_wallpaper():
    """Open IDesktopWallpaper in the current thread's COM apartment.

    Every GUI operation runs on a newly created worker thread. This context
    initializes and balances COM on every call, creates and releases the
    interface in the same apartment, and tolerates a thread that was already
    initialized with a different apartment model.
    """
    ole32 = None
    wallpaper = None
    should_uninitialize = False
    try:
        ole32 = _load_windows_dll("ole32")
        initialize = ole32.CoInitializeEx
        _set_signature(
            initialize,
            [ctypes.c_void_p, ctypes.c_uint32],
            ctypes.c_int32,
        )
        init_result = _signed_hresult(initialize(None, _COINIT_APARTMENTTHREADED))
        if init_result in (0, 1):  # S_OK / S_FALSE both require CoUninitialize.
            should_uninitialize = True
        elif init_result != _RPC_E_CHANGED_MODE:
            ole32 = None
        if ole32 is not None:
            create = ole32.CoCreateInstance
            _set_signature(
                create,
                [
                    ctypes.POINTER(_GUID),
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(_GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                ],
                ctypes.c_int32,
            )
            clsid = _GUID.parse(_DESKTOP_WALLPAPER_CLSID)
            iid = _GUID.parse(_DESKTOP_WALLPAPER_IID)
            pointer = ctypes.c_void_p()
            create_result = _signed_hresult(
                create(
                    ctypes.byref(clsid),
                    None,
                    _CLSCTX_SERVER,
                    ctypes.byref(iid),
                    ctypes.byref(pointer),
                )
            )
            if _hresult_succeeded(create_result) and pointer.value:
                wallpaper = _DesktopWallpaperPointer(pointer, ole32)
    except Exception:
        wallpaper = None
    try:
        yield wallpaper
    finally:
        if wallpaper is not None:
            try:
                wallpaper.close()
            except Exception:
                pass
        if should_uninitialize and ole32 is not None:
            try:
                uninitialize = ole32.CoUninitialize
                _set_signature(uninitialize, [], None)
                uninitialize()
            except Exception:
                pass


def _find_progman_hwnd() -> int:
    try:
        user32 = _load_windows_dll("user32")
        find_window = user32.FindWindowW
        _set_signature(find_window, [ctypes.c_wchar_p, ctypes.c_wchar_p], ctypes.c_void_p)
        return int(find_window("Progman", None) or 0)
    except Exception:
        return 0


def _sync_explorer_generation() -> int:
    """Invalidate shell-dependent caches when Explorer has restarted."""
    global _observed_progman_hwnd, _primed_progman_hwnd, _last_position_mode
    hwnd = _find_progman_hwnd()
    if not hwnd:
        return 0
    with _explorer_state_lock:
        if _observed_progman_hwnd and hwnd != _observed_progman_hwnd:
            _last_position_mode = None
            _primed_progman_hwnd = 0
        _observed_progman_hwnd = hwnd
    return hwnd


def _prime_explorer_wallpaper_host() -> bool:
    """Reproduce the one Explorer initialization side effect HTML used to own.

    The dynamic backends send Explorer's long-standing, undocumented 0x052C
    message before locating WorkerW.  In affected builds the first HTML launch
    therefore initialized the desktop host and made later static transitions
    work.  Static mode now performs that one best-effort wake-up itself, once
    per Progman window (and retries after Explorer restarts), without creating
    or parenting any application window.
    """
    global _primed_progman_hwnd, _last_position_mode
    hwnd = _sync_explorer_generation()
    if not hwnd:
        return False
    with _explorer_state_lock:
        if hwnd == _primed_progman_hwnd:
            return True
        try:
            user32 = _load_windows_dll("user32")
            send = user32.SendMessageTimeoutW
            _set_signature(
                send,
                [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_size_t,
                    ctypes.c_ssize_t,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.POINTER(ctypes.c_size_t),
                ],
                ctypes.c_void_p,
            )
            result = ctypes.c_size_t()
            sent = send(
                ctypes.c_void_p(hwnd),
                _PROGMAN_SPAWN_WORKERW,
                0,
                0,
                _SMTO_ABORTIFHUNG,
                1000,
                ctypes.byref(result),
            )
            if not sent:
                return False
            _primed_progman_hwnd = hwnd
            return True
        except Exception:
            return False


def _set_wallpaper_via_com(path: str) -> bool:
    """Set one wallpaper through IDesktopWallpaper in this calling thread."""
    api_path = _wallpaper_api_path(path)
    with _open_idesktop_wallpaper() as wallpaper:
        if wallpaper is None:
            return False
        try:
            return _hresult_succeeded(wallpaper.set_wallpaper(api_path))
        except Exception:
            return False


def _set_position_via_com(fit_mode: str) -> bool:
    """Set desktop fit mode through IDesktopWallpaper with restart-safe caching."""
    global _last_position_mode
    _sync_explorer_generation()
    normalized = normalize_style_key(fit_mode)
    if normalized == _last_position_mode:
        return True
    position_map = {
        "居中": DWPOS_CENTER,
        "平铺": DWPOS_TILE,
        "拉伸": DWPOS_STRETCH,
        "适应": DWPOS_FIT,
        "填充": DWPOS_FILL,
    }
    with _open_idesktop_wallpaper() as wallpaper:
        if wallpaper is None:
            return False
        try:
            if _hresult_succeeded(wallpaper.set_position(position_map.get(normalized, DWPOS_FILL))):
                _last_position_mode = normalized
                return True
        except Exception:
            pass
    return False


def _ensure_existing_file(path: str) -> str:
    """Return a direct absolute Unicode path without filesystem canonicalization.

    ``Path.resolve()`` performs extra metadata work and can block on slow or
    disconnected locations.  Windows wallpaper APIs are wide-character APIs, so
    Chinese and other non-ASCII names should be passed through directly.
    """
    try:
        raw = os.fspath(path)
    except (TypeError, ValueError, OSError) as exc:
        raise FileNotFoundError(f"Invalid wallpaper path: {path!r}") from exc
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    abs_path = os.path.abspath(os.path.expanduser(str(raw)))
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Wallpaper file does not exist: {abs_path}")
    return abs_path


def get_screen_size(root=None):
    try:
        user32 = _load_windows_dll("user32")
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        pass
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if isinstance(app, QApplication):
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                return geo.width(), geo.height()
    except Exception:
        pass
    try:
        if root is not None:
            return root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception:
        pass
    return 1920, 1080


def _set_windows_wallpaper_legacy(path: str) -> None:
    """旧路径兜底: SystemParametersInfoW.

    注意: 用 SPIF_UPDATEINIFILE | SPIF_SENDCHANGE 会广播 WM_SETTINGCHANGE,
    导致 Explorer 重绘任务栏. 仅在 IDesktopWallpaper COM 不可用(老版本 Windows
    或原生 COM 接口不可用)时使用.
    """
    abs_path = _wallpaper_api_path(path)
    user32 = _load_windows_dll("user32")
    system_parameters = user32.SystemParametersInfoW
    _set_signature(
        system_parameters,
        [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_uint32],
        ctypes.c_int32,
    )
    set_last_error = getattr(ctypes, "set_last_error", None)
    if set_last_error is not None:
        set_last_error(0)
    ok = system_parameters(
        SPI_SETDESKWALLPAPER,
        0,
        abs_path,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    if not ok:
        get_last_error = getattr(ctypes, "get_last_error", None)
        err = get_last_error() if get_last_error is not None else "unknown"
        raise RuntimeError(f"Windows wallpaper change failed, GetLastError={err}")


def _set_windows_wallpaper(path: str, *, animate: bool = True) -> None:
    """Apply a Windows wallpaper using the selected transition policy.

    ``animate=True`` uses IDesktopWallpaper, which follows Explorer's normal
    transition policy. ``animate=False`` commits the target frame directly via
    SystemParametersInfoW. Both paths accept Unicode filenames.
    """
    original = _ensure_existing_file(path)
    # 1.4.2: keep the native Explorer transition path for static wallpapers.
    # Do not force a legacy SPI refresh after a successful COM update: the
    # fallback used to cancel the first static-wallpaper transition in some
    # packaged builds where COM initialization was delayed.
    if animate:
        # Static wallpaper changes must stay on the documented shell path.
        # Do not send Progman/WorkerW messages here: 0x052C is an undocumented
        # dynamic-wallpaper host trick and changes Explorer's desktop window
        # topology.  It is unrelated to IDesktopWallpaper transitions and can
        # suppress the native static-wallpaper animation entirely.
        try:
            if _set_wallpaper_via_com(original):
                return
        except Exception:
            # A failed COM attempt must not break wallpaper switching.
            pass
    _set_windows_wallpaper_legacy(original)


def refresh_shell_ui() -> None:
    """轻量 shell 重绘. 仅在显式需要时调用(如托盘菜单变更).

    壁纸切换路径不再调用此函数(IDesktopWallpaper 不需要).
    改进:
      - 不再广播 WM_SETTINGCHANGE (SPIF_SENDCHANGE 已经广播过, 重复广播会卡).
      - RedrawWindow 用 RDW_INVALIDATE (异步) 而非 RDW_UPDATENOW (同步阻塞).
    """
    try:
        user32 = _load_windows_dll("user32")
        RDW_INVALIDATE = 0x0001
        RDW_ALLCHILDREN = 0x0080
        # 移除 RDW_UPDATENOW (0x0100) —— 同步重绘会阻塞调用线程, 是卡顿主因之一
        flags = RDW_INVALIDATE | RDW_ALLCHILDREN
        for class_name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = user32.FindWindowW(class_name, None)
            while hwnd:
                try:
                    user32.RedrawWindow(hwnd, None, None, flags)
                except Exception:
                    pass
                hwnd = user32.FindWindowExW(None, hwnd, class_name, None)
    except Exception:
        pass


def set_wallpaper_platform(path: str, animate: bool = True) -> None:
    _set_windows_wallpaper(path, animate=bool(animate))


def get_current_wallpaper_platform() -> str:
    # Query through the same documented COM interface as the write path.
    with _open_idesktop_wallpaper() as wallpaper:
        if wallpaper is not None:
            try:
                result, path = wallpaper.get_wallpaper()
                if _hresult_succeeded(result) and path:
                    return _original_path_for_alias(path)
            except Exception:
                pass
    # Fall back to the wide-character SystemParametersInfo API.
    max_chars = 32767
    buf = ctypes.create_unicode_buffer(max_chars)
    user32 = _load_windows_dll("user32")
    system_parameters = user32.SystemParametersInfoW
    _set_signature(
        system_parameters,
        [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32],
        ctypes.c_int32,
    )
    ok = system_parameters(
        SPI_GETDESKWALLPAPER,
        max_chars,
        ctypes.cast(buf, ctypes.c_void_p),
        0,
    )
    return _original_path_for_alias(buf.value) if ok else ""


def configure_fit_mode(fit_mode, winreg_module=None, log=None):
    """设置壁纸适应方式.

    优先走 IDesktopWallpaper::SetPosition (与 SetWallpaper 同路径, 不触发刷新).
    回退到注册表 WallpaperStyle/TileWallpaper (旧路径, 需要 SetWallpaper 重新触发).
    """
    # 优先 COM
    if _set_position_via_com(fit_mode):
        return
    # 回退注册表
    fit_mode = normalize_style_key(fit_mode)
    if winreg_module is None:
        return
    key = None
    try:
        key = winreg_module.OpenKey(
            winreg_module.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
            0,
            winreg_module.KEY_WRITE,
        )
        winreg_module.SetValueEx(key, "WallpaperStyle", 0, winreg_module.REG_SZ, str(STYLE_MAP[fit_mode]))
        winreg_module.SetValueEx(
            key,
            "TileWallpaper",
            0,
            winreg_module.REG_SZ,
            "1" if fit_mode == "平铺" else "0",
        )
    except Exception as exc:
        if log:
            log("设置适应模式失败: " + str(exc))
    finally:
        if key is not None:
            try:
                winreg_module.CloseKey(key)
            except Exception:
                pass
