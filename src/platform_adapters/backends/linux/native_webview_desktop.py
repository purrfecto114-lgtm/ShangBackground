"""X11 desktop-window integration for pywebview's GTK/WebKitGTK window."""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any, cast

_X11: Any | None = None
_XEXT: Any | None = None
_X11_LOADED = False


def _is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def _require_linux_x11() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux desktop integration is unavailable on this host")
    if _is_wayland() and os.environ.get("SHANGBACKGROUND_ALLOW_UNSAFE_WAYLAND_HTML") != "1":
        raise RuntimeError("native HTML wallpaper currently requires X11")


def _x11_libraries() -> tuple[Any | None, Any | None]:
    global _X11, _XEXT, _X11_LOADED
    if _X11_LOADED:
        return _X11, _XEXT
    _X11_LOADED = True
    if not sys.platform.startswith("linux") or _is_wayland():
        return None, None
    try:
        x11 = ctypes.CDLL("libX11.so.6")
        xext = ctypes.CDLL("libXext.so.6")
        x11.XOpenDisplay.argtypes = (ctypes.c_char_p,)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultScreen.argtypes = (ctypes.c_void_p,)
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDisplayWidth.argtypes = (ctypes.c_void_p, ctypes.c_int)
        x11.XDisplayWidth.restype = ctypes.c_int
        x11.XDisplayHeight.argtypes = (ctypes.c_void_p, ctypes.c_int)
        x11.XDisplayHeight.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = (ctypes.c_void_p,)
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XFlush.argtypes = (ctypes.c_void_p,)
        x11.XFlush.restype = ctypes.c_int
        xext.XShapeCombineRectangles.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        )
        xext.XShapeCombineRectangles.restype = None
        xext.XShapeCombineShape.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
        )
        xext.XShapeCombineShape.restype = None
        _X11, _XEXT = x11, xext
    except (OSError, AttributeError):
        _X11, _XEXT = None, None
    return _X11, _XEXT


def environment_error() -> str:
    """Return an actionable error when X11 desktop embedding is unavailable."""
    try:
        _require_linux_x11()
    except RuntimeError as exc:
        return str(exc)
    if not os.environ.get("DISPLAY"):
        return "DISPLAY is not set; native HTML wallpaper requires an X11 session"
    x11, xext = _x11_libraries()
    if x11 is None or xext is None:
        return "libX11/libXext is unavailable; X11 desktop embedding cannot start"
    display = x11.XOpenDisplay(None)
    if not display:
        return "the configured X11 display cannot be opened"
    x11.XCloseDisplay(display)
    return ""


def _gdk_window(native: Any) -> Any:
    native.realize()
    window = native.get_window()
    if window is None:
        raise RuntimeError("GTK window has not been realized")
    return window


def native_window_id(native: Any) -> int:
    _require_linux_x11()
    window = _gdk_window(native)
    get_xid = getattr(window, "get_xid", None)
    if not callable(get_xid):
        raise RuntimeError("GTK backend does not expose an X11 window id")
    return int(cast(int, get_xid()))


def virtual_geometry() -> tuple[int, int, int, int]:
    _require_linux_x11()
    x11, _xext = _x11_libraries()
    if x11 is None:
        return (0, 0, 1920, 1080)
    display = x11.XOpenDisplay(None)
    if not display:
        return (0, 0, 1920, 1080)
    try:
        screen = int(x11.XDefaultScreen(display))
        return (
            0,
            0,
            max(1, int(x11.XDisplayWidth(display, screen))),
            max(1, int(x11.XDisplayHeight(display, screen))),
        )
    finally:
        x11.XCloseDisplay(display)


def _set_x11_input_shape(window_handle: int, enabled: bool) -> bool:
    x11, xext = _x11_libraries()
    if x11 is None or xext is None or not window_handle:
        return False
    display = x11.XOpenDisplay(None)
    if not display:
        return False
    try:
        shape_input = 2
        shape_set = 1
        if enabled:
            xext.XShapeCombineRectangles(
                display,
                window_handle,
                shape_input,
                0,
                0,
                None,
                0,
                shape_set,
                0,
            )
        else:
            shape_bounding = 0
            xext.XShapeCombineShape(
                display,
                window_handle,
                shape_input,
                0,
                0,
                window_handle,
                shape_bounding,
                shape_set,
            )
        x11.XFlush(display)
        return True
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(display)


def set_mouse_through(native: Any, enabled: bool) -> bool:
    _require_linux_x11()
    return _set_x11_input_shape(native_window_id(native), bool(enabled))


def set_render_visible(native: Any, visible: bool) -> bool:
    """Hide rendering while covered so WebKit can enter background state."""
    if visible:
        native.show_all()
        native.lower()
    else:
        native.hide()
    return True


def configure(native: Any, mouse_through: bool) -> bool:
    _require_linux_x11()
    import gi  # pyright: ignore[reportMissingImports]  # Linux-only optional binding

    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk  # pyright: ignore[reportMissingImports]

    native.set_decorated(False)
    native.set_skip_taskbar_hint(True)
    native.set_skip_pager_hint(True)
    native.set_accept_focus(False)
    native.set_keep_below(True)
    native.stick()
    native.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
    native.show_all()
    native.lower()
    set_mouse_through(native, mouse_through)
    return True
