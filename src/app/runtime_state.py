"""Thread-safe ownership for process-local application runtime state.

This module is deliberately free of Qt, platform backends, file-system access,
and application configuration.  It owns volatile state only; persistence and
business decisions remain in their respective services/repositories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, RLock
from time import monotonic
from typing import Any, Callable, Literal

PathKey = Callable[[str], str]
PathNormalizer = Callable[[str], str]
DynamicWallpaperKind = Literal["video", "html"]


@dataclass(frozen=True, slots=True)
class CancellationSnapshot:
    requested: bool
    reason: str


class CancellationState:
    """Own cooperative cancellation and the latest human-readable reason."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = RLock()
        self._reason = ""

    @property
    def event(self) -> Event:
        """Compatibility handle for code that must wait on the native Event."""
        return self._event

    def request(self, reason: str = "") -> bool:
        """Request cancellation and return ``True`` only for the first request."""
        with self._lock:
            was_requested = self._event.is_set()
            clean_reason = str(reason or "").strip()
            if clean_reason:
                self._reason = clean_reason
            self._event.set()
            return not was_requested

    def clear(self) -> None:
        with self._lock:
            self._reason = ""
            self._event.clear()

    def is_requested(self) -> bool:
        return self._event.is_set()

    def snapshot(self) -> CancellationSnapshot:
        with self._lock:
            return CancellationSnapshot(self._event.is_set(), self._reason)


@dataclass(frozen=True, slots=True)
class SlideshowSnapshot:
    enabled: bool
    images: tuple[str, ...]
    generation: int
    timer_attached: bool


class SlideshowState:
    """Own slideshow images, timer lifecycle, generation and lookup cache.

    ``generation`` invalidates callbacks from timers that were cancelled while
    already starting.  A stale callback therefore cannot apply another image or
    re-arm itself after a reset/stop operation.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._timer: Any = None
        self._enabled = False
        self._images: list[str] = []
        self._generation = 0
        self._index_cache: dict[str, tuple[int, str]] = {}

    @property
    def lock(self) -> RLock:
        return self._lock

    def snapshot(self) -> SlideshowSnapshot:
        with self._lock:
            return SlideshowSnapshot(
                enabled=self._enabled,
                images=tuple(self._images),
                generation=self._generation,
                timer_attached=self._timer is not None,
            )

    def replace_images(self, images: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Replace images with a defensive copy and invalidate all lookup data."""
        with self._lock:
            self._images = [str(item) for item in images if str(item)]
            self._index_cache.clear()
            return tuple(self._images)

    def invalidate_index(self) -> None:
        with self._lock:
            self._index_cache.clear()

    def index_map(
        self,
        keyer: PathKey,
        normalizer: PathNormalizer,
        images: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, tuple[int, str]]:
        """Return a safe path-key index for current or explicitly supplied images."""
        with self._lock:
            use_cache = images is None
            candidates = tuple(self._images if images is None else images)
            if use_cache and self._index_cache:
                return dict(self._index_cache)
            mapping: dict[str, tuple[int, str]] = {}
            for index, item in enumerate(candidates):
                key = keyer(item)
                if key and key not in mapping:
                    mapping[key] = (index, normalizer(item))
            if use_cache:
                self._index_cache = mapping
            return dict(mapping)

    def start(self, images: list[str] | tuple[str, ...]) -> tuple[Any, int, tuple[str, ...]]:
        """Enable a new slideshow generation and detach the previous timer."""
        with self._lock:
            previous_timer = self._timer
            self._timer = None
            self._images = [str(item) for item in images if str(item)]
            self._index_cache.clear()
            self._enabled = True
            self._generation += 1
            return previous_timer, self._generation, tuple(self._images)

    def stop(self) -> tuple[Any, bool]:
        """Disable slideshow, invalidate running callbacks and detach its timer."""
        with self._lock:
            previous_timer = self._timer
            was_running = self._enabled or previous_timer is not None
            self._timer = None
            self._enabled = False
            self._generation += 1
            return previous_timer, was_running

    def renew_timer_generation(self) -> tuple[Any, int] | None:
        """Invalidate the current timer and return the generation for a replacement."""
        with self._lock:
            if not self._enabled or not self._images:
                return None
            previous_timer = self._timer
            self._timer = None
            self._generation += 1
            return previous_timer, self._generation

    def attach_timer(self, timer: Any, generation: int) -> bool:
        """Attach a timer only when its generation is still current and enabled."""
        with self._lock:
            if not self._enabled or generation != self._generation:
                return False
            self._timer = timer
            return True

    def timer_fired(self, generation: int) -> tuple[str, ...] | None:
        """Consume the current timer and return images for a valid callback."""
        with self._lock:
            if not self._enabled or generation != self._generation:
                return None
            self._timer = None
            return tuple(self._images)

    def discard_timer(self, timer: Any, generation: int) -> None:
        """Detach *timer* after a failed start without touching newer timers."""
        with self._lock:
            if generation == self._generation and self._timer is timer:
                self._timer = None

    def is_active(self, generation: int) -> bool:
        with self._lock:
            return self._enabled and generation == self._generation


