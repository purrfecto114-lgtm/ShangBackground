"""Central resource/latency policy for the three wallpaper scheduling modes.

Keep these values outside the Qt window class so mode semantics are testable
without importing PySide6 and cannot silently drift between platform mixins.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    preview_startup_ms: int
    bing_startup_ms: int
    native_effect_startup_ms: int
    tray_startup_ms: int
    autostart_prompt_ms: int
    bing_task_startup_ms: int
    status_startup_ms: int
    preview_poll_ms: int
    followup_refresh_ms: tuple[int, ...]
    icon_decode_limit_mb: int
    icon_cache_items: int


_PROFILES = {
    "power_saver": PerformanceProfile(
        400, 1200, 700, 950, 2400, 2800, 1700,
        preview_poll_ms=4000, followup_refresh_ms=(800,),
        icon_decode_limit_mb=48, icon_cache_items=32,
    ),
    # Preserve the historical default behaviour to avoid a 1.5.0 regression.
    "balanced": PerformanceProfile(
        100, 320, 260, 420, 1500, 1250, 950,
        preview_poll_ms=1200, followup_refresh_ms=(300, 800),
        icon_decode_limit_mb=128, icon_cache_items=96,
    ),
    # "Responsive" must actually be more responsive than balanced. It may use
    # more memory/work because the user explicitly opted into this mode.
    "performance": PerformanceProfile(
        80, 250, 200, 320, 1200, 1000, 750,
        preview_poll_ms=800, followup_refresh_ms=(200, 650),
        icon_decode_limit_mb=192, icon_cache_items=128,
    ),
}


def performance_profile(level: str | None) -> PerformanceProfile:
    return _PROFILES.get(str(level or "").lower(), _PROFILES["balanced"])
