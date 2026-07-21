"""Shared parsing helpers for platform global-hotkey backends."""
from __future__ import annotations

from dataclasses import dataclass
import sys


@dataclass(frozen=True, slots=True)
class ParsedHotkey:
    modifiers: tuple[str, ...]
    key: str

    @property
    def is_focus_sensitive(self) -> bool:
        return len(self.modifiers) <= 1


def parse_hotkey(value: str | None) -> ParsedHotkey | None:
    parts = [part.strip() for part in str(value or "").replace("-", "+").split("+") if part.strip()]
    if not parts:
        return None
    aliases = {
        "control": "ctrl",
        "ctrl_l": "ctrl",
        "ctrl_r": "ctrl",
        "alt_l": "alt",
        "alt_r": "alt",
        "alt_gr": "alt",
        "shift_l": "shift",
        "shift_r": "shift",
        "meta": "super",
        "win": "super",
        "cmd": "cmd",
        "command": "cmd",
    }
    modifiers: list[str] = []
    key = ""
    for raw in parts:
        lower = aliases.get(raw.lower(), raw.lower())
        if lower in {"ctrl", "alt", "shift", "super", "cmd"}:
            normalized = "cmd" if sys.platform == "darwin" and lower in {"super", "cmd"} else lower
            if normalized not in modifiers:
                modifiers.append(normalized)
            continue
        if key:
            return None
        if len(lower) == 1 and lower.isalnum():
            key = lower
        elif lower.startswith("f") and lower[1:].isdigit() and 1 <= int(lower[1:]) <= 24:
            key = lower
        else:
            return None
    if not key or not modifiers:
        return None
    return ParsedHotkey(tuple(modifiers), key)


def to_pynput(value: str | None, *, macos: bool = False) -> str | None:
    parsed = parse_hotkey(value)
    if parsed is None:
        return None
    names = []
    for modifier in parsed.modifiers:
        if modifier in {"super", "cmd"}:
            names.append("<cmd>" if macos else "<super>")
        else:
            names.append(f"<{modifier}>")
    names.append(parsed.key)
    return "+".join(names)


def to_win32(value: str | None) -> tuple[int, int] | None:
    parsed = parse_hotkey(value)
    if parsed is None:
        return None
    flags = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "super": 0x0008, "cmd": 0x0008}
    mod = 0
    for name in parsed.modifiers:
        mod |= flags[name]
    key = parsed.key
    if len(key) == 1:
        vk = ord(key.upper())
    else:
        vk = 0x6F + int(key[1:])
    return mod, vk
