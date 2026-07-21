"""WorkerW integration for pywebview's Windows WebView2/WinForms window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
from platform import machine
import sys
from typing import Any, cast


def webview2_runtime_version() -> str:
    """Return the installed WebView2 Runtime version using the loader API."""
    if not sys.platform.startswith("win"):
        return ""
    try:
        from webview.util import interop_dll_path  # pyright: ignore[reportMissingImports]

        arch_name = machine().lower()
        if arch_name in {"arm64", "aarch64"}:
            runtime_arch = "win-arm64"
        elif arch_name in {"x86", "i386", "i686"}:
            runtime_arch = "win-x86"
        else:
            runtime_arch = "win-x64"
        runtime_dir = Path(interop_dll_path(runtime_arch))
        loader_path = runtime_dir / "WebView2Loader.dll"
        if not loader_path.is_file():
            return ""
        factory = cast(Any, getattr(ctypes, "WinDLL", None))
        if not callable(factory):
            return ""
        loader = cast(Any, factory(str(loader_path), use_last_error=True))
        query = loader.GetAvailableCoreWebView2BrowserVersionString
        query.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p))
        query.restype = ctypes.c_long
        version = ctypes.c_wchar_p()
        result = int(query(None, ctypes.byref(version)))
        if result < 0 or not version.value:
            return ""
        value = str(version.value)
        try:
            ole32 = cast(Any, factory("ole32", use_last_error=True))
            ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
            ole32.CoTaskMemFree(ctypes.cast(version, ctypes.c_void_p))
        except Exception:
            pass
        return value
    except Exception:
        return ""


def environment_error() -> str:
    """Return an actionable platform-runtime error for dependency probes."""
    if not sys.platform.startswith("win"):
        return "Windows WebView2 probing is unavailable on this host"
    if webview2_runtime_version():
        return ""
    return (
        "Microsoft Edge WebView2 Evergreen Runtime was not detected; "
        "install or repair WebView2 before using HTML wallpaper"
    )


def _handle(native: Any) -> int:
    value = getattr(native, "Handle", None)
    if value is None:
        raise RuntimeError("pywebview WinForms window has no Handle")
    try:
        return int(value.ToInt64())
    except AttributeError:
        return int(value)


def _user32() -> Any:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Windows desktop integration is unavailable on this host")
    factory = cast(Any, getattr(ctypes, "WinDLL", None))
    if not callable(factory):
        raise RuntimeError("ctypes.WinDLL is unavailable on this host")
    return factory("user32", use_last_error=True)


def _find_workerw(user32) -> int:
    progman = int(user32.FindWindowW("Progman", None) or 0)
    if progman:
        result = ctypes.c_size_t(0)
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0002, 1000, ctypes.byref(result))
    found = ctypes.c_void_p()
    callback_factory = cast(Any, getattr(ctypes, "WINFUNCTYPE", None))
    if not callable(callback_factory):
        raise RuntimeError("ctypes.WINFUNCTYPE is unavailable on this host")
    callback_type = cast(Any, callback_factory(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM))

    @callback_type
    def callback(hwnd, _lparam):
        shell = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shell:
            candidate = user32.FindWindowExW(0, hwnd, "WorkerW", None)
            if candidate:
                found.value = int(candidate)
                return False
        return True

    user32.EnumWindows(callback, 0)
    return int(found.value or progman or 0)


def native_window_id(native: Any) -> int:
    return _handle(native)


def virtual_geometry() -> tuple[int, int, int, int]:
    user32 = _user32()
    return (
        int(user32.GetSystemMetrics(76)),
        int(user32.GetSystemMetrics(77)),
        max(1, int(user32.GetSystemMetrics(78))),
        max(1, int(user32.GetSystemMetrics(79))),
    )


def set_mouse_through(native: Any, enabled: bool) -> bool:
    hwnd = _handle(native)
    user32 = _user32()
    current = int(user32.GetWindowLongW(hwnd, -20))
    transparent = 0x00000020
    updated = current | transparent if enabled else current & ~transparent
    if updated != current:
        user32.SetWindowLongW(hwnd, -20, updated)
    return True


def set_render_visible(native: Any, visible: bool) -> bool:
    """Hide or show the native wallpaper window without activating it."""
    hwnd = _handle(native)
    user32 = _user32()
    user32.ShowWindow(hwnd, 8 if visible else 0)  # SW_SHOWNA / SW_HIDE
    return True


def configure(native: Any, mouse_through: bool) -> bool:
    hwnd = _handle(native)
    user32 = _user32()
    workerw = _find_workerw(user32)
    if not hwnd or not workerw:
        return False
    style = int(user32.GetWindowLongW(hwnd, -16))
    user32.SetWindowLongW(hwnd, -16, (style | 0x40000000) & ~0x80000000)
    exstyle = int(user32.GetWindowLongW(hwnd, -20))
    user32.SetWindowLongW(hwnd, -20, exstyle | 0x00000080 | 0x08000000)
    set_mouse_through(native, mouse_through)
    user32.SetParent(hwnd, workerw)
    rect = wintypes.RECT()
    if user32.GetClientRect(workerw, ctypes.byref(rect)):
        user32.MoveWindow(hwnd, 0, 0, max(1, rect.right - rect.left), max(1, rect.bottom - rect.top), False)
    user32.ShowWindow(hwnd, 5)
    return True
