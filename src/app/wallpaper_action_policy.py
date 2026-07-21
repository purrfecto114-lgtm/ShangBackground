"""Qt-free mode-aware availability rules for wallpaper playback actions."""
from __future__ import annotations

from dataclasses import dataclass

from app.config import normalize_mode_key


@dataclass(frozen=True, slots=True)
class WallpaperActionAvailability:
    action: str
    mode: str
    allowed: bool
    reason: str = ""


_SLIDESHOW_ONLY = frozenset({"previous", "next", "random"})
_HTML_ONLY = frozenset({"refresh_html"})


def wallpaper_action_availability(
    mode: str | None,
    action: str | None,
) -> WallpaperActionAvailability:
    canonical_mode = normalize_mode_key(str(mode or ""))
    canonical_action = str(action or "").strip().lower()
    if canonical_action in _SLIDESHOW_ONLY:
        allowed = canonical_mode == "幻灯片放映"
        return WallpaperActionAvailability(
            canonical_action,
            canonical_mode,
            allowed,
            "requires_slideshow" if not allowed else "",
        )
    if canonical_action in _HTML_ONLY:
        allowed = canonical_mode == "HTML"
        return WallpaperActionAvailability(
            canonical_action,
            canonical_mode,
            allowed,
            "requires_html" if not allowed else "",
        )
    return WallpaperActionAvailability(canonical_action, canonical_mode, True)
