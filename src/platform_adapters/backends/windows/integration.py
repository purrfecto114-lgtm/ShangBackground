from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import tempfile

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
# 联网考证结论:
#   1. SystemParametersInfoW(SPIF_SENDCHANGE) 会广播 WM_SETTINGCHANGE,
#      Explorer 收到后重绘任务栏 → 卡顿/闪烁.
#      证据: https://devblogs.microsoft.com/oldnewthing/20050310-00/?p=36233
#   2. IDesktopWallpaper::SetWallpaper 是 Settings 应用走的路径,
#      不广播 WM_SETTINGCHANGE → 无任务栏闪烁, 且原生支持 per-monitor.
#      证据: https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-idesktopwallpaper
#   3. 原生淡入淡出是 OS 级效果, 由"系统属性→性能→在窗口下显示动画"控制,
#      走 IDesktopWallpaper 路径时若用户开了动画设置会自动渲染淡入淡出.
#      证据: https://github.com/t1m0thyj/WinDynamicDesktop/issues/321 (Lively 作者确认)
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

# COM interface definitions are safe to cache, but COM object instances are
# apartment-affine and must be created on the calling thread.
_idw_available = None  # None=未探测, True/False=探测结果
_idw_interface_cls = None  # 缓存 IDesktopWallpaper 接口类定义（线程安全）
_last_position_mode: str | None = None


def _build_idesktop_wallpaper_interface():
    """构建并缓存 IDesktopWallpaper comtypes 接口类。

    接口类定义本身是线程安全的，可以跨线程共享；只有实例（COM 对象）
    有 apartment 亲和性。把类定义缓存起来避免每次调用都重新定义。
    """
    global _idw_interface_cls
    if _idw_interface_cls is not None:
        return _idw_interface_cls
    from comtypes import IUnknown, GUID, COMMETHOD
    from ctypes import HRESULT, POINTER
    from ctypes.wintypes import LPCWSTR, UINT, LPWSTR

    class IDesktopWallpaper(IUnknown):
        _iid_ = GUID(_DESKTOP_WALLPAPER_IID)
        _methods_ = [
            COMMETHOD([], HRESULT, "SetWallpaper",
                      (["in"], LPCWSTR, "monitorID"),
                      (["in"], LPCWSTR, "wallpaper")),
            COMMETHOD([], HRESULT, "GetWallpaper",
                      (["in"], LPCWSTR, "monitorID"),
                      (["out"], POINTER(LPWSTR), "wallpaper")),
            COMMETHOD([], HRESULT, "GetMonitorDevicePathAt",
                      (["in"], UINT, "monitorIndex"),
                      (["out"], POINTER(LPWSTR), "monitorID")),
            COMMETHOD([], HRESULT, "GetMonitorDevicePathCount",
                      (["out"], POINTER(UINT), "count")),
            COMMETHOD([], HRESULT, "GetMonitorRECT",
                      (["in"], LPCWSTR, "monitorID"),
                      (["out"], POINTER(ctypes.c_long * 4), "rect")),
            COMMETHOD([], HRESULT, "SetBackgroundColor",
                      (["in"], UINT, "color")),
            COMMETHOD([], HRESULT, "GetBackgroundColor",
                      (["out"], POINTER(UINT), "color")),
            COMMETHOD([], HRESULT, "SetPosition",
                      (["in"], UINT, "position")),
            COMMETHOD([], HRESULT, "GetPosition",
                      (["out"], POINTER(UINT), "position")),
            COMMETHOD([], HRESULT, "SetStatus",
                      (["in"], ctypes.c_int, "enable")),
            COMMETHOD([], HRESULT, "GetStatus",
                      (["out"], POINTER(ctypes.c_int), "enable")),
        ]
    _idw_interface_cls = IDesktopWallpaper
    return IDesktopWallpaper


def _create_idesktop_wallpaper():
    """在当前线程创建一个新的 IDesktopWallpaper COM 实例。

    COM 对象有 apartment 亲和性：在线程 A 创建的实例不能在线程 B 上调用。
    壁纸切换、当前壁纸查询和 shell 辅助命令可能来自不同线程，因此实例
    不跨调用缓存，避免 RPC_E_WRONGTHREAD 后退到较重的兼容路径。

    这里每次调用都重新 CoCreateInstance，开销远小于一次
    WM_SETTINGCHANGE 广播的卡顿。comtypes 会在 CoCreateInstance 时自动
    CoInitialize 当前线程。
    """
    global _idw_available
    if _idw_available is False:
        return None
    try:
        import comtypes  # type: ignore
        from comtypes import GUID  # type: ignore
    except (ImportError, ModuleNotFoundError):
        # A missing optional dependency is stable for this process.  RPC/COM
        # failures are not: Explorer restarts, apartment initialization and
        # transient shell state can recover on the next call, so those must not
        # permanently disable native transitions.
        _idw_available = False
        return None

    try:
        IDesktopWallpaper = _build_idesktop_wallpaper_interface()
        clsid = GUID(_DESKTOP_WALLPAPER_CLSID)
        instance = comtypes.CoCreateInstance(clsid, interface=IDesktopWallpaper)
    except Exception:
        return None
    _idw_available = True
    return instance


