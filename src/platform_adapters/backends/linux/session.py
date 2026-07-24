"""Shared Linux desktop-session detection helpers.

Desktop launchers do not consistently export ``XDG_SESSION_TYPE``.  All Linux
backends must therefore use the same fallback order so capability probing and
runtime dispatch cannot disagree about X11 versus Wayland.
"""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping


def detect_session_type(env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    explicit = str(values.get("XDG_SESSION_TYPE", "") or "").strip().lower()
    if explicit in {"wayland", "x11"}:
        return explicit
    if values.get("WAYLAND_DISPLAY"):
        return "wayland"
    if values.get("DISPLAY"):
        return "x11"
    return explicit or "unknown"


def is_wayland_session(env: Mapping[str, str] | None = None) -> bool:
    return detect_session_type(env) == "wayland"


def session_bus_available(env: Mapping[str, str] | None = None) -> bool:
    """Return whether a user D-Bus session endpoint is discoverable.

    ``dbus-next`` can connect through the conventional
    ``$XDG_RUNTIME_DIR/bus`` socket even when ``DBUS_SESSION_BUS_ADDRESS`` was
    not inherited by an autostart launcher.
    """
    values = os.environ if env is None else env
    if str(values.get("DBUS_SESSION_BUS_ADDRESS", "") or "").strip():
        return True
    runtime_dir = str(values.get("XDG_RUNTIME_DIR", "") or "").strip()
    return bool(runtime_dir and (Path(runtime_dir) / "bus").exists())
