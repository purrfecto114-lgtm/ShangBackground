"""Runtime feasibility description for the Windows source tree."""
from __future__ import annotations

import sys


def probe_capabilities() -> dict[str, dict[str, object]]:
    from platform_adapters.html_runtime import (
        missing_runtime_modules,
        runtime_backend_label,
        select_html_runtime,
    )

    html_runtime = select_html_runtime()
    html_ready = html_runtime.name != "disabled" and not missing_runtime_modules(html_runtime, platform="windows")
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
            "runtime_ready": native and html_ready,
            "backend": runtime_backend_label(html_runtime, "windows"),
            "limitations": "Requires the selected HTML runtime; WorkerW is undocumented and may change across Explorer releases.",
        },
        "global_hotkeys": {
            "state": "supported",
            "runtime_ready": native,
            "backend": "RegisterHotKey + foreground focus guard",
            "limitations": "Some key combinations are reserved by Windows or other applications; single-modifier bindings are guarded outside the desktop.",
        },
        "mouse_through": {
            "state": "supported",
            "runtime_ready": native,
            "backend": "WorkerW window styles and hit-test/input transparency",
            "limitations": "HTML mouse-through is configurable; video wallpaper remains mouse-transparent by design.",
        },
        "tray": {"state": "supported", "runtime_ready": native, "backend": "QSystemTrayIcon", "limitations": "Explorer tray must be available."},
        "autostart": {"state": "supported", "runtime_ready": native, "backend": "per-user Startup folder", "limitations": "Portable app path must remain valid."},
        "single_instance": {"state": "supported", "runtime_ready": native, "backend": "per-user file lock + authenticated QLocalServer IPC; Local mutex fallback", "limitations": "Fast User Switching and session-bound endpoint behavior require native validation."},
        "multi_monitor_static": {"state": "supported", "runtime_ready": native, "backend": "IDesktopWallpaper monitor API", "limitations": "Legacy fallback cannot provide the same per-monitor control."},
    }
