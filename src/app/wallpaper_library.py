"""Application boundary for wallpaper history and favorites.

The repositories own individual list semantics. ``WallpaperLibrary`` coordinates
changes that span a collection and adjacent configuration fields, such as
recording history together with ``current_wallpaper`` or resetting the
slideshow resume pointer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
import os
from threading import RLock
from typing import Any

from app.wallpaper_repositories import (
    CollectionPersistenceError,
    ConfigSource,
    FavoritesRepository,
    HistoryRepository,
    normalize_wallpaper_path,
)

PersistCallback = Callable[[], bool]


class WallpaperLibrary:
    """Coordinate config-backed wallpaper history and favorites."""

    def __init__(
        self,
        config: ConfigSource,
        *,
        persist: PersistCallback,
        lock: RLock,
    ) -> None:
        self._config_source = config
        self._persist = persist
        self._lock = lock
        self.history = HistoryRepository(config, persist=persist, lock=lock)
        self.favorites = FavoritesRepository(config, persist=persist, lock=lock)

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_source() if callable(self._config_source) else self._config_source
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    def list_history(
        self,
        *,
        limit: int | None = None,
        existing_only: bool = False,
    ) -> tuple[str, ...]:
        return self.history.items(keep_missing=not existing_only, limit=limit)

    def history_count(self) -> int:
        return self.history.count()

    def remember_wallpaper(
        self,
        path: str,
        *,
        update_current: bool = True,
        persist: bool = True,
        updates: Mapping[str, Any] | None = None,
    ) -> bool:
        """Record *path* and adjacent config fields in one transaction."""
        normalized = normalize_wallpaper_path(path)
        if not normalized or not os.path.isfile(normalized):
            return False
        with self._lock:
            config = self._config
            previous_history = config.get("history", [])
            previous_current = config.get("current_wallpaper", "")
            adjacent = dict(updates or {})
            previous_adjacent = {key: config.get(key) for key in adjacent}
            history_changed = self.history.add(normalized, persist=False)
            current_changed = bool(update_current and previous_current != normalized)
            if update_current:
                config["current_wallpaper"] = normalized
            adjacent_changed = False
            for key, value in adjacent.items():
                if config.get(key) != value:
                    config[key] = value
                    adjacent_changed = True
            changed = history_changed or current_changed or adjacent_changed
            if not persist or not changed:
                return changed
            try:
                saved = bool(self._persist())
            except Exception as exc:
                config["history"] = previous_history
                config["current_wallpaper"] = previous_current
                for key, value in previous_adjacent.items():
                    if value is None:
                        config.pop(key, None)
                    else:
                        config[key] = value
                raise CollectionPersistenceError("无法保存壁纸历史") from exc
            if not saved:
                config["history"] = previous_history
                config["current_wallpaper"] = previous_current
                for key, value in previous_adjacent.items():
                    if value is None:
                        config.pop(key, None)
                    else:
                        config[key] = value
                raise CollectionPersistenceError("无法保存壁纸历史")
            return True

    def remember_current_without_reordering(
        self,
        path: str,
        *,
        persist: bool = True,
        updates: Mapping[str, Any] | None = None,
    ) -> bool:
        """Update the current wallpaper while preserving MRU history order.

        History navigation must not move the selected older item back to the
        front of the recent list. Otherwise repeated ``previous`` actions bounce
        between the same two files. This transaction updates only
        ``current_wallpaper`` and adjacent resume metadata.
        """
        normalized = normalize_wallpaper_path(path)
        if not normalized or not os.path.isfile(normalized):
            return False
        with self._lock:
            config = self._config
            previous_current = config.get("current_wallpaper", "")
            adjacent = dict(updates or {})
            previous_adjacent = {key: config.get(key) for key in adjacent}
            changed = previous_current != normalized
            config["current_wallpaper"] = normalized
            for key, value in adjacent.items():
                if config.get(key) != value:
                    config[key] = value
                    changed = True
            if not persist or not changed:
                return changed
            try:
                saved = bool(self._persist())
            except Exception as exc:
                config["current_wallpaper"] = previous_current
                for key, value in previous_adjacent.items():
                    if value is None:
                        config.pop(key, None)
                    else:
                        config[key] = value
                raise CollectionPersistenceError("无法保存当前壁纸位置") from exc
            if not saved:
                config["current_wallpaper"] = previous_current
                for key, value in previous_adjacent.items():
                    if value is None:
                        config.pop(key, None)
                    else:
                        config[key] = value
                raise CollectionPersistenceError("无法保存当前壁纸位置")
            return True

    def clear_history(self, *, reset_slideshow_position: bool = False) -> bool:
        """Clear history and optionally reset slideshow resume state atomically."""
        with self._lock:
            config = self._config
            previous_history = config.get("history", [])
            previous_slideshow = config.get("slideshow_last_wallpaper", "")
            history_changed = self.history.clear(persist=False)
            slideshow_changed = bool(reset_slideshow_position and previous_slideshow)
            if reset_slideshow_position:
                config["slideshow_last_wallpaper"] = ""
            if not (history_changed or slideshow_changed):
                return False
            try:
                saved = bool(self._persist())
            except Exception as exc:
                config["history"] = previous_history
                config["slideshow_last_wallpaper"] = previous_slideshow
                raise CollectionPersistenceError("无法清空壁纸历史") from exc
            if not saved:
                config["history"] = previous_history
                config["slideshow_last_wallpaper"] = previous_slideshow
                raise CollectionPersistenceError("无法清空壁纸历史")
            return True

    def list_favorites(
        self,
        *,
        limit: int | None = None,
        existing_only: bool = False,
    ) -> tuple[str, ...]:
        return self.favorites.items(keep_missing=not existing_only, limit=limit)

    def is_favorite(self, path: str) -> bool:
        return self.favorites.contains(path)

    def add_favorite(self, path: str) -> bool:
        return self.favorites.add(path)

    def remove_favorite(self, path: str) -> bool:
        return self.favorites.remove(path)

    def toggle_favorite(self, path: str) -> bool:
        return self.favorites.toggle(path)

    def clear_favorites(self) -> bool:
        return self.favorites.clear()
