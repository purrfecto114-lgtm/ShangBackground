import sys


IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
APP_NAME = "ShangBackground"

UI_BG = "#f6f8fb"
UI_PANEL = "#ffffff"
UI_ACCENT = "#12c7b7"
UI_ACCENT_DARK = "#0f766e"
UI_TEXT = "#1f2937"
UI_MUTED = "#6b7280"
UI_BORDER = "#d8dee9"
FONT_FAMILY = "Microsoft YaHei UI" if IS_WINDOWS else ("PingFang SC" if IS_MACOS else "Noto Sans CJK SC")

IMAGE_FILETYPES = [
    ("JPEG 图片", "*.jpg"),
    ("JPEG 图片", "*.jpeg"),
    ("PNG 图片", "*.png"),
    ("BMP 图片", "*.bmp"),
    ("GIF 图片", "*.gif"),
]

DEPENDENCIES = [
    {"module": "PIL", "package": "pillow", "required": True, "desc": "图片读取、缩略图和壁纸生成"},
    {"module": "requests", "package": "requests", "required": False, "desc": "版本检查和使用统计"},
    {"module": "numpy", "package": "numpy", "required": False, "desc": "更快的渐变/转场生成"},
    {"module": "PySide6", "package": "PySide6", "required": True, "desc": "新版 PySide6 图形界面与系统托盘"},
    {"module": "pystray", "package": "pystray", "required": False, "desc": "旧版 Tkinter 兼容托盘"},
    {"module": "httpx", "package": "httpx", "required": False, "desc": "Bing 壁纸 API 下载"},
    {"module": "psutil", "package": "psutil", "required": False, "desc": "进程清理与辅助控制"},
]

STYLE_MAP = {"填充": 10, "适应": 6, "拉伸": 2, "平铺": 1, "居中": 0}