@dataclass(frozen=True, slots=True)
class IpcCommandSnapshot:
    worker_active: bool
    pending_command: str | None
    generation: int


class IpcCommandState:
    """Coalesce wallpaper IPC commands while one worker is active."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._worker_active = False
        self._pending_command: str | None = None
        self._generation = 0

    def submit(self, command: str) -> int | None:
        """Return a worker generation when a new worker must be started.

        While a worker is active only the latest command is retained.  The
        generation prevents a delayed old worker from clearing a newer worker.
        """
        clean_command = str(command or "").strip()
        if not clean_command:
            return None
        with self._lock:
            if self._worker_active:
                self._pending_command = clean_command
                return None
            self._worker_active = True
            self._generation += 1
            return self._generation

    def next_or_finish(self, generation: int) -> str | None:
        """Take the latest pending command or mark the matching worker idle."""
        with self._lock:
            if generation != self._generation or not self._worker_active:
                return None
            command = self._pending_command
            self._pending_command = None
            if command is None:
                self._worker_active = False
            return command

    def abort_worker(self, generation: int) -> None:
        """Reset only the matching worker after thread construction/start fails."""
        with self._lock:
            if generation != self._generation:
                return
            self._worker_active = False
            self._pending_command = None

    def snapshot(self) -> IpcCommandSnapshot:
        with self._lock:
            return IpcCommandSnapshot(
                self._worker_active,
                self._pending_command,
                self._generation,
            )


@dataclass(frozen=True, slots=True)
class DynamicWallpaperSnapshot:
    kind: DynamicWallpaperKind | None
    target: str
    started_at: float | None


class DynamicWallpaperState:
    """Record the dynamic wallpaper requested by this application process.

    Backends remain the source of truth for whether a child process is alive;
    this state records ownership and is reconciled after backend probes.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._kind: DynamicWallpaperKind | None = None
        self._target = ""
        self._started_at: float | None = None

    def mark_started(self, kind: DynamicWallpaperKind, target: str) -> None:
        with self._lock:
            self._kind = kind
            self._target = str(target or "")
            self._started_at = monotonic()

    def mark_stopped(self, kind: DynamicWallpaperKind | None = None) -> bool:
        with self._lock:
            if kind is not None and self._kind not in (None, kind):
                return False
            changed = self._kind is not None or bool(self._target)
            self._kind = None
            self._target = ""
            self._started_at = None
            return changed

    def reconcile(self, kind: DynamicWallpaperKind, running: bool, target: str = "") -> None:
        with self._lock:
            if running:
                if self._kind != kind:
                    self._kind = kind
                    self._target = str(target or "")
                    self._started_at = monotonic()
                elif target:
                    self._target = str(target)
            elif self._kind == kind:
                self._kind = None
                self._target = ""
                self._started_at = None

    def snapshot(self) -> DynamicWallpaperSnapshot:
        with self._lock:
            return DynamicWallpaperSnapshot(self._kind, self._target, self._started_at)


@dataclass(frozen=True, slots=True)
class SessionWallpaperSnapshot:
    wallpaper: str
    style: dict[str, Any]
    captured: bool


class SessionWallpaperState:
    """Own the in-memory startup-wallpaper restore anchor."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._wallpaper = ""
        self._style: dict[str, Any] = {}
        self._captured = False

    @property
    def lock(self) -> RLock:
        return self._lock

    def snapshot(self) -> SessionWallpaperSnapshot:
        with self._lock:
            return SessionWallpaperSnapshot(
                self._wallpaper,
                dict(self._style),
                self._captured,
            )

    def replace(self, wallpaper: str, style: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._wallpaper = str(wallpaper or "")
            self._style = dict(style or {})
            self._captured = bool(self._wallpaper)

    def clear(self) -> None:
        with self._lock:
            self._wallpaper = ""
            self._style = {}
            self._captured = False


class OperationClockState:
    """Measure elapsed time between successful wallpaper operations."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._last_ms: float | None = None

    def record(self, current_ms: float) -> float | None:
        with self._lock:
            previous = self._last_ms
            self._last_ms = float(current_ms)
            return None if previous is None else float(current_ms) - previous

    def reset(self) -> None:
        with self._lock:
            self._last_ms = None


@dataclass(slots=True)
class RuntimeState:
    """Aggregate all process-local state behind one explicit ownership boundary."""

    cancellation: CancellationState = field(default_factory=CancellationState)
    slideshow: SlideshowState = field(default_factory=SlideshowState)
    ipc_commands: IpcCommandState = field(default_factory=IpcCommandState)
    dynamic_wallpaper: DynamicWallpaperState = field(default_factory=DynamicWallpaperState)
    session_wallpaper: SessionWallpaperState = field(default_factory=SessionWallpaperState)
    operation_clock: OperationClockState = field(default_factory=OperationClockState)
    wallpaper_operation_lock: RLock = field(default_factory=RLock)
