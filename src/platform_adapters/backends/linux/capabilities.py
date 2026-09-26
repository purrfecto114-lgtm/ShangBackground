"""Runtime feasibility probing for Linux desktop sessions."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import Callable

from platform_adapters.backends.linux.session import detect_session_type, session_bus_available


def _has(name: str) -> bool:
    """Safely probe optional modules, including nested modules with a missing parent package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False




def _libmpv_ready() -> bool:
    try:
        from app.libmpv_runtime import runtime_available

        return bool(runtime_available())
    except Exception:
        return False

def _tokens(env: dict[str, str]) -> str:
    return " ".join(filter(None, (env.get("XDG_CURRENT_DESKTOP", ""), env.get("XDG_SESSION_DESKTOP", ""), env.get("DESKTOP_SESSION", "")))).lower()


def _layer_shell_session(env: dict[str, str]) -> bool:
    """mpvpaper 官方定位的 wlroots 系合成器会话（显式 socket 或桌面令牌证据）。

    v1.6.2 审查修正：kde/plasma 令牌不再计入。mpvpaper 以 wlroots 为依赖、
    面向 Sway 等合成器；KWin 的 layer-shell 实现未经独立探针验证前，
    KDE 会话必须走 ``_kde_session`` 的显式未验证分支。
    """
    tokens = _tokens(env)
    if any(env.get(name) for name in ("SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE", "WAYFIRE_SOCKET")):
        return True
    return any(name in tokens for name in ("sway", "hyprland", "wayfire", "river", "wlroots"))


def _kde_session(env: dict[str, str]) -> bool:
    """KDE/Plasma 会话令牌检测（与静态壁纸判定同源）。"""
    tokens = _tokens(env)
    return "kde" in tokens or "plasma" in tokens


def probe_capabilities(env: dict[str, str] | None = None, which: Callable[[str], str | None] = shutil.which) -> dict[str, dict[str, object]]:
    env = dict(os.environ if env is None else env)
    session = detect_session_type(env)
    desktop = _tokens(env) or "unknown"
    from platform_adapters.html_runtime import (
        missing_runtime_modules,
        runtime_backend_label,
        select_html_runtime,
    )

    html_runtime = select_html_runtime()
    html_dependencies_ready = html_runtime.name != "disabled" and not missing_runtime_modules(html_runtime, platform="linux")
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
        wlroots = _layer_shell_session(env)
        kde = _kde_session(env)
        mpvpaper = bool(which("mpvpaper"))
        video_limitations = "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol."
        if wlroots:
            # mpvpaper 官方支持的平台（sway/hyprland/wayfire/river 等）。
            video_ready = mpvpaper
            video_state = "best_effort"
            video_backend = "mpvpaper layer-shell (wlroots compositors)"
        elif kde:
            # v1.6.2 审查必须修复项：KDE/KWin 会话不宣称 runtime_ready。
            # mpvpaper 面向 wlroots 合成器，KWin 的 layer-shell 兼容性
            # 未经独立探针验证；best_effort 仅表示“可用实验开关显式尝试”，
            # 不得被 UI 或发布门禁当作已支持。
            video_ready = False
            video_state = "best_effort" if mpvpaper else "unsupported"
            video_backend = (
                "mpvpaper (experimental, untested on KWin; opt-in via SHANGBACKGROUND_ALLOW_MPVPAPER=1)"
                if mpvpaper
                else "no supported KDE/KWin video wallpaper backend"
            )
            video_limitations += (
                " KDE/KWin sessions are NOT auto-marked ready: mpvpaper targets wlroots"
                " compositors and KWin compatibility is untested; set"
                " SHANGBACKGROUND_ALLOW_MPVPAPER=1 to opt in explicitly."
            )
        else:
            video_ready = False
            video_state = "unsupported"
            video_backend = "no compatible Wayland desktop-layer backend"
        html_state = "unsupported"
        html_ready = False
        portal_module = _has("dbus_next")
        portal_bus = session_bus_available(env)
        hotkey_state = "best_effort"
        hotkey_ready = portal_module and portal_bus
    elif session == "x11":
        xwinwrap = bool(which("xwinwrap"))
        embedded = _libmpv_ready()
        external = bool(which("mpv"))
        video_ready = xwinwrap and bool(embedded or external)
        video_state = "best_effort"
        video_limitations = "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol."
        if embedded:
            video_backend = "xwinwrap + direct libmpv"
        elif external:
            video_backend = "xwinwrap + mpv"
        else:
            video_backend = "xwinwrap requires libmpv or mpv"
        html_state = "best_effort"
        html_ready = html_dependencies_ready
        hotkey_state = "best_effort"
        hotkey_ready = pynput
    else:
        video_ready = False
        video_state = "unavailable"
        video_backend = "no graphical session detected"
        video_limitations = "X11 uses third-party embedding; Wayland requires a compositor-specific desktop-layer protocol."
        html_state = "unavailable"
        html_ready = False
        hotkey_state = "unavailable"
        hotkey_ready = False

    autostart_dir = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"
    return {
        "static_wallpaper": {"state": "supported" if static_ready else "best_effort", "runtime_ready": static_ready, "backend": static_backend, "limitations": f"Desktop/session detected: {desktop}/{session}; support is desktop-environment specific."},
        "video_wallpaper": {"state": video_state, "runtime_ready": video_ready, "backend": video_backend, "limitations": video_limitations},
        "html_wallpaper": {"state": html_state, "runtime_ready": html_ready, "backend": runtime_backend_label(html_runtime, "linux") if session == "x11" else "none", "limitations": "The current implementation is X11-only and is not a Wayland layer-shell client."},
        "global_hotkeys": {"state": hotkey_state, "runtime_ready": hotkey_ready, "backend": "pynput/X11 + active-window guard" if session == "x11" else "XDG GlobalShortcuts portal v1/v2 via dbus-next", "limitations": "Single-modifier X11 bindings are guarded outside desktop windows; Wayland registration requires user consent and a distribution-provided portal backend."},
        "mouse_through": {"state": "best_effort" if session == "x11" else "unsupported", "runtime_ready": session == "x11", "backend": "X11 Shape input region" if session == "x11" else "none", "limitations": "The X11 HTML window supports input-region toggling; the current Wayland backend cannot request desktop-layer input transparency."},
        "tray": {"state": "best_effort", "runtime_ready": True, "backend": "QSystemTrayIcon / desktop status notifier", "limitations": "Availability depends on the desktop shell and tray extension."},
        "autostart": {"state": "supported", "runtime_ready": bool(autostart_dir.parent.exists()), "backend": "XDG ~/.config/autostart desktop entry", "limitations": "Starts after login in desktop environments implementing the XDG autostart specification."},
        "single_instance": {"state": "supported", "runtime_ready": True, "backend": "per-user file lock + authenticated QLocalServer IPC", "limitations": "Network/home filesystems with unusual locking semantics require validation."},
        "multi_monitor_static": {"state": "best_effort", "runtime_ready": static_ready, "backend": static_backend, "limitations": "Behavior and per-monitor selection vary by GNOME/KDE/XFCE and their versions."},
    }
