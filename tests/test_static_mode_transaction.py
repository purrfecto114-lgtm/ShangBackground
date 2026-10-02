from __future__ import annotations

from pathlib import Path
from threading import RLock

import pytest

from app.wallpaper_repositories import CollectionPersistenceError
from app.wallpaper_service import WallpaperService


class _Backend:
    def __init__(self, current: str = "") -> None:
        self.current = current
        self.calls: list[tuple[str, str]] = []

    def get_current(self) -> str:
        self.calls.append(("get", self.current))
        return self.current

    def configure_fit_mode(self, mode: str) -> None:
        self.calls.append(("fit", mode))

    def set_wallpaper(self, path: str) -> None:
        self.calls.append(("set", path))
        self.current = path


class _Library:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.items: list[str] = []

    def remember_wallpaper(self, path: str, **_kwargs) -> bool:
        if self.fail:
            raise CollectionPersistenceError("simulated persistence failure")
        self.items.append(path)
        return True

    def remember_current_without_reordering(self, path: str, **_kwargs) -> bool:
        return self.remember_wallpaper(path)


@pytest.mark.parametrize("dynamic_was_running", [False, True])
def test_static_mode_works_with_or_without_prior_dynamic_mode(
    tmp_path: Path,
    dynamic_was_running: bool,
):
    image = tmp_path / "new.jpg"
    image.write_bytes(b"new")
    backend = _Backend()
    library = _Library()
    events: list[str] = []
    config = {"fit_mode": "填充"}

    service = WallpaperService(
        backend=backend,
        config=lambda: config,
        library=library,
        operation_lock=RLock(),
        stop_dynamic=lambda: events.append("stop-dynamic") or dynamic_was_running,
        normalize_fit_mode=lambda value: value,
    )

    assert service.apply(str(image), "test") is True
    assert events == ["stop-dynamic"]
    assert [name for name, _value in backend.calls] == ["get", "fit", "set"]
    assert library.items == [str(image)]


def test_static_mode_rolls_back_system_wallpaper_when_persistence_fails(tmp_path: Path):
    previous = tmp_path / "previous.jpg"
    target = tmp_path / "target.jpg"
    previous.write_bytes(b"previous")
    target.write_bytes(b"target")
    backend = _Backend(str(previous))
    errors: list[str] = []

    service = WallpaperService(
        backend=backend,
        config=lambda: {"fit_mode": "适应"},
        library=_Library(fail=True),
        operation_lock=RLock(),
        stop_dynamic=lambda: False,
        set_error=errors.append,
        normalize_fit_mode=lambda value: value,
    )

    assert service.apply(str(target), "test") is False
    assert backend.current == str(previous)
    assert [value for name, value in backend.calls if name == "set"] == [str(target), str(previous)]
    assert errors and "保存壁纸历史失败" in errors[-1]


def test_apply_primes_current_wallpaper_cache(tmp_path: Path):
    """v1.6.3: a successful apply must record the applied path as the known
    current wallpaper instead of invalidating the cache.

    History navigation (上一张/下一张) anchors on ``get_current()``; with the
    old invalidate-on-apply behavior every click forced a fresh system query
    (Windows: new COM apartment + IDesktopWallpaper round-trip), which is the
    per-switch latency the sidebar browse path never paid.
    """
    image = tmp_path / "applied.jpg"
    image.write_bytes(b"applied")
    backend = _Backend("")
    service = WallpaperService(
        backend=backend,
        config=lambda: {"fit_mode": "填充"},
        library=_Library(),
        operation_lock=RLock(),
        stop_dynamic=lambda: False,
        normalize_fit_mode=lambda value: value,
    )

    assert service.apply(str(image), "test") is True
    gets_before = [call for call in backend.calls if call[0] == "get"]

    # The cached read must return the applied path without touching the backend.
    assert service.get_current() == str(image)
    assert [call for call in backend.calls if call[0] == "get"] == gets_before

    # Explicit invalidation still clears the primed value.
    service.invalidate_current_cache()
    assert service.get_current() == str(image)  # repopulated from backend.current
    assert [call for call in backend.calls if call[0] == "get"] == gets_before + [("get", str(image))]


