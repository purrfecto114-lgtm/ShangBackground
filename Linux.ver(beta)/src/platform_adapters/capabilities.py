"""Runtime feasibility probing for Linux desktop sessions."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import Callable


def _has(name: str) -> bool:
    """Safely probe optional modules, including nested modules with a missing parent package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False


def _tokens(env: dict[str, str]) -> str:
    return " ".join(filter(None, (env.get("XDG_CURRENT_DESKTOP", ""), env.get("XDG_SESSION_DESKTOP", ""), env.get("DESKTOP_SESSION", "")))).lower()


def _layer_shell_session(env: dict[str, str]) -> bool:
    tokens = _tokens(env)
    if any(env.get(name) for name in ("SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE", "WAYFIRE_SOCKET")):
        return True
    return any(name in tokens for name in ("sway", "hyprland", "wayfire", "river", "wlroots"))


def probe_capabilities(env: dict[str, str] | None = None, which: Callable[[str], str | None] = shutil.which) -> dict[str, dict[str, object]]:
    env = dict(os.environ if env is None else env)
    session = (env.get("XDG_SESSION_TYPE") or ("wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else "unknown")).lower()
    desktop = _tokens(env) or "unknown"
    webengine = _has("PySide6.QtWebEngineWidgets")
    pynput = _has("pynput")

    static_backend = ""
    static_ready = False
    if "kde" in desktop or "plasma" in desktop:
        static_backend = "plasma-apply-wallpaperimage or qdbus6/qdbus Plasma scripting"
        static_ready = bool(which("plasma-apply-wallpaperimage") or which("qdbus6") or which("qdbus"))
    elif "gnome" in desktop or "unity" in desktop or "cinnamon" in desktop:
        static_backend = "GSettings org.gnome.desktop.background"
        static_ready = bool(which("gsettings"))
    elif "xfce" in desktop:
        static_backend = "xfconf-query"
        static_ready = bool(which("xfconf-query"))
    elif "lxde" in desktop or "pcmanfm" in desktop:
        static_backend = "pcmanfm"
        static_ready = bool(which("pcmanfm"))
    else:
        static_backend = "desktop-specific command; X11 fallback feh/nitrogen"
        static_ready = bool(which("feh") or which("nitrogen")) if session == "x11" else False

    if session == "wayland":
        layer = _layer_shell_session(env)
        video_ready = layer and bool(which("mpvpaper"))
        video_state = "best_effort" if layer else "unsupported"
        video_backend = "mpvpaper layer-shell" if layer else "no generic GNOME/KDE Wayland backend"
        html_state = "unsupported"
        html_ready = False
        hotkey_state = "unsupported"
        hotkey_ready = False
    elif session == "x11":
        video_ready = bool(which("xwinwrap") and which("mpv"))
        video_state = "best_effort"
        video_backend = "xwinwrap + mpv"
        html_state = "best_effort"
        html_ready = webengine
        hotkey_state = "best_effort"
        hotkey_ready = pynput
    else:
        video_ready = False
        video_state = "unavailable"
        video_backend = "no graphical session detected"
        html_state = "unavailable"
        html_ready = False
        hotkey_state = "unavailable"
        hotkey_ready = False

    autostart_dir = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"
    return {
        "static_wallpaper": {"state": "supported" if static_ready else "best_effort", "runtime_ready": static_ready, "backend": static_backend, "limitations": f"Desktop/session detected: {desktop}/{session}; support is desktop-environment specific."},
        "video_wallpaper": {"state": video_state, "runtime_ready": video_ready, "backend": video_backend, "limitations": "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol."},
        "html_wallpaper": {"state": html_state, "runtime_ready": html_ready, "backend": "Qt WebEngine bottom window on X11" if session == "x11" else "none", "limitations": "The current implementation is not a Wayland layer-shell client and cannot be positioned as a wallpaper there."},
        "global_hotkeys": {"state": hotkey_state, "runtime_ready": hotkey_ready, "backend": "pynput/X11" if session == "x11" else "XDG GlobalShortcuts portal not implemented", "limitations": "Wayland intentionally blocks generic global keyboard hooks."},
        "tray": {"state": "best_effort", "runtime_ready": True, "backend": "QSystemTrayIcon / desktop status notifier", "limitations": "Availability depends on the desktop shell and tray extension."},
        "autostart": {"state": "supported", "runtime_ready": bool(autostart_dir.parent.exists()), "backend": "XDG ~/.config/autostart desktop entry", "limitations": "Starts after login in desktop environments implementing the XDG autostart specification."},
        "single_instance": {"state": "supported", "runtime_ready": True, "backend": "per-user lock/state file", "limitations": "Network/home filesystems with unusual locking semantics require validation."},
        "multi_monitor_static": {"state": "best_effort", "runtime_ready": static_ready, "backend": static_backend, "limitations": "Behavior and per-monitor selection vary by GNOME/KDE/XFCE and their versions."},
    }
