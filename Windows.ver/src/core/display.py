"""
显示器分辨率检测工具 - Windows 版。

策略：
1. 优先使用 GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN)，拿真实主屏像素。
2. 若已在 PySide6 GUI 中运行，可传入 QApplication，用 primaryScreen().geometry()。
3. 检测失败时回退到 DEFAULT_RESOLUTION。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

DEFAULT_RESOLUTION = "1920x1080"
MIN_WIDTH = 800
MIN_HEIGHT = 600
MAX_WIDTH = 16384
MAX_HEIGHT = 16384


@dataclass(frozen=True)
class ResolutionResult:
    resolution: str
    width: int
    height: int
    source: str
    detected: bool


def _normalize(width: object, height: object) -> Optional[Tuple[int, int]]:
    try:
        w = int(width)
        h = int(height)
    except Exception:
        return None
    if not (MIN_WIDTH <= w <= MAX_WIDTH and MIN_HEIGHT <= h <= MAX_HEIGHT):
        return None
    return w, h


def resolution_to_tuple(value: str | None) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    text = str(value).lower().strip().replace("*", "x")
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    return _normalize(left, right)


def tuple_to_resolution(size: Tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


def detect_with_windows_api() -> Optional[Tuple[int, int]]:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        return _normalize(user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    except Exception:
        return None


def detect_with_pyside6(app=None) -> Optional[Tuple[int, int]]:
    try:
        if app is None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        if app is None:
            return None
        screen = app.primaryScreen()
        if screen is None:
            return None
        geo = screen.geometry()
        dpr = float(screen.devicePixelRatio() or 1.0)
        return _normalize(round(geo.width() * dpr), round(geo.height() * dpr))
    except Exception:
        return None


def get_system_resolution(app=None, fallback: str = DEFAULT_RESOLUTION) -> ResolutionResult:
    for source, fn in (
        ("windows", detect_with_windows_api),
        ("pyside6", lambda: detect_with_pyside6(app)),
    ):
        size = fn()
        if size:
            return ResolutionResult(tuple_to_resolution(size), size[0], size[1], source, True)
    fb = resolution_to_tuple(fallback) or resolution_to_tuple(DEFAULT_RESOLUTION) or (1920, 1080)
    return ResolutionResult(tuple_to_resolution(fb), fb[0], fb[1], "fallback", False)


def choose_resolution(requested: str | None = "auto", app=None, fallback: str = DEFAULT_RESOLUTION) -> ResolutionResult:
    """解析请求分辨率。

    requested 为 auto/system/detect/空值时检测系统分辨率；具体 `宽x高` 则直接使用；非法值回退。
    """
    if requested is None or str(requested).strip().lower() in {"", "auto", "system", "detect", "native"}:
        return get_system_resolution(app=app, fallback=fallback)
    size = resolution_to_tuple(str(requested))
    if size:
        return ResolutionResult(tuple_to_resolution(size), size[0], size[1], "requested", True)
    fb = resolution_to_tuple(fallback) or (1920, 1080)
    return ResolutionResult(tuple_to_resolution(fb), fb[0], fb[1], "fallback", False)
