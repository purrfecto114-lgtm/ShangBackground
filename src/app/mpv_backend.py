"""Stable mpv lifecycle/control abstraction.

Platform modules in ``platform_adapters.backends.*.video`` intentionally keep
legacy function entry points for compatibility with existing plugins and CLI
helpers.  ``LegacyModuleMpvBackend`` adapts those functions to one explicit
contract so application services do not need to know whether playback uses
ctypes/libmpv, an external mpv process, mpvpaper, or a native platform player.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any

from app.ports import BackendResult

PropertyObserver = Callable[[str, Any], None]


class MpvBackend(ABC):
    """Unified lifecycle and control surface for video-wallpaper players."""

    @abstractmethod
    def start(
        self,
        target: str,
        *,
        muted: bool = True,
        volume: int = 100,
    ) -> BackendResult:
        """Start playback and return a normalized result."""

    @abstractmethod
    def pause(self, paused: bool) -> bool:
        """Pause or resume the active player without restarting it."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the active video wallpaper."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return whether the backend owns a live player."""

    @abstractmethod
    def set_property(self, name: str, value: Any) -> bool:
        """Set a normalized runtime property such as ``volume`` or ``pause``."""

    @abstractmethod
    def observe_property(self, name: str, callback: PropertyObserver) -> bool:
        """Subscribe to a player property when supported by the backend."""

    @abstractmethod
    def ipc(self, command: Sequence[Any] | Mapping[str, Any]) -> bool:
        """Send a raw backend command when a compatible IPC channel is exposed."""

    @abstractmethod
    def last_target(self) -> str:
        """Return the most recently started video target, if known."""


class LegacyModuleMpvBackend(MpvBackend):
    """Adapt the project's existing module-level video API to :class:`MpvBackend`.

    Unsupported optional operations deliberately return ``False`` instead of
    raising.  This keeps Windows WorkerW, Linux mpvpaper/xwinwrap, and macOS
    AVPlayer paths behavior-compatible while giving callers one capability
    boundary.
    """

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    @property
    def module(self) -> ModuleType:
        return self._module

    def start(
        self,
        target: str,
        *,
        muted: bool = True,
        volume: int = 100,
    ) -> BackendResult:
        raw = self._module.start_video_wallpaper(
            target,
            muted=bool(muted),
            volume=max(0, min(100, int(volume))),
        )
        return _backend_result(raw)

    def pause(self, paused: bool) -> bool:
        setter = getattr(self._module, "set_video_paused", None)
        return bool(setter and setter(bool(paused)))

    def stop(self) -> None:
        self._module.stop_video_wallpaper()

    def is_running(self) -> bool:
        return bool(self._module.is_video_wallpaper_running())

    def set_property(self, name: str, value: Any) -> bool:
        normalized = str(name or "").strip().lower().replace("-", "_")
        if normalized in {"pause", "paused"}:
            return self.pause(bool(value))
        if normalized in {"volume", "audio"}:
            setter = getattr(self._module, "set_video_volume", None)
            if setter is None:
                return False
            if isinstance(value, (tuple, list)) and len(value) == 2:
                muted, volume = value
            elif isinstance(value, Mapping):
                muted = bool(value.get("muted", False))
                volume = value.get("volume", 100)
            else:
                muted = False
                volume = value
            return bool(setter(bool(muted), max(0, min(100, int(volume)))))
        generic = getattr(self._module, "set_video_property", None)
        return bool(generic and generic(normalized, value))

    def observe_property(self, name: str, callback: PropertyObserver) -> bool:
        observer = getattr(self._module, "observe_video_property", None)
        return bool(observer and observer(str(name), callback))

    def ipc(self, command: Sequence[Any] | Mapping[str, Any]) -> bool:
        sender = getattr(self._module, "send_video_ipc", None)
        return bool(sender and sender(command))

    def last_target(self) -> str:
        getter = getattr(self._module, "get_last_path", None)
        return str(getter() or "") if getter else ""


def _backend_result(raw: Any) -> BackendResult:
    if isinstance(raw, BackendResult):
        return raw
    if isinstance(raw, tuple):
        ok = bool(raw[0]) if raw else False
        message = str(raw[1]) if len(raw) > 1 else ""
        return BackendResult(ok, message)
    return BackendResult(bool(raw), "")
