"""Repositories for config-backed wallpaper path collections.

History and favorites currently live inside ``settings.json`` for backward
compatibility.  These repositories own collection normalization and mutation so
UI code no longer edits the shared configuration dictionary directly.

The boundary is intentionally free of Qt and platform-adapter imports.  It can
therefore be tested in isolation and reused by future application services.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
import os
from threading import RLock
from typing import Any

PersistCallback = Callable[[], bool]
ConfigProvider = Callable[[], MutableMapping[str, Any]]
ConfigSource = MutableMapping[str, Any] | ConfigProvider


class CollectionPersistenceError(RuntimeError):
    """Raised when a collection mutation could not be durably persisted."""


def normalize_wallpaper_path(path: object) -> str:
    """Return the canonical stored representation for a wallpaper path.

    Local paths are expanded and made absolute without resolving symlinks.
    HTTP(S) values are preserved because older settings may contain remote
    references, although current UI lists only display existing local files.
    """
    if path is None:
        return ""
    try:
        if isinstance(path, (str, bytes, os.PathLike)):
            value = os.fspath(path)
        else:
            value = str(path)
    except (OSError, TypeError, ValueError):
        value = str(path)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    value = str(value).strip()
    if not value:
        return ""
    if value.casefold().startswith(("http://", "https://")):
        return value
    try:
        return os.path.abspath(os.path.expanduser(value))
    except (OSError, RuntimeError, ValueError):
        return value


def wallpaper_path_key(path: object) -> str:
    """Return a stable identity key for path membership and de-duplication."""
    normalized = normalize_wallpaper_path(path)
    if not normalized:
        return ""
    if normalized.casefold().startswith(("http://", "https://")):
        return normalized
    try:
        return os.path.normcase(os.path.normpath(normalized))
    except (OSError, RuntimeError, ValueError):
        return normalized.casefold()


def normalize_wallpaper_paths(
    values: object,
    *,
    keep_missing: bool = True,
    limit: int | None = None,
) -> list[str]:
    """Normalize, de-duplicate and optionally filter a stored path sequence."""
    if (
        isinstance(values, (str, bytes, os.PathLike, Mapping))
        or not isinstance(values, Iterable)
    ):
        source: Iterable[object] = ()
    else:
        source = values

    normalized_values: list[str] = []
    seen: set[str] = set()
    maximum = None if limit is None else max(0, int(limit))
    if maximum == 0:
        return normalized_values

    for item in source:
        normalized = normalize_wallpaper_path(item)
        if not normalized:
            continue
        if not keep_missing and not os.path.isfile(normalized):
            continue
        identity = wallpaper_path_key(normalized)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        normalized_values.append(normalized)
        if maximum is not None and len(normalized_values) >= maximum:
            break
    return normalized_values


class _ConfigPathCollectionRepository:
    """Thread-safe mutation boundary for one list stored in a config mapping."""

    config_key = ""
    max_items = 0

    def __init__(
        self,
        config: ConfigSource,
        *,
        persist: PersistCallback | None = None,
        lock: RLock | None = None,
    ) -> None:
        if not self.config_key or self.max_items <= 0:
            raise TypeError("repository subclass must define config_key and max_items")
        self._config_source = config
        self._persist = persist
        self._lock = lock or RLock()

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_source() if callable(self._config_source) else self._config_source
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    @classmethod
    def normalize_items(
        cls,
        values: object,
        *,
        keep_missing: bool = True,
        limit: int | None = None,
    ) -> list[str]:
        maximum = cls.max_items if limit is None else min(cls.max_items, max(0, int(limit)))
        return normalize_wallpaper_paths(values, keep_missing=keep_missing, limit=maximum)

    @classmethod
    def prepend_item(cls, path: object, values: object) -> list[str]:
        """Return a normalized MRU list with *path* moved to the front."""
        normalized = normalize_wallpaper_path(path)
        identity = wallpaper_path_key(normalized)
        current = cls.normalize_items(values)
        if not normalized or not identity:
            return current
        return cls.normalize_items(
            [normalized, *[item for item in current if wallpaper_path_key(item) != identity]]
        )

    def _items_from_config(
        self,
        config: MutableMapping[str, Any],
        *,
        keep_missing: bool = True,
        limit: int | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            self.normalize_items(
                config.get(self.config_key, []),
                keep_missing=keep_missing,
                limit=limit,
            )
        )

    def items(
        self,
        *,
        keep_missing: bool = True,
        limit: int | None = None,
    ) -> tuple[str, ...]:
        with self._lock:
            config = self._config
            return self._items_from_config(
                config,
                keep_missing=keep_missing,
                limit=limit,
            )

    def count(self) -> int:
        return len(self.items())

    def contains(self, path: object) -> bool:
        identity = wallpaper_path_key(path)
        if not identity:
            return False
        with self._lock:
            config = self._config
            return any(
                wallpaper_path_key(item) == identity
                for item in self._items_from_config(config)
            )

    def _replace_in_config(
        self,
        config: MutableMapping[str, Any],
        normalized: list[str],
        *,
        persist: bool,
    ) -> bool:
        previous = config.get(self.config_key, [])
        if isinstance(previous, list) and previous == normalized:
            return False
        config[self.config_key] = normalized
        try:
            self._persist_if_requested(persist)
        except Exception:
            config[self.config_key] = previous
            raise
        return True

    def replace(
        self,
        values: object,
        *,
        persist: bool = True,
    ) -> bool:
        normalized = self.normalize_items(values)
        with self._lock:
            config = self._config
            return self._replace_in_config(config, normalized, persist=persist)

    def add(self, path: object, *, persist: bool = True) -> bool:
        normalized = normalize_wallpaper_path(path)
        identity = wallpaper_path_key(normalized)
        if not normalized or not identity:
            return False
        with self._lock:
            config = self._config
            current = self._items_from_config(config)
            replacement = self.prepend_item(normalized, current)
            return self._replace_in_config(config, replacement, persist=persist)

    def remove(self, path: object, *, persist: bool = True) -> bool:
        identity = wallpaper_path_key(path)
        if not identity:
            return False
        with self._lock:
            config = self._config
            current = list(self._items_from_config(config))
            remaining = [item for item in current if wallpaper_path_key(item) != identity]
            if remaining == current:
                return False
            return self._replace_in_config(config, remaining, persist=persist)

    def clear(self, *, persist: bool = True) -> bool:
        return self.replace([], persist=persist)

    def _persist_if_requested(self, persist: bool) -> None:
        if not persist or self._persist is None:
            return
        try:
            succeeded = bool(self._persist())
        except Exception as exc:
            raise CollectionPersistenceError(
                f"无法保存 {self.config_key} 集合"
            ) from exc
        if not succeeded:
            raise CollectionPersistenceError(f"无法保存 {self.config_key} 集合")


class HistoryRepository(_ConfigPathCollectionRepository):
    """Most-recent-first wallpaper history stored in the application config.

    Twenty entries are enough for deterministic Previous/Next recovery while
    keeping shell-menu navigation bounded when a user has accumulated a long
    history over many sessions.
    """

    config_key = "history"
    max_items = 20


class FavoritesRepository(_ConfigPathCollectionRepository):
    """User-pinned wallpapers stored in the application config."""

    config_key = "favorites"
    max_items = 200

    def toggle(self, path: object, *, persist: bool = True) -> bool:
        """Toggle *path* atomically and return its new favorite state."""
        normalized = normalize_wallpaper_path(path)
        identity = wallpaper_path_key(normalized)
        if not normalized or not identity:
            return False
        with self._lock:
            config = self._config
            current = list(self._items_from_config(config))
            present = any(wallpaper_path_key(item) == identity for item in current)
            if present:
                self._replace_in_config(
                    config,
                    [item for item in current if wallpaper_path_key(item) != identity],
                    persist=persist,
                )
                return False
            self._replace_in_config(config, [normalized, *current], persist=persist)
            return True


def previous_history_item(items: Sequence[str], current: object) -> str | None:
    """Return the next older item from a newest-first history snapshot."""
    current_key = wallpaper_path_key(current)
    if current_key:
        for index, item in enumerate(items):
            if wallpaper_path_key(item) == current_key:
                return items[index + 1] if index + 1 < len(items) else None
    return items[0] if items else None


def newer_history_item(items: Sequence[str], current: object) -> str | None:
    """Return the next newer item from a newest-first history snapshot.

    ``items[0]`` is the latest wallpaper.  This helper lets the Next action
    walk back toward that latest item after one or more Previous actions
    without reordering the MRU collection.
    """
    current_key = wallpaper_path_key(current)
    if not current_key:
        return None
    for index, item in enumerate(items):
        if wallpaper_path_key(item) == current_key:
            return items[index - 1] if index > 0 else None
    # The operating-system wallpaper may have been changed outside the main
    # process (for example by the Explorer context-menu helper).  In that case
    # rejoin the bounded history at its newest item instead of skipping directly
    # into the slideshow sequence.
    return items[0] if items else None
