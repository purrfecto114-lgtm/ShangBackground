from __future__ import annotations

import ctypes
from pathlib import Path

from app.config import STYLE_MAP, normalize_style_key


SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002


def _ensure_existing_file(path: str) -> str:
    """Return an absolute path and fail early with a useful error."""
    abs_path = str(Path(path).expanduser().resolve())
    if not Path(abs_path).is_file():
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


def _set_windows_wallpaper(path: str) -> None:
    abs_path = _ensure_existing_file(path)
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


def refresh_shell_ui() -> None:
    """Force a lightweight Windows shell/taskbar repaint after wallpaper or tray changes."""
    try:
        RDW_INVALIDATE = 0x0001
        RDW_UPDATENOW = 0x0100
        RDW_ALLCHILDREN = 0x0080
        for class_name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = ctypes.windll.user32.FindWindowW(class_name, None)
            while hwnd:
                try:
                    ctypes.windll.user32.RedrawWindow(hwnd, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
                except Exception:
                    pass
                hwnd = ctypes.windll.user32.FindWindowExW(None, hwnd, class_name, None)
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,
            0x001A,
            0,
            r"Control Panel\Desktop",
            0x0002,
            100,
            None,
        )
    except Exception:
        pass


def set_wallpaper_platform(path: str) -> None:
    _set_windows_wallpaper(path)


def get_current_wallpaper_platform() -> str:
    # Use a long-path-sized buffer; 260 truncates valid wallpapers under long
    # user/profile paths and then makes restore/history logic mis-detect files.
    max_chars = 32767
    buf = ctypes.create_unicode_buffer(max_chars)
    ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, max_chars, buf, 0)
    return buf.value if ok else ""


def configure_fit_mode(fit_mode, winreg_module=None, log=None):
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