def _try_get_idesktop_wallpaper():
    """Create an IDesktopWallpaper instance for the current COM apartment.

    Reads can run from the GUI, shell helper, or worker threads, so caching the
    instance is unsafe for exactly the same reason as caching it for writes.
    """
    return _create_idesktop_wallpaper()


def _com_call_succeeded(result) -> bool:
    """Normalize comtypes HRESULT method return conventions.

    ``comtypes`` raises ``COMError`` for a failed HRESULT.  A successful method
    with no ``[out]`` parameters commonly returns ``None`` rather than integer
    ``0``.  Treating only ``0`` as success caused SetWallpaper/SetPosition to
    be followed by the legacy SPI path, replacing Explorer's native transition
    with an immediate second write.
    """
    if result is None:
        return True
    try:
        return int(result) >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def _set_wallpaper_via_com(path: str) -> bool:
    """用 IDesktopWallpaper::SetWallpaper 设置壁纸. 成功返回 True, 不可用返回 False.

    优势:
      - 不广播 WM_SETTINGCHANGE → 不触发任务栏重绘.
      - 支持 per-monitor.
      - 走 Settings 应用同款路径, 用户开启"窗口动画"时自动有原生淡入淡出.

    Thread safety: COM 对象有 apartment 亲和性 — 在线程 A 创建的实例不能
    在线程 B 上调用，否则会 RPC_E_WRONGTHREAD 失败。壁纸切换是在 run_core
    的 worker 线程里执行 set_wallpaper_platform，而 _try_get_idesktop_wallpaper
    缓存的实例可能是主线程创建的。因此这里每次调用都在当前线程重新
    CoCreateInstance，避免跨线程使用缓存实例导致的回退到 SystemParametersInfoW
    旧路径（旧路径广播 WM_SETTINGCHANGE → 任务栏重绘 → 卡顿）。
    """
    dw = _create_idesktop_wallpaper()
    if dw is None:
        return False
    try:
        api_path = _wallpaper_api_path(path)
        # monitorID = NULL (0) 表示所有显示器同一张
        result = dw.SetWallpaper(None, api_path)
        # comtypes returns None for many successful HRESULT-only methods.
        return _com_call_succeeded(result)
    except Exception:
        return False


def _set_position_via_com(fit_mode: str) -> bool:
    """用 IDesktopWallpaper::SetPosition 设置适应方式. 成功返回 True.

    Reapplying the same position before every image creates unnecessary COM and
    Explorer work, so the normalized mode is cached after a successful call.
    """
    global _last_position_mode
    normalized = normalize_style_key(fit_mode)
    if normalized == _last_position_mode:
        return True
    dw = _create_idesktop_wallpaper()
    if dw is None:
        return False
    try:
        position_map = {
            "居中": DWPOS_CENTER,
            "平铺": DWPOS_TILE,
            "拉伸": DWPOS_STRETCH,
            "适应": DWPOS_FIT,
            "填充": DWPOS_FILL,
        }
        pos = position_map.get(normalized, DWPOS_FILL)
        result = dw.SetPosition(pos)
        if _com_call_succeeded(result):
            _last_position_mode = normalized
            return True
        return False
    except Exception:
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
        return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        pass
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
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
    或 comtypes 缺失)时使用.
    """
    abs_path = _wallpaper_api_path(path)
    try:
        ctypes.windll.kernel32.SetLastError(0)
    except Exception:
        pass
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER,
        0,
        abs_path,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    if not ok:
        try:
            err = ctypes.windll.kernel32.GetLastError()
        except Exception:
            err = "unknown"
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
        RDW_INVALIDATE = 0x0001
        RDW_ALLCHILDREN = 0x0080
        # 移除 RDW_UPDATENOW (0x0100) —— 同步重绘会阻塞调用线程, 是卡顿主因之一
        flags = RDW_INVALIDATE | RDW_ALLCHILDREN
        for class_name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = ctypes.windll.user32.FindWindowW(class_name, None)
            while hwnd:
                try:
                    ctypes.windll.user32.RedrawWindow(hwnd, None, None, flags)
                except Exception:
                    pass
                hwnd = ctypes.windll.user32.FindWindowExW(None, hwnd, class_name, None)
    except Exception:
        pass


def set_wallpaper_platform(path: str, animate: bool = True) -> None:
    _set_windows_wallpaper(path, animate=bool(animate))


def get_current_wallpaper_platform() -> str:
    # Prefer IDesktopWallpaper so the query observes the same per-monitor state
    # used by the write path.  The returned string is allocated with CoTaskMem.
    dw = _try_get_idesktop_wallpaper()
    if dw is not None:
        from ctypes.wintypes import LPWSTR

        out = LPWSTR()
        try:
            hr = dw.GetWallpaper(None, ctypes.byref(out))  # type: ignore[attr-defined]
            if hr == 0 and out.value:
                return _original_path_for_alias(str(out.value))
        except Exception:
            pass
        finally:
            if out:
                try:
                    ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
                except Exception:
                    pass
    # Fall back to the wide-character SystemParametersInfo API.
    max_chars = 32767
    buf = ctypes.create_unicode_buffer(max_chars)
    ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, max_chars, buf, 0)
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
        winreg_module.CloseKey(key)
    except Exception as exc:
        if log:
            log("设置适应模式失败: " + str(exc))
