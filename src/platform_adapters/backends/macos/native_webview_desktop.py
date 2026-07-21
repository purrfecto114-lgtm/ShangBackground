"""Desktop-layer integration for pywebview's native NSWindow/WKWebView."""

from __future__ import annotations

import sys
from typing import Any


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("macOS desktop integration is unavailable on this host")


def environment_error() -> str:
    """Return an actionable platform-runtime error for dependency probes."""
    if sys.platform != "darwin":
        return "macOS WKWebView probing is unavailable on this host"
    return ""


def native_window_id(native: Any) -> int:
    _require_macos()
    return int(native.windowNumber())


def virtual_geometry() -> tuple[int, int, int, int]:
    _require_macos()
    import AppKit  # pyright: ignore[reportMissingImports]  # macOS-only binding

    screens = list(AppKit.NSScreen.screens())
    if not screens:
        return (0, 0, 1920, 1080)
    min_x = min(float(screen.frame().origin.x) for screen in screens)
    min_y = min(float(screen.frame().origin.y) for screen in screens)
    max_x = max(float(screen.frame().origin.x + screen.frame().size.width) for screen in screens)
    max_y = max(float(screen.frame().origin.y + screen.frame().size.height) for screen in screens)
    return (int(min_x), int(min_y), max(1, int(max_x - min_x)), max(1, int(max_y - min_y)))


def set_mouse_through(native: Any, enabled: bool) -> bool:
    _require_macos()
    native.setIgnoresMouseEvents_(bool(enabled))
    return True


def set_render_visible(native: Any, visible: bool) -> bool:
    """Hide rendering while covered without changing desktop window policy."""
    if visible:
        native.orderFrontRegardless()
    else:
        native.orderOut_(None)
    return True


def configure(native: Any, mouse_through: bool) -> bool:
    _require_macos()
    import AppKit  # pyright: ignore[reportMissingImports]  # macOS-only binding
    import Quartz  # pyright: ignore[reportMissingImports]  # macOS-only binding

    icon_level = int(Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey))
    native.setLevel_(icon_level - 1)
    behavior = 0
    for name in (
        "NSWindowCollectionBehaviorCanJoinAllSpaces",
        "NSWindowCollectionBehaviorStationary",
        "NSWindowCollectionBehaviorIgnoresCycle",
        "NSWindowCollectionBehaviorFullScreenAuxiliary",
    ):
        behavior |= int(getattr(AppKit, name, 0))
    if behavior:
        native.setCollectionBehavior_(behavior)
    set_mouse_through(native, mouse_through)
    native.setHidesOnDeactivate_(False)
    native.setCanHide_(False)
    native.setOpaque_(False)
    native.orderFrontRegardless()
    return True
