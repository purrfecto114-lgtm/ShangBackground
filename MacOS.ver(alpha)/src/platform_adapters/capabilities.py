"""Runtime feasibility description for the macOS source tree."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _has(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def probe_capabilities() -> dict[str, dict[str, object]]:
    native = sys.platform == "darwin"
    cocoa = all(_has(name) for name in ("objc", "AppKit", "Quartz"))
    av = cocoa and _has("AVFoundation")
    webengine = _has("PySide6.QtWebEngineWidgets")
    pynput = _has("pynput")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    return {
        "static_wallpaper": {"state": "supported", "runtime_ready": native and cocoa, "backend": "NSWorkspace setDesktopImageURL:forScreen:options:error:", "limitations": "Tile has no stable NSWorkspace option and is degraded to fit."},
        "video_wallpaper": {"state": "best_effort", "runtime_ready": native and av, "backend": "AVPlayerLayer in desktop-level NSWindow per NSScreen", "limitations": "Desktop-level window behavior across Spaces/full-screen apps requires real-device validation."},
        "html_wallpaper": {"state": "best_effort", "runtime_ready": native and cocoa and webengine, "backend": "Qt WebEngine bridged to desktop-level NSWindow", "limitations": "Multi-display Spaces and Finder restart behavior require real-device validation."},
        "global_hotkeys": {"state": "best_effort", "runtime_ready": native and pynput, "backend": "pynput event tap", "limitations": "Requires user-granted Input Monitoring permission; sandbox/distribution policy must be validated."},
        "tray": {"state": "supported", "runtime_ready": native, "backend": "QSystemTrayIcon / NSStatusItem integration", "limitations": "Menu-bar-only behavior varies with application activation policy."},
        "autostart": {"state": "best_effort", "runtime_ready": native and launch_agents.parent.exists(), "backend": "per-user LaunchAgent", "limitations": "The executable path must remain stable; signed/notarized app packaging still needs real-device validation."},
        "single_instance": {"state": "supported", "runtime_ready": native, "backend": "per-user lock/state file", "limitations": "Must be tested across Fast User Switching sessions."},
        "multi_monitor_static": {"state": "supported", "runtime_ready": native and cocoa, "backend": "iterate NSScreen with NSWorkspace", "limitations": "Applies the same image to every screen rather than independent image selection."},
    }
