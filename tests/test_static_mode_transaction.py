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
