"""Runtime feasibility description for the macOS source tree."""
from __future__ import annotations

import sys
from pathlib import Path


def probe_capabilities() -> dict[str, dict[str, object]]:
    import importlib.util

    from platform_adapters.html_runtime import (
        missing_runtime_modules,
        runtime_backend_label,
        select_html_runtime,
    )

    def has(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
            return False

    native = sys.platform == "darwin"
    cocoa = all(has(name) for name in ("objc", "AppKit", "Quartz"))
    av = cocoa and has("AVFoundation")
    html_runtime = select_html_runtime()
    html_ready = html_runtime.name != "disabled" and not missing_runtime_modules(html_runtime, platform="macos")
    pynput = has("pynput")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    return {
        "static_wallpaper": {"state": "supported", "runtime_ready": native and cocoa, "backend": "NSWorkspace setDesktopImageURL:forScreen:options:error:", "limitations": "Tile has no stable NSWorkspace option and is degraded to fit."},
        "video_wallpaper": {"state": "best_effort", "runtime_ready": native and av, "backend": "AVPlayerLayer in desktop-level NSWindow per NSScreen", "limitations": "Desktop-level window behavior across Spaces/full-screen apps requires real-device validation."},
        "html_wallpaper": {"state": "best_effort", "runtime_ready": native and cocoa and html_ready, "backend": runtime_backend_label(html_runtime, "macos"), "limitations": "Multi-display Spaces and Finder restart behavior require real-device validation."},
        "global_hotkeys": {"state": "best_effort", "runtime_ready": native and pynput, "backend": "pynput event tap + NSWorkspace frontmost-app guard", "limitations": "Requires user-granted Input Monitoring permission; single-modifier bindings are guarded outside Finder/Dock; sandbox/distribution policy must be validated."},
        "mouse_through": {"state": "supported", "runtime_ready": native and cocoa, "backend": "NSWindow setIgnoresMouseEvents:", "limitations": "HTML mouse-through is configurable; video wallpaper remains mouse-transparent by design."},
        "tray": {"state": "supported", "runtime_ready": native, "backend": "QSystemTrayIcon / NSStatusItem integration", "limitations": "Menu-bar-only behavior varies with application activation policy."},
        "autostart": {"state": "best_effort", "runtime_ready": native and launch_agents.parent.exists(), "backend": "per-user LaunchAgent", "limitations": "The executable path must remain stable; signed/notarized app packaging still needs real-device validation."},
        "single_instance": {"state": "supported", "runtime_ready": native, "backend": "per-user file lock + authenticated QLocalServer IPC", "limitations": "Must be tested across Fast User Switching sessions."},
        "multi_monitor_static": {"state": "supported", "runtime_ready": native and cocoa, "backend": "iterate NSScreen with NSWorkspace", "limitations": "Applies the same image to every screen rather than independent image selection."},
    }
