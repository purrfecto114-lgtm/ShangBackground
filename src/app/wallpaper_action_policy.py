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

# v1.6.3: engine operations whose service layer already commits the config
# transaction inside the operation itself. ``run_core`` used to append an
# unconditional ``core.save_config()`` after every worker call; for these
# operations it only re-serialized the whole config (the repository skips the
# byte-identical disk write) — the exact "duplicate disk write" the hotkey
# path already dropped in v1.6.1 (see ``_run_wallpaper_action``). Keeping the
# set explicit keeps operations that do NOT self-persist (slideshow timer
# start/stop, fit-mode-only changes before v1.6.3 wiring, ...) on the old
# behavior.
_SELF_PERSISTING_OPERATIONS = frozenset(
    {
        "previous_wallpaper",
        "next_wallpaper",
        "random_wallpaper",
        "set_wallpaper",
        "set_wallpaper_direct",
    }
)


def operation_persists_itself(operation_name: object) -> bool:
    """Whether the named engine operation commits its own config transaction.

    All entries end in ``WallpaperService.apply()``, which persists through
    ``remember_wallpaper``/``remember_current_without_reordering`` (both call
    the configured ``persist`` callback) before returning ``True``. On failure
    nothing needs persisting, so skipping the trailing save is safe in both
    directions.
    """
    return str(getattr(operation_name, "__name__", operation_name) or "") in _SELF_PERSISTING_OPERATIONS


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
