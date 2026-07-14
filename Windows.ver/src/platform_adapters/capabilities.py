"""Runtime feasibility description for the Windows source tree."""
from __future__ import annotations

import importlib.util
import sys


def _has(name: str) -> bool:
    """Safely probe optional modules, including nested modules with a missing parent package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False


def probe_capabilities() -> dict[str, dict[str, object]]:
    webengine = _has("PySide6.QtWebEngineWidgets")
    native = sys.platform.startswith("win")
    return {
        "static_wallpaper": {
            "state": "supported",
            "runtime_ready": native,
            "backend": "IDesktopWallpaper COM (Windows 8+) with SystemParametersInfo fallback",
            "limitations": "Native execution still requires a Windows desktop session.",
        },
        "video_wallpaper": {
            "state": "best_effort",
            "runtime_ready": native,
            "backend": "WorkerW/Progman child window + media player",
            "limitations": "WorkerW discovery and message 0x052C are undocumented Explorer behavior.",
        },
        "html_wallpaper": {
            "state": "best_effort",
            "runtime_ready": native and webengine,
            "backend": "Qt WebEngine embedded in WorkerW",
            "limitations": "Requires full build; WorkerW is undocumented and may change across Explorer releases.",
        },
        "global_hotkeys": {
            "state": "supported",
            "runtime_ready": native,
            "backend": "RegisterHotKey",
            "limitations": "Some key combinations are reserved by Windows or other applications.",
        },
        "tray": {"state": "supported", "runtime_ready": native, "backend": "QSystemTrayIcon", "limitations": "Explorer tray must be available."},
        "autostart": {"state": "supported", "runtime_ready": native, "backend": "per-user Startup folder", "limitations": "Portable app path must remain valid."},
        "single_instance": {"state": "supported", "runtime_ready": native, "backend": "named Win32 mutex", "limitations": "Per-user/session behavior depends on mutex namespace."},
        "multi_monitor_static": {"state": "supported", "runtime_ready": native, "backend": "IDesktopWallpaper monitor API", "limitations": "Legacy fallback cannot provide the same per-monitor control."},
    }
