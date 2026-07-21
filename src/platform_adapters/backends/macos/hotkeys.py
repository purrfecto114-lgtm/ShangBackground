"""macOS global-hotkey backend and frontmost-application guard."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import os

from platform_adapters.hotkey_bindings import parse_hotkey, to_pynput

_LISTENER = None
_KEYBOARD_OVERRIDE = None


def refresh(bindings: Mapping[str, str], dispatch: Callable[[str], None]) -> bool:
    global _LISTENER
    stop()
    keyboard = _KEYBOARD_OVERRIDE
    if keyboard is None:
        try:
            from pynput import keyboard
        except Exception:
            return False
    combos: dict[str, Callable[[], None]] = {}
    for action, value in bindings.items():
        chord = to_pynput(value, macos=True)
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
    context = _frontmost_application_context()
    if context is None:
        return ""
    name, bundle_id, pid = context
    if pid == os.getpid() or bundle_id in {"com.apple.finder", "com.apple.dock"}:
        return ""
    return f"当前前台应用不是 Finder({name or bundle_id or 'unknown'})，简单热键已保护"


def _frontmost_application_context() -> tuple[str, str, int] | None:
    try:
        from AppKit import NSWorkspace

        application = NSWorkspace.sharedWorkspace().frontmostApplication()
        if application is None:
            return None
        return (
            str(application.localizedName() or ""),
            str(application.bundleIdentifier() or ""),
            int(application.processIdentifier()),
        )
    except Exception:
        return None
