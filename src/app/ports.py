"""Application ports for wallpaper, dynamic media and global hotkeys.

The ports intentionally describe only the operations used by the application
services.  Platform adapters may expose many more helpers, but those helpers do
not belong in these stable service contracts.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

MediaKind = Literal["video", "html"]


@dataclass(frozen=True, slots=True)
class BackendResult:
    """Normalized result returned by platform start/restart operations."""

    ok: bool
    message: str = ""


@runtime_checkable
class WallpaperBackend(Protocol):
    """Minimal platform contract needed to apply a static wallpaper."""

    def get_current(self) -> str: ...

    def configure_fit_mode(self, mode: str) -> None: ...

    def set_wallpaper(self, path: str) -> None: ...


@runtime_checkable
class MediaBackend(Protocol):
    """Generic lifecycle contract for video and HTML wallpaper backends."""

    def validate(self, kind: MediaKind, target: str) -> bool: ...

    def start(
        self,
        kind: MediaKind,
        target: str,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> BackendResult: ...

    def stop(self, kind: MediaKind) -> None: ...

    def is_running(self, kind: MediaKind) -> bool: ...

    def set_option(self, kind: MediaKind, key: str, value: Any) -> bool: ...

    def last_target(self, kind: MediaKind) -> str: ...

    def restart(
        self,
        kind: MediaKind,
        target: str,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> BackendResult: ...


@runtime_checkable
class HotkeyBackend(Protocol):
    """Minimal global-hotkey lifecycle contract."""

    def refresh(
        self,
        bindings: Mapping[str, str],
        dispatch: Callable[[str], None],
    ) -> bool: ...

    def stop(self) -> None: ...
