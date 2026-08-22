from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from types import SimpleNamespace
from threading import RLock
from typing import cast

from app.source_validation import SourceValidation
from app.config import VIDEO_EXTENSIONS
from app.source_validation import validate_existing_file
from app.wallpaper_mode_service import WallpaperModeError, WallpaperModeService
from ui.main_window import _SharedShangBackgroundWindow


@dataclass
class _Edit:
    value: str = ""

    def text(self) -> str:
        return self.value

    def setText(self, value: str) -> None:
        self.value = str(value)


class _Binding:
    key = "video_file"

    def __init__(self, edit: _Edit, result: SourceValidation | None = None):
        self.edit = edit
        self.result = result

    def validate(self, *, required: bool = False, show_dialog: bool = False) -> SourceValidation:
        if self.result is not None:
            return self.result
        return validate_existing_file(self.edit.text(), optional=not required, suffixes=VIDEO_EXTENSIONS)


def _window(monkeypatch, *, mode="图片", video_file="old.mp4"):
    edit = _Edit(video_file)
    window = SimpleNamespace(
        video_edit=edit,
        _video_source=_Binding(edit),
        _refresh_video_volume_controls=lambda: None,
        _core_busy=False,
        set_status=lambda _text: None,
    )
    window._submit_video_selection = MethodType(
        _SharedShangBackgroundWindow._submit_video_selection,
        window,
    )
    monkeypatch.setattr("ui.main_window.core.config", {"mode": mode, "video_file": video_file})
    return window, edit


def _choose(window) -> None:
    _SharedShangBackgroundWindow.choose_video_file(cast(_SharedShangBackgroundWindow, window))


def _finish(window, ok: bool, message: str = "failed") -> None:
    _SharedShangBackgroundWindow._on_core_finished(
        cast(_SharedShangBackgroundWindow, window), ok, message, None
    )


def test_choose_video_from_image_mode_submits_video_transaction(monkeypatch, tmp_path):
    video_file = tmp_path / "candidate.mp4"
    video_file.write_bytes(b"video")
    window, edit = _window(monkeypatch)
    calls = []
    monkeypatch.setattr("ui.main_window.core.switch_wallpaper_mode", lambda *_args, **_kwargs: True)
    window._select_video_path = lambda: str(video_file)
    window._run_mode_transition = lambda label, worker: calls.append((label, worker))

    _choose(window)

    assert len(calls) == 1
    assert edit.text() == str(video_file)
    assert calls[0][1]() is True


def test_cancelled_video_selection_preserves_mode_and_source(monkeypatch):
    window, edit = _window(monkeypatch)
    calls = []
    window._select_video_path = lambda: ""
    window._run_mode_transition = lambda *args: calls.append(args)

    _choose(window)

    assert calls == []
    assert edit.text() == "old.mp4"


def test_illegal_video_selection_is_rejected_without_transaction(monkeypatch, tmp_path):
    image_file = tmp_path / "not-video.txt"
    image_file.write_text("not a video", encoding="utf-8")
    window, edit = _window(monkeypatch)
    calls = []
    window._select_video_path = lambda: str(image_file)
    window._run_mode_transition = lambda *args: calls.append(args)

    _choose(window)

    assert calls == []
    assert edit.text() == "old.mp4"


def test_failed_transition_restores_previous_mode_and_video_file(monkeypatch, tmp_path):
    video_file = tmp_path / "candidate.mp4"
    video_file.write_bytes(b"video")
    window, edit = _window(monkeypatch)
    config = {"mode": "图片", "video_file": "old.mp4"}
    monkeypatch.setattr("ui.main_window.core.config", config)
    window._select_video_path = lambda: str(video_file)
    window._run_mode_transition = lambda _label, worker: worker()
    monkeypatch.setattr("ui.main_window.core.switch_wallpaper_mode", lambda *_args, **_kwargs: False)

    _choose(window)
    window._mode_transition_pending = True
    window._schedule_preview_refresh = lambda: None
    window._sync_mode_ui_from_config = lambda: None
    window._is_qobject_alive = lambda obj: obj is not None
    window.tray = None
    window.finish_operation = lambda _message: None
    window._show_non_modal_warning = lambda *_args: None
    window._pending_core_actions = []
    _finish(window, False)

    assert config == {"mode": "图片", "video_file": "old.mp4"}
    assert edit.text() == "old.mp4"


def test_video_player_start_failure_restores_transaction_snapshot(tmp_path):
    candidate = tmp_path / "candidate.mp4"
    candidate.write_bytes(b"video")
    config = {"mode": "图片", "video_file": "old.mp4"}
    activations = []

    def activate(mode, cfg):
        activations.append((mode, cfg.get("video_file")))
        return mode != "视频"

    service = WallpaperModeService(
        config=lambda: config,
        persist=lambda: True,
        operation_lock=RLock(),
        mode_order=("图片", "视频", "HTML"),
        normalize_mode=lambda value: str(value),
        activate=activate,
    )

    try:
        service.switch("视频", updates={"video_file": str(candidate)})
    except WallpaperModeError:
        pass
    else:
        raise AssertionError("failed video activation must raise")

    assert config == {"mode": "图片", "video_file": "old.mp4"}
    assert activations == [("视频", str(candidate)), ("图片", "old.mp4")]


def test_video_controls_remain_browsable_outside_video_mode():
    source = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    assert "self.video_browse_btn.setEnabled(video_feature_enabled)" in source
    assert "self.video_edit.setEnabled(video_feature_enabled)" in source
