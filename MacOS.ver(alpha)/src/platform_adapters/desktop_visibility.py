"""Cross-platform desktop visibility detection for HTML wallpaper auto-pause.

The renderer must only freeze when the desktop is *visually covered*, not merely
because another application owns keyboard focus.  We estimate visual coverage
using a small grid on each display.  If any display has more than 5% desktop
area visible, rendering continues.  Unknown/unsupported window systems return
``None`` so callers can conservatively keep rendering rather than falsely
pausing.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from typing import Sequence


PAUSE_COVERAGE_THRESHOLD = 0.95
# A globally translucent window still lets the desktop contribute visibly.
# Treat only near-opaque windows as covering so auto-pause stays conservative.
OPAQUE_ALPHA_THRESHOLD = 0.95
GRID_COLUMNS = 24
GRID_ROWS = 14
_CACHE_TTL_SECONDS = 1.25


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def valid(self) -> bool:
        return self.width > 1 and self.height > 1

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


def _as_rect(value) -> Rect | None:
    if isinstance(value, Rect):
        return value if value.valid() else None
    try:
        if len(value) != 4:
            return None
        rect = Rect(float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        return rect if rect.valid() else None
    except Exception:
        return None


def coverage_ratios(
    screen_rects: Sequence[Rect | Sequence[float]],
    window_rects: Sequence[Rect | Sequence[float]],
    *,
    columns: int = GRID_COLUMNS,
    rows: int = GRID_ROWS,
) -> list[float]:
    """Return approximate covered-area ratios for each screen.

    Center-point grid sampling intentionally mirrors the practical approach used
    by live-wallpaper software: it handles multiple side-by-side windows and is
    much cheaper and less fragile than exact polygon unions across native APIs.
    """
    columns = max(4, int(columns))
    rows = max(4, int(rows))
    screens = [rect for value in screen_rects if (rect := _as_rect(value))]
    windows = [rect for value in window_rects if (rect := _as_rect(value))]
    ratios: list[float] = []
    for screen in screens:
        relevant = [window for window in windows if window.intersects(screen)]
        if not relevant:
            ratios.append(0.0)
            continue
        covered = 0
        total = columns * rows
        cell_w = screen.width / columns
        cell_h = screen.height / rows
        for row in range(rows):
            cy = screen.y + (row + 0.5) * cell_h
            for col in range(columns):
                cx = screen.x + (col + 0.5) * cell_w
                if any(window.contains_point(cx, cy) for window in relevant):
                    covered += 1
        ratios.append(covered / total)
    return ratios


def desktop_visible_from_rects(
    screen_rects: Sequence[Rect | Sequence[float]],
    window_rects: Sequence[Rect | Sequence[float]],
    *,
    pause_threshold: float = PAUSE_COVERAGE_THRESHOLD,
) -> bool | None:
    """Return True when any display still exposes meaningful desktop area.

    ``None`` means screen geometry was unavailable.  A wallpaper should only be
    paused when *every* display is covered by at least ``pause_threshold``.
    """
    ratios = coverage_ratios(screen_rects, window_rects)
    if not ratios:
        return None
    threshold = max(0.5, min(1.0, float(pause_threshold)))
    return any(ratio < threshold for ratio in ratios)


def _qt_screen_rects() -> list[Rect]:
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return []
        result: list[Rect] = []
        for screen in app.screens() or []:
            geo = screen.geometry()
            rect = Rect(float(geo.x()), float(geo.y()), float(geo.width()), float(geo.height()))
            if rect.valid():
                result.append(rect)
        return result
    except Exception:
        return []


def _windows_screen_rects() -> list[Rect]:
    """Enumerate monitor bounds in the same Win32 coordinate space as windows.

    QScreen geometry is device-independent on some Qt/DPI configurations while
    EnumWindows rectangles follow the process DPI-awareness context. Mixing the
    two can make a maximized window appear to cover only part of a scaled monitor.
    """
    if not sys.platform.startswith("win"):
        return []
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        result: list[Rect] = []

        # HMONITOR/HDC are absent in some Python builds even though both are
        # pointer-sized Win32 handles. Fall back to HANDLE to keep the module
        # importable across supported CPython distributions.
        hmonitor_type = getattr(wintypes, "HMONITOR", wintypes.HANDLE)
        hdc_type = getattr(wintypes, "HDC", wintypes.HANDLE)

        @ctypes.WINFUNCTYPE(
            wintypes.BOOL, hmonitor_type, hdc_type, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
        )
        def _enum(_monitor, _hdc, native_rect, _lparam):
            try:
                value = native_rect.contents
                rect = Rect(
                    float(value.left), float(value.top),
                    float(value.right - value.left), float(value.bottom - value.top),
                )
                if rect.valid():
                    result.append(rect)
            except Exception:
                pass
            return True

        if not user32.EnumDisplayMonitors(0, None, _enum, 0):
            return []
        return result
    except Exception:
        return []


def _screen_rects() -> list[Rect]:
    if sys.platform.startswith("win"):
        native = _windows_screen_rects()
        if native:
            return native
    return _qt_screen_rects()


def _windows_covering_rects(screens: Sequence[Rect]) -> list[Rect] | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        try:
            dwmapi = ctypes.windll.dwmapi
        except Exception:
            dwmapi = None

        own_pid = os.getpid()
        skip_classes = {
            "Progman",
            "WorkerW",
            "SHELLDLL_DefView",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "NotifyIconOverflowWindow",
            "DV2ControlHost",
            "MsgrIMEWindowClass",
            "IME",
        }
        result: list[Rect] = []
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        DWMWA_CLOAKED = 14
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_ALPHA = 0x00000002

        get_window_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW

        def _window_rect(hwnd) -> Rect | None:
            native = wintypes.RECT()
            got = False
            if dwmapi is not None:
                try:
                    got = dwmapi.DwmGetWindowAttribute(
                        hwnd,
                        DWMWA_EXTENDED_FRAME_BOUNDS,
                        ctypes.byref(native),
                        ctypes.sizeof(native),
                    ) == 0
                except Exception:
                    got = False
            if not got and not user32.GetWindowRect(hwnd, ctypes.byref(native)):
                return None
            rect = Rect(
                float(native.left),
                float(native.top),
                float(native.right - native.left),
                float(native.bottom - native.top),
            )
            return rect if rect.valid() and any(rect.intersects(screen) for screen in screens) else None

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) == own_pid:
                    return True
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, len(class_buf))
                if class_buf.value in skip_classes:
                    return True
                if dwmapi is not None:
                    cloaked = wintypes.DWORD()
                    try:
                        if dwmapi.DwmGetWindowAttribute(
                            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                        ) == 0 and cloaked.value:
                            return True
                    except Exception:
                        pass
                try:
                    ex_style = int(get_window_long(hwnd, GWL_EXSTYLE) or 0)
                except Exception:
                    ex_style = 0
                if ex_style & WS_EX_LAYERED:
                    key = wintypes.DWORD()
                    alpha = ctypes.c_ubyte(255)
                    flags = wintypes.DWORD()
                    try:
                        ok = user32.GetLayeredWindowAttributes(
                            hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)
                        )
                    except Exception:
                        ok = False
                    # A per-pixel layered window can be visually transparent even
                    # when its bounding rectangle is fullscreen. Unless Windows
                    # exposes a near-opaque global alpha, fail open and keep the
                    # wallpaper active rather than pausing over visible desktop.
                    if not ok or not (flags.value & LWA_ALPHA):
                        return True
                    if alpha.value < int(255 * OPAQUE_ALPHA_THRESHOLD):
                        return True
                rect = _window_rect(hwnd)
                if rect is not None:
                    result.append(rect)
            except Exception:
                pass
            return True

        if not user32.EnumWindows(_enum, 0):
            return None
        return result
    except Exception:
        return None


def _macos_covering_rects(screens: Sequence[Rect]) -> list[Rect] | None:
    if sys.platform != "darwin":
        return None
    try:
        import Quartz

        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        infos = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
        if infos is None:
            return None
        own_pid = os.getpid()
        result: list[Rect] = []
        for info in infos:
            try:
                if int(info.get(Quartz.kCGWindowOwnerPID, -1)) == own_pid:
                    continue
                owner = str(info.get(Quartz.kCGWindowOwnerName, "") or "")
                if owner in {"Dock", "Window Server", "SystemUIServer"}:
                    continue
                # Normal application windows are layer 0. Menus, panels and the
                # Dock should not make an otherwise visible desktop count covered.
                if int(info.get(Quartz.kCGWindowLayer, 0) or 0) != 0:
                    continue
                alpha = float(info.get(Quartz.kCGWindowAlpha, 1.0) or 0.0)
                if alpha < OPAQUE_ALPHA_THRESHOLD:
                    continue
                bounds = info.get(Quartz.kCGWindowBounds) or {}
                rect = Rect(
                    float(bounds.get("X", 0.0)),
                    float(bounds.get("Y", 0.0)),
                    float(bounds.get("Width", 0.0)),
                    float(bounds.get("Height", 0.0)),
                )
                if rect.valid() and any(rect.intersects(screen) for screen in screens):
                    result.append(rect)
            except Exception:
                continue
        return result
    except Exception:
        return None


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", ctypes.c_ulong),
        ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


def _linux_x11_covering_rects(screens: Sequence[Rect]) -> list[Rect] | None:
    if not sys.platform.startswith("linux"):
        return None
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        # XWayland only exposes X11 clients and would miss native Wayland windows.
        return None
    if not os.environ.get("DISPLAY"):
        return None
    try:
        xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        xlib.XGetWindowProperty.restype = ctypes.c_int
        xlib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XWindowAttributes)]
        xlib.XGetWindowAttributes.restype = ctypes.c_int
        xlib.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        xlib.XTranslateCoordinates.restype = ctypes.c_int
        xlib.XFree.argtypes = [ctypes.c_void_p]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]

        display = xlib.XOpenDisplay(None)
        if not display:
            return None
        try:
            root = int(xlib.XDefaultRootWindow(display))

            def atom(name: str) -> int:
                return int(xlib.XInternAtom(display, name.encode("ascii"), 1) or 0)

            atoms = {
                name: atom(name)
                for name in (
                    "_NET_CLIENT_LIST_STACKING",
                    "_NET_WM_WINDOW_TYPE",
                    "_NET_WM_WINDOW_TYPE_DESKTOP",
                    "_NET_WM_WINDOW_TYPE_DOCK",
                    "_NET_WM_WINDOW_TYPE_TOOLBAR",
                    "_NET_WM_WINDOW_TYPE_MENU",
                    "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
                    "_NET_WM_WINDOW_TYPE_POPUP_MENU",
                    "_NET_WM_WINDOW_TYPE_TOOLTIP",
                    "_NET_WM_WINDOW_TYPE_NOTIFICATION",
                    "_NET_WM_WINDOW_TYPE_COMBO",
                    "_NET_WM_WINDOW_TYPE_DND",
                    "_NET_WM_STATE",
                    "_NET_WM_STATE_HIDDEN",
                    "_NET_WM_PID",
                    "_NET_WM_DESKTOP",
                    "_NET_CURRENT_DESKTOP",
                    "_NET_WM_WINDOW_OPACITY",
                )
            }
            if not atoms["_NET_CLIENT_LIST_STACKING"]:
                return None

            def property_values(window: int, property_atom: int, limit: int = 4096) -> list[int]:
                if not property_atom:
                    return []
                actual_type = ctypes.c_ulong()
                actual_format = ctypes.c_int()
                nitems = ctypes.c_ulong()
                bytes_after = ctypes.c_ulong()
                data = ctypes.POINTER(ctypes.c_ubyte)()
                status = xlib.XGetWindowProperty(
                    display,
                    ctypes.c_ulong(window),
                    ctypes.c_ulong(property_atom),
                    0,
                    limit,
                    0,
                    0,
                    ctypes.byref(actual_type),
                    ctypes.byref(actual_format),
                    ctypes.byref(nitems),
                    ctypes.byref(bytes_after),
                    ctypes.byref(data),
                )
                if status != 0 or not data or actual_format.value not in (8, 16, 32):
                    if data:
                        xlib.XFree(data)
                    return []
                try:
                    if actual_format.value == 32:
                        ptr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
                    elif actual_format.value == 16:
                        ptr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ushort))
                    else:
                        ptr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte))
                    return [int(ptr[index]) for index in range(int(nitems.value))]
                finally:
                    xlib.XFree(data)

            windows = property_values(root, atoms["_NET_CLIENT_LIST_STACKING"])
            if not windows:
                return []
            own_pid = os.getpid()
            current_desktop_values = property_values(root, atoms["_NET_CURRENT_DESKTOP"], 1)
            current_desktop = current_desktop_values[0] if current_desktop_values else None
            skip_types = {
                atoms[name]
                for name in (
                    "_NET_WM_WINDOW_TYPE_DESKTOP",
                    "_NET_WM_WINDOW_TYPE_DOCK",
                    "_NET_WM_WINDOW_TYPE_TOOLBAR",
                    "_NET_WM_WINDOW_TYPE_MENU",
                    "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
                    "_NET_WM_WINDOW_TYPE_POPUP_MENU",
                    "_NET_WM_WINDOW_TYPE_TOOLTIP",
                    "_NET_WM_WINDOW_TYPE_NOTIFICATION",
                    "_NET_WM_WINDOW_TYPE_COMBO",
                    "_NET_WM_WINDOW_TYPE_DND",
                )
                if atoms[name]
            }
            result: list[Rect] = []
            for window in windows:
                try:
                    pids = property_values(window, atoms["_NET_WM_PID"], 1)
                    if pids and int(pids[0]) == own_pid:
                        continue
                    states = set(property_values(window, atoms["_NET_WM_STATE"], 64))
                    if atoms["_NET_WM_STATE_HIDDEN"] and atoms["_NET_WM_STATE_HIDDEN"] in states:
                        continue
                    types = set(property_values(window, atoms["_NET_WM_WINDOW_TYPE"], 32))
                    if types & skip_types:
                        continue
                    desktops = property_values(window, atoms["_NET_WM_DESKTOP"], 1)
                    if current_desktop is not None and desktops:
                        desktop = int(desktops[0]) & 0xFFFFFFFF
                        if desktop not in (int(current_desktop), 0xFFFFFFFF):
                            continue
                    opacity = property_values(window, atoms["_NET_WM_WINDOW_OPACITY"], 1)
                    if opacity and (int(opacity[0]) & 0xFFFFFFFF) < int(0xFFFFFFFF * OPAQUE_ALPHA_THRESHOLD):
                        continue
                    attrs = _XWindowAttributes()
                    if not xlib.XGetWindowAttributes(display, ctypes.c_ulong(window), ctypes.byref(attrs)):
                        continue
                    if attrs.map_state != 2 or attrs.width <= 1 or attrs.height <= 1:
                        continue
                    x = ctypes.c_int()
                    y = ctypes.c_int()
                    child = ctypes.c_ulong()
                    if not xlib.XTranslateCoordinates(
                        display,
                        ctypes.c_ulong(window),
                        ctypes.c_ulong(root),
                        0,
                        0,
                        ctypes.byref(x),
                        ctypes.byref(y),
                        ctypes.byref(child),
                    ):
                        continue
                    rect = Rect(
                        float(x.value - attrs.border_width),
                        float(y.value - attrs.border_width),
                        float(attrs.width + attrs.border_width * 2),
                        float(attrs.height + attrs.border_width * 2),
                    )
                    if rect.valid() and any(rect.intersects(screen) for screen in screens):
                        result.append(rect)
                except Exception:
                    continue
            return result
        finally:
            xlib.XCloseDisplay(display)
    except Exception:
        return None


_last_probe_at = 0.0
_last_probe_result: bool | None = None


def desktop_is_visible(*, force: bool = False) -> bool | None:
    """Return whether any connected display still shows the desktop.

    ``False`` means every display is at least 95% covered and the HTML page may
    be frozen. ``None`` means the platform cannot be inspected reliably; callers
    must keep rendering rather than guess from keyboard focus.
    """
    global _last_probe_at, _last_probe_result
    now = time.monotonic()
    if not force and now - _last_probe_at < _CACHE_TTL_SECONDS:
        return _last_probe_result
    screens = _screen_rects()
    if not screens:
        result = None
    elif sys.platform.startswith("win"):
        windows = _windows_covering_rects(screens)
        result = None if windows is None else desktop_visible_from_rects(screens, windows)
    elif sys.platform == "darwin":
        windows = _macos_covering_rects(screens)
        result = None if windows is None else desktop_visible_from_rects(screens, windows)
    elif sys.platform.startswith("linux"):
        windows = _linux_x11_covering_rects(screens)
        result = None if windows is None else desktop_visible_from_rects(screens, windows)
    else:
        result = None
    _last_probe_at = now
    _last_probe_result = result
    return result
