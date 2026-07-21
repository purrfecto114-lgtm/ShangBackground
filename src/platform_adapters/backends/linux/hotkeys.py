"""Linux global-hotkey backend and X11 foreground guard."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import os

from platform_adapters.hotkey_bindings import parse_hotkey, to_pynput

_LISTENER = None
_KEYBOARD_OVERRIDE = None


def _is_wayland_session() -> bool:
    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    return session_type == "wayland" or (not session_type and bool(os.environ.get("WAYLAND_DISPLAY")))


def refresh(bindings: Mapping[str, str], dispatch: Callable[[str], None]) -> bool:
    global _LISTENER
    stop()
    if _is_wayland_session():
        return False
    keyboard = _KEYBOARD_OVERRIDE
    if keyboard is None:
        try:
            from pynput import keyboard
        except Exception:
            return False
    combos: dict[str, Callable[[], None]] = {}
    for action, value in bindings.items():
        chord = to_pynput(value, macos=False)
        if chord and chord not in combos:
            combos[chord] = lambda selected=str(action): dispatch(selected)
    if not combos:
        return False
    listener = keyboard.GlobalHotKeys(combos)
    listener.daemon = True
    listener.start()
    _LISTENER = listener
    return True


def stop() -> None:
    global _LISTENER
    listener = _LISTENER
    _LISTENER = None
    if listener is not None:
        listener.stop()


def focus_block_reason(_action: str, binding: str) -> str:
    parsed = parse_hotkey(binding)
    if parsed is None or not parsed.is_focus_sensitive:
        return ""
    if _is_wayland_session():
        return "Wayland 会话未提供可验证的前台窗口上下文"
    context = _x11_active_window_context()
    if context is None:
        return ""
    window_class, pid = context
    own_pid = os.getpid()
    class_text = " ".join(window_class).lower()
    desktop_tokens = (
        "desktop",
        "nautilus-desktop",
        "nemo-desktop",
        "pcmanfm",
        "xfdesktop",
        "plasmashell",
    )
    if pid == own_pid or any(token in class_text for token in desktop_tokens):
        return ""
    return f"当前前台窗口不是桌面({class_text or 'unknown'})，简单热键已保护"


def _x11_active_window_context() -> tuple[tuple[str, ...], int] | None:
    try:
        from Xlib import X, display
        from Xlib.protocol import error

        connection = display.Display()
        try:
            root = connection.screen().root
            active_atom = connection.intern_atom("_NET_ACTIVE_WINDOW")
            active = root.get_full_property(active_atom, X.AnyPropertyType)
            if not active or not active.value or int(active.value[0]) == 0:
                return (("desktop",), 0)
            window = connection.create_resource_object("window", int(active.value[0]))
            try:
                wm_class = tuple(str(item or "") for item in (window.get_wm_class() or ()))
            except error.BadWindow:
                return None
            pid_atom = connection.intern_atom("_NET_WM_PID")
            pid_prop = window.get_full_property(pid_atom, X.AnyPropertyType)
            pid = int(pid_prop.value[0]) if pid_prop and pid_prop.value else 0
            return wm_class, pid
        finally:
            connection.close()
    except Exception:
        return None
