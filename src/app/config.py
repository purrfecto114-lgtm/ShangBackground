from __future__ import annotations

import os
import sys

from app.version import APP_VERSION, APP_VERSION_FILE, APP_VERSION_TUPLE
from app.build_features import is_feature_enabled


def _detect_platform() -> str:
    """Return the active target platform.

    Production builds always follow the host OS.  The environment override is
    intentionally explicit and is used only by repository smoke tests and
    cross-platform build dry-runs; it never guesses or silently falls back.
    """
    # Cross-platform smoke tests and build dry-runs need to exercise another
    # backend on the current host. Packaged applications must never let an
    # environment variable select a foreign native backend.
    packaged = bool(getattr(sys, "frozen", False) or globals().get("__compiled__") is not None)
    override = "" if packaged else str(os.environ.get("SHANGBACKGROUND_PLATFORM_OVERRIDE", "")).strip().lower()
    aliases = {"win": "windows", "win32": "windows", "darwin": "macos", "mac": "macos"}
    override = aliases.get(override, override)
    if override:
        if override not in {"windows", "linux", "macos"}:
            raise RuntimeError(f"Invalid SHANGBACKGROUND_PLATFORM_OVERRIDE: {override!r}")
        return override
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


PLATFORM_ID = _detect_platform()
IS_WINDOWS = PLATFORM_ID == "windows"
IS_LINUX = PLATFORM_ID == "linux"
IS_MACOS = PLATFORM_ID == "macos"
PLATFORM_LABEL = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}[PLATFORM_ID]
APP_NAME = "ShangBackground"

__all__ = ["APP_VERSION", "APP_VERSION_FILE", "APP_VERSION_TUPLE"]
UPDATE_CHECK_ON_STARTUP = is_feature_enabled("updates")
UPDATE_CHECK_STARTUP_DELAY_MS = 1800
UPDATE_CHECK_TIMEOUT_SECONDS = 8

UI_BG = "#f6f8fb"
UI_PANEL = "#ffffff"
DEFAULT_THEME_COLOR = "#ffffff"
DEFAULT_SOLID_COLOR = "#ffffff"
DEFAULT_GRADIENT_COLOR2 = "#ffffff"
UI_ACCENT = "#12c7b7"
UI_ACCENT_DARK = "#0f766e"
UI_TEXT = "#1f2937"
UI_MUTED = "#6b7280"
UI_BORDER = "#d8dee9"
FONT_FAMILY = {
    "windows": "Microsoft YaHei UI",
    "linux": "Noto Sans CJK SC",
    "macos": "PingFang SC",
}[PLATFORM_ID]
FONT_EXTENSIONS = (".ttf", ".ttc", ".otf")

VIDEO_EXTENSIONS = (
    ((".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv")
     if IS_WINDOWS else (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"))
    if is_feature_enabled("video") else ()
)
VIDEO_FILETYPES = [("视频文件", "*" + " *".join(VIDEO_EXTENSIONS))]
IMAGE_FILETYPES = [
    ("JPEG 图片", "*.jpg"),
    ("JPEG 图片", "*.jpeg"),
    ("PNG 图片", "*.png"),
    ("BMP 图片", "*.bmp"),
    ("GIF 图片", "*.gif"),
]


def get_video_filetypes(lang_func=None):
    if lang_func is None:
        return VIDEO_FILETYPES
    return [(lang_func(desc, desc), ext) for desc, ext in VIDEO_FILETYPES]


def is_supported_video_path(path) -> bool:
    try:
        value = os.path.abspath(os.path.expanduser(str(path or "")))
        return bool(value and os.path.isfile(value) and os.path.splitext(value)[1].lower() in VIDEO_EXTENSIONS)
    except (OSError, TypeError, ValueError):
        return False


DEPENDENCIES = [
    {"module": "PIL", "package": "pillow", "required": True, "desc": "图片读取、缩略图和壁纸生成"},
    {"module": "PySide6", "package": "PySide6-Essentials", "required": True, "desc": "新版 PySide6 图形界面与系统托盘"},
    {"module": "psutil", "package": "psutil", "required": False, "desc": "进程清理与辅助控制"},
]
if not IS_WINDOWS and is_feature_enabled("hotkeys"):
    DEPENDENCIES.append(
        {"module": "pynput", "package": "pynput", "required": False, "desc": "可选的系统级全局热键"}
    )
if IS_MACOS:
    DEPENDENCIES.extend([
        {"module": "AppKit", "package": "pyobjc-framework-Cocoa", "required": False, "desc": "macOS 原生桌面集成"},
        {"module": "Quartz", "package": "pyobjc-framework-Quartz", "required": False, "desc": "macOS 桌面窗口层级"},
    ])
    if is_feature_enabled("video"):
        DEPENDENCIES.append(
            {"module": "AVFoundation", "package": "pyobjc-framework-AVFoundation", "required": False, "desc": "macOS 视频壁纸播放"}
        )
if is_feature_enabled("html"):
    DEPENDENCIES.append(
        {
            "module": "webview",
            "package": "pywebview[gtk]" if IS_LINUX else "pywebview",
            "required": False,
            "desc": "系统原生 WebView HTML 壁纸（WebView2/WKWebView/WebKitGTK）",
        }
    )
    if IS_LINUX:
        DEPENDENCIES.append(
            {
                "module": "gi",
                "package": "PyGObject",
                "required": False,
                "desc": "Linux GTK/WebKitGTK Python 绑定；系统还需安装 WebKitGTK typelib",
            }
        )

STYLE_MAP = {"填充": 10, "适应": 6, "拉伸": 2, "平铺": 1, "居中": 0}
STYLE_KEYS = ["填充", "适应", "拉伸", "居中", "平铺"]
MODE_KEYS = ["幻灯片放映", "图片", "纯色", "渐变"]
if is_feature_enabled("video"):
    MODE_KEYS.insert(2, "视频")
if is_feature_enabled("html"):
    MODE_KEYS.append("HTML")
MODE_ALIASES = {
    "幻灯片放映": "幻灯片放映", "幻灯片": "幻灯片放映", "slideshow": "幻灯片放映",
    "slide show": "幻灯片放映", "slides": "幻灯片放映", "图片": "图片", "单张图片": "图片",
    "image": "图片", "picture": "图片", "single image": "图片", "视频": "视频", "video": "视频",
    "video wallpaper": "视频", "纯色": "纯色", "solid": "纯色", "solid color": "纯色",
    "渐变": "渐变", "gradient": "渐变", "html": "HTML", "HTML": "HTML", "网页": "HTML",
    "web": "HTML", "webpage": "HTML",
}
STYLE_ALIASES = {
    "填充": "填充", "fill": "填充", "zoom": "填充", "适应": "适应", "fit": "适应",
    "scaled": "适应", "拉伸": "拉伸", "stretch": "拉伸", "stretched": "拉伸",
    "居中": "居中", "center": "居中", "centered": "居中", "平铺": "平铺",
    "tile": "平铺", "tiled": "平铺",
}


def _norm_text(value):
    return str(value or "").strip().lower()


def normalize_mode_key(value, default="幻灯片放映"):
    if value in MODE_KEYS:
        return value
    candidate = MODE_ALIASES.get(_norm_text(value), default)
    if candidate in MODE_KEYS:
        return candidate
    if default in MODE_KEYS:
        return default
    return MODE_KEYS[0] if MODE_KEYS else "图片"


def normalize_style_key(value, default="填充"):
    if value in STYLE_KEYS:
        return value
    return STYLE_ALIASES.get(_norm_text(value), default)
