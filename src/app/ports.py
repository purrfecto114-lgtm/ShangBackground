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

    def configure_fit_mode(self, mode: str) -> bool | None:
        """Apply the desktop fit/position mode.

        v1.6.0 审计 P0-2: Windows 后端返回 bool（True=COM 或注册表至少一条
        路径生效，False=两条路径都失败）；其余平台后端仍返回 None（无失败
        信号）。服务层只把显式 ``False`` 视为失败，``None`` 维持旧语义。
        """
        ...

    def set_wallpaper(self, path: str) -> None: ...


@runtime_checkable
class StatefulWallpaperBackend(Protocol):
    """Optional plugin/state-level capture+restore extension (schema=3).

    Backends that cannot capture plugin state simply do not implement this
    protocol; the session service then falls back to the path-only schema=2.

    Contract:
    - ``capture_state()`` returns ``None`` when no state-level capture is
      possible (non-KDE session, unreachable scripting channel, parse
      failure).  A returned mapping describes the platform's wallpaper
      plugin state verbatim; it must never fabricate restorable local
      files for remote or unknown sources.
    - ``restore_state(state)`` applies a previously captured mapping and
      returns a ``BackendResult``.  Backends without restore support must
      return ``BackendResult(False, ...)`` rather than raising.
    """

    def capture_state(self) -> Mapping[str, Any] | None: ...

    def restore_state(self, state: Mapping[str, Any]) -> BackendResult: ...


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
