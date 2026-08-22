"""Runtime configuration validation shared by storage and UI boundaries.

The application historically accepted arbitrary JSON values and let individual
widgets coerce them.  This module establishes one Qt-free boundary so corrupt,
old, or externally edited settings cannot crash the GUI or poison later saves.
Unknown keys are preserved for forward compatibility.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.config import normalize_mode_key, normalize_style_key
from app.config_defaults import build_default_config

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_TEXT_KEYS = {
    "slide_folder",
    "video_file",
    "html_file",
    "single_image",
    "current_wallpaper",
    "slideshow_last_wallpaper",
    "bing_cache_dir",
    "log_file_path",
    "ignored_version",
    "ignored_dependency_warning_signature",
    "font_path",
    "hotkey_previous",
    "hotkey_next",
    "hotkey_random",
    "hotkey_jump",
}
_BOOL_KEYS = {
    "video_muted",
    "html_auto_pause",
    "shuffle",
    "wallpaper_transition_enabled",
    "auto_start",
    "auto_start_prompt_shown",
    "ctx_last_wallpaper",
    "ctx_next_wallpaper",
    "ctx_random_wallpaper",
    "ctx_jump_to_wallpaper",
    "hotkey_focus_guard",
    "global_hotkeys_enabled",
    "app_shortcuts_enabled",
    "run_in_background",
    "tray_icon",
    "tray_notify",
    "dark_mode",
    "enable_animations",
    "performance_mode",
    "silent_update_check_on_startup",
    "bing_auto_cleanup",
    "bing_auto_update_on_start",
    "bing_auto_delete_on_start",
    "log_enabled",
}
_INT_BOUNDS = {
    "slide_seconds": (5, 86400),
    "transition_duration_ms": (0, 2000),
    "wallpaper_transition_policy_version": (1, 1),
    "video_volume": (0, 100),
    "video_focus_duck_volume": (0, 100),
    "gradient_angle": (0, 360),
    "bing_sync_count": (1, 16),
    "bing_next_index": (0, 1000000),
    "bing_auto_update_count": (1, 16),
    "bing_auto_delete_count": (1, 200),
    "font_size": (0, 48),
}
_ENUMS = {
    "transition_effect": frozenset({"none", "system"}),
    "transition_direction": frozenset({"right"}),
    "video_focus_behavior": frozenset({"none", "pause", "duck"}),
    "tray_click_action": frozenset({"none", "show", "previous", "next", "random", "bing", "jump", "about", "exit"}),
    "performance_level": frozenset({"power_saver", "balanced", "performance"}),
    "app_theme": frozenset({"default"}),
    "font_weight": frozenset({"normal", "medium", "bold"}),
    "language": frozenset({"zh", "en"}),
}
_COLOR_KEYS = {"solid_color", "gradient_color2", "theme_color"}
_TRAY_ACTIONS = frozenset({"show", "previous", "next", "random", "bing", "jump", "settings", "about", "exit"})
_APP_SHORTCUT_KEYS = ("previous", "next", "random", "bing", "settings", "exit", "hide_to_tray")


def _text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, converted))



def _html_frame_rate(value: Any, default: int = 30) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return converted if converted in {0, 15, 24, 30, 45, 60} else default


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if converted != converted or converted in {float("inf"), float("-inf")}:
        return default
    return max(minimum, min(maximum, converted))


def _enum(value: Any, default: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _tray_items(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        items = list(default)
    else:
        items = []
        for item in value:
            # Tolerate the pre-1.4 representation at the boundary as well as in
            # the dedicated migration path.
            if isinstance(item, Mapping):
                if not _bool(item.get("enabled", True), True):
                    continue
                item = item.get("action")
            if isinstance(item, str):
                action = item.strip().lower()
                if action in _TRAY_ACTIONS and action not in items:
                    items.append(action)
    for required in ("show", "exit"):
        if required not in items:
            items.append(required)
    return items


def _shortcuts(value: Any, default: Mapping[str, Any]) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, str] = {}
    for key in _APP_SHORTCUT_KEYS:
        fallback = str(default.get(key, ""))
        candidate = source.get(key, fallback)
        result[key] = candidate.strip() if isinstance(candidate, str) else fallback
    return result


def migrate_wallpaper_transition_policy(target: dict[str, Any]) -> bool:
    """Upgrade the old placeholder transition value without overriding real choices.

    P2-14/P2-15 wrote ``wallpaper_transition_enabled=False`` even though the
    Windows backend still used its native transition. The version marker lets
    us restore that historical behavior once and then preserve later user
    choices, including an intentional ``False``.
    """
    try:
        version = int(target.get("wallpaper_transition_policy_version", 0))
    except (TypeError, ValueError, OverflowError):
        version = 0
    if version >= 1:
        return False
    target["wallpaper_transition_enabled"] = True
    target["transition_effect"] = "system"
    target["transition_duration_ms"] = 300
    target["wallpaper_transition_policy_version"] = 1
    return True


def normalize_runtime_config(
    raw: Mapping[str, Any] | None,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return a safe runtime configuration and whether normalization changed it.

    Values not owned by the current schema are copied unchanged.  This lets a
    newer build add settings without an older build silently deleting them.
    """
    base = dict(defaults or build_default_config())
    source = dict(raw) if isinstance(raw, Mapping) else {}
    normalized = dict(source)
    normalized.pop("html_compatibility_mode", None)

    # Every known key exists after normalization, but unknown keys survive.
    for key, fallback in base.items():
        normalized.setdefault(key, fallback.copy() if isinstance(fallback, dict) else list(fallback) if isinstance(fallback, list) else fallback)

    for key in _TEXT_KEYS:
        normalized[key] = _text(normalized.get(key), str(base.get(key, "")))
    for key in _BOOL_KEYS:
        normalized[key] = _bool(normalized.get(key), bool(base.get(key, False)))
    for key, (minimum, maximum) in _INT_BOUNDS.items():
        normalized[key] = _bounded_int(normalized.get(key), int(base.get(key, minimum)), minimum, maximum)
    for key, allowed in _ENUMS.items():
        normalized[key] = _enum(normalized.get(key), str(base.get(key, "")), allowed)
    for key in _COLOR_KEYS:
        fallback = str(base.get(key, "#ffffff"))
        value = normalized.get(key)
        normalized[key] = value.lower() if isinstance(value, str) and _COLOR_RE.fullmatch(value.strip()) else fallback.lower()

    normalized["mode"] = normalize_mode_key(normalized.get("mode"), str(base["mode"]))
    normalized["fit_mode"] = normalize_style_key(normalized.get("fit_mode"), str(base["fit_mode"]))
    normalized["html_frame_rate"] = _html_frame_rate(normalized.get("html_frame_rate"), int(base.get("html_frame_rate", 30)))
    normalized["dpi_scale"] = _bounded_float(normalized.get("dpi_scale"), float(base["dpi_scale"]), 0.75, 2.0)
    tray_items = normalized.get("tray_menu_items")
    if _bool(normalized.get("ctx_global_settings"), False):
        if isinstance(tray_items, list):
            tray_items = [*tray_items, "settings"]
        else:
            tray_items = [*base["tray_menu_items"], "settings"]
    normalized["tray_menu_items"] = _tray_items(tray_items, list(base["tray_menu_items"]))
    normalized["app_shortcuts"] = _shortcuts(normalized.get("app_shortcuts"), base["app_shortcuts"])
    normalized["recent_folders"] = _string_list(normalized.get("recent_folders"))

    # These collections are normalized by their repositories in core.engine;
    # here we only ensure hostile scalar/object values cannot reach them.
    for key in ("history", "favorites"):
        if not isinstance(normalized.get(key), list):
            normalized[key] = []

    # Compatibility boolean is derived from the authoritative three-state key.
    normalized["performance_mode"] = normalized["performance_level"] == "performance"

    return normalized, normalized != source


def normalize_runtime_config_in_place(
    target: dict[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> bool:
    """Normalize ``target`` without replacing references held by services/UI."""
    normalized, changed = normalize_runtime_config(target, defaults=defaults)
    if changed:
        target.clear()
        target.update(normalized)
    return changed
