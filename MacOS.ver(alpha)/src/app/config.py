from app.version import APP_VERSION, APP_VERSION_FILE, APP_VERSION_TUPLE

# MacOS.ver is now a separated platform project; keep platform flags fixed.
IS_WINDOWS = False
IS_MACOS = True
IS_LINUX = False
APP_NAME = "ShangBackground"

__all__ = ["APP_VERSION", "APP_VERSION_FILE", "APP_VERSION_TUPLE"]
PLATFORM_ID = "macos"
PLATFORM_LABEL = "macOS"
UPDATE_CHECK_ON_STARTUP = True
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
FONT_FAMILY = "PingFang SC"
FONT_EXTENSIONS = (".ttf", ".ttc", ".otf")

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm',)

VIDEO_FILETYPES = [
    ("视频文件", "*.mp4 *.mov *.m4v *.avi *.mkv *.webm"),
]

IMAGE_FILETYPES = [
    ("JPEG 图片", "*.jpg"),
    ("JPEG 图片", "*.jpeg"),
    ("PNG 图片", "*.png"),
    ("BMP 图片", "*.bmp"),
    ("GIF 图片", "*.gif"),
]


def get_video_filetypes(lang_func=None):
    """Return video filetypes with translated descriptions."""
    if lang_func is None:
        return VIDEO_FILETYPES
    return [(lang_func(desc, desc), ext) for desc, ext in VIDEO_FILETYPES]


def is_supported_video_path(path) -> bool:
    """Return whether *path* exists and uses a video extension supported by this platform."""
    try:
        import os

        value = os.path.abspath(os.path.expanduser(str(path or "")))
        return bool(value and os.path.isfile(value) and os.path.splitext(value)[1].lower() in VIDEO_EXTENSIONS)
    except (OSError, TypeError, ValueError):
        return False


DEPENDENCIES = [
    {"module": "PIL", "package": "pillow", "required": True, "desc": "图片读取、缩略图和壁纸生成"},
    {"module": "PySide6", "package": "PySide6-Essentials", "required": True, "desc": "新版 PySide6 图形界面与系统托盘"},
    {"module": "psutil", "package": "psutil", "required": False, "desc": "进程清理与辅助控制"},
    {"module": "AVFoundation", "package": "pyobjc-framework-AVFoundation", "required": False, "desc": "macOS 视频壁纸播放"},
    {"module": "AppKit", "package": "pyobjc-framework-Cocoa", "required": False, "desc": "macOS 视频壁纸窗口层级"},
    {"module": "Quartz", "package": "pyobjc-framework-Quartz", "required": False, "desc": "macOS 视频壁纸桌面层级"},

    # HTML 交互式壁纸依赖 Qt WebEngine 模块；非必须，但缺失时无法加载 HTML 壁纸。
    {"module": "PySide6.QtWebEngineWidgets", "package": "PySide6-Addons", "required": False, "desc": "HTML 交互式壁纸渲染"},
]

# Style map: Chinese key -> Windows WallpaperStyle value
STYLE_MAP = {"填充": 10, "适应": 6, "拉伸": 2, "平铺": 1, "居中": 0}

# Style key lists for UI (order matters for display)
STYLE_KEYS = ["填充", "适应", "拉伸", "居中", "平铺"]

# Mode keys
# 在模式列表中新增 HTML 交互式壁纸。此模式将使用 Qt WebEngine 渲染本地或远程网页。
MODE_KEYS = ["幻灯片放映", "图片", "视频", "纯色", "渐变", "HTML"]

# Canonical internal keys.  The UI may display translated text, but config/core
# logic must keep these Chinese keys for backwards compatibility.
MODE_ALIASES = {
    "幻灯片放映": "幻灯片放映",
    "幻灯片": "幻灯片放映",
    "slideshow": "幻灯片放映",
    "slide show": "幻灯片放映",
    "slides": "幻灯片放映",
    "图片": "图片",
    "视频": "视频",
    "video": "视频",
    "video wallpaper": "视频",
    "单张图片": "图片",
    "image": "图片",
    "picture": "图片",
    "single image": "图片",
    "纯色": "纯色",
    "solid": "纯色",
    "solid color": "纯色",
    "渐变": "渐变",
    "gradient": "渐变",

    # HTML 模式。允许通过英文关键字或“网页”等中文关键字切换到 HTML 模式。
    "html": "HTML",
    "HTML": "HTML",
    "网页": "HTML",
    "web": "HTML",
    "webpage": "HTML",
}
STYLE_ALIASES = {
    "填充": "填充",
    "fill": "填充",
    "zoom": "填充",
    "适应": "适应",
    "fit": "适应",
    "scaled": "适应",
    "拉伸": "拉伸",
    "stretch": "拉伸",
    "stretched": "拉伸",
    "居中": "居中",
    "center": "居中",
    "centered": "居中",
    "平铺": "平铺",
    "tile": "平铺",
    "tiled": "平铺",
}

def _norm_text(value):
    return str(value or "").strip().lower()

def normalize_mode_key(value, default="幻灯片放映"):
    """Return a stable Chinese mode key from Chinese/English UI text or old configs."""
    if value in MODE_KEYS:
        return value
    return MODE_ALIASES.get(_norm_text(value), default)

def normalize_style_key(value, default="填充"):
    """Return a stable Chinese fit/style key from Chinese/English UI text or old configs."""
    if value in STYLE_KEYS:
        return value
    return STYLE_ALIASES.get(_norm_text(value), default)