def test_rollback_primes_cache_with_restored_wallpaper(tmp_path: Path):
    """v1.6.3: after a successful rollback the restored wallpaper is the
    authoritative current state; the cache must reflect it without a new
    backend query."""
    previous = tmp_path / "previous.jpg"
    target = tmp_path / "target.jpg"
    previous.write_bytes(b"previous")
    target.write_bytes(b"target")
    backend = _Backend(str(previous))

    service = WallpaperService(
        backend=backend,
        config=lambda: {"fit_mode": "填充"},
        library=_Library(fail=True),
        operation_lock=RLock(),
        stop_dynamic=lambda: False,
        normalize_fit_mode=lambda value: value,
    )

    assert service.apply(str(target), "test", previous_path=str(previous)) is False
    gets_before = [call for call in backend.calls if call[0] == "get"]
    assert service.get_current() == str(previous)
    assert [call for call in backend.calls if call[0] == "get"] == gets_before


class _FlakyRollbackBackend(_Backend):
    """Backend whose set_wallpaper raises once armed (rollback failure)."""

    def __init__(self, current: str = "") -> None:
        super().__init__(current)
        self.fail_rollback = False

    def set_wallpaper(self, path: str) -> None:
        if self.fail_rollback:
            self.calls.append(("set-failed", path))
            raise OSError("simulated rollback failure")
        super().set_wallpaper(path)


class _ArmThenFailLibrary(_Library):
    """Succeeds on the first record, then arms the backend's rollback failure
    and raises CollectionPersistenceError on the second."""

    def __init__(self, backend: _FlakyRollbackBackend) -> None:
        super().__init__(fail=False)
        self._backend = backend
        self._recorded = 0

    def remember_wallpaper(self, path, **_kwargs) -> bool:
        self._recorded += 1
        if self._recorded > 1:
            self._backend.fail_rollback = True
            raise CollectionPersistenceError("simulated persistence failure")
        return super().remember_wallpaper(path)


def test_failed_rollback_invalidates_primed_cache(tmp_path: Path):
    """v1.6.3: when the rollback itself fails the system wallpaper keeps the
    target that failed to persist, so the primed cache must be invalidated —
    otherwise a stale value (here: the previously applied wallpaper) keeps
    answering cached reads for the rest of the TTL and history navigation
    anchors on the wrong position.
    """
    previous = tmp_path / "previous.jpg"
    target = tmp_path / "target.jpg"
    previous.write_bytes(b"previous")
    target.write_bytes(b"target")
    backend = _FlakyRollbackBackend("")
    errors: list[str] = []

    service = WallpaperService(
        backend=backend,
        config=lambda: {"fit_mode": "填充"},
        library=_ArmThenFailLibrary(backend),
        operation_lock=RLock(),
        stop_dynamic=lambda: False,
        set_error=errors.append,
        normalize_fit_mode=lambda value: value,
    )

    # First apply succeeds and primes the cache with *previous*.
    assert service.apply(str(previous), "test") is True
    assert service.get_current() == str(previous)

    # Second apply persists nothing and the rollback restore fails: the system
    # wallpaper stays on *target* while the cache still holds *previous*.
    assert service.apply(str(target), "test", previous_path=str(previous)) is False
    assert [call for call in backend.calls if call[0] == "set-failed"] == [("set-failed", str(previous))]
    assert errors and "保存壁纸历史失败" in errors[-1] and "恢复原壁纸失败" in errors[-1]

    # The failed rollback must have invalidated the cache: the next read
    # re-queries the backend and returns the real system state (target),
    # not the stale primed value (previous).
    gets_before = [call for call in backend.calls if call[0] == "get"]
    assert service.get_current() == str(target)
    assert [call for call in backend.calls if call[0] == "get"] == gets_before + [("get", str(target))]


def test_note_current_wallpaper_replaces_cached_value(tmp_path: Path):
    """v1.6.3: an externally observed wallpaper (WM_SETTINGCHANGE answer)
    must replace the primed cache value without a backend query, and an
    empty observation must clear the cache so the next read re-queries."""
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    backend = _Backend("")
    service = WallpaperService(
        backend=backend,
        config=lambda: {"fit_mode": "填充"},
        library=_Library(),
        operation_lock=RLock(),
        stop_dynamic=lambda: False,
        normalize_fit_mode=lambda value: value,
    )

    assert service.apply(str(first), "test") is True
    gets_after_apply = [call for call in backend.calls if call[0] == "get"]

    # External change observed: the authoritative value replaces the cache.
    service.note_current_wallpaper(str(second))
    assert service.get_current() == str(second)
    assert [call for call in backend.calls if call[0] == "get"] == gets_after_apply

    # Empty observation clears the cache; the next read re-queries the
    # backend (whose state still reflects the last backend set — the
    # observation API records knowledge, it does not drive the backend).
    service.note_current_wallpaper("")
    assert service.get_current() == str(first)
    assert [call for call in backend.calls if call[0] == "get"] == gets_after_apply + [("get", str(first))]